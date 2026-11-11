"""The gate every tool call goes through, over MCP and in YAAP's own runner alike.

`call_tool` records the call as a step of the run (app.tracing) around `gate_call`, which finds the tool, stops endless
repeats, runs it through `gated_call` and prepares its output for the model: masked secrets, long output stored, the
user's steering messages added, the outgoing patterns checked. `gated_call` gathers the facts, lets
app.agent.permissions decide, shows the approval card when it asks, takes a checkpoint before a change and runs the
tool.
"""

import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from typing import Any

import anyio

from app.agent.attachment_tools import find_attachment
from app.agent.document_tools import deliver_document_changes, with_document
from app.agent.guard import excerpts, note_outside
from app.agent.outputs import STORE_ABOVE, WHOLE_TOOLS, shortened
from app.agent.permissions import PermissionDecision, Place, RunFacts, ToolFacts, decide
from app.agent.runs import AgentRun, ApprovalAnswer, RunRegistry
from app.agent.tool_context import in_runner, in_sandbox, on_target, target_label
from app.agent.toolkit import Tool, ToolError, ToolOutput, truncate, with_text
from app.agent.tools import find_tool
from app.checkpoints.service import CHANGING_TOOLS, call_key
from app.core.outgoing_checks import OutgoingRules, text_refusal
from app.git.service import GitError
from app.learning.signals import step_signal
from app.secrets.service import mask
from app.targets.base import TargetError
from app.targets.container import CONTAINER
from app.tracing.recorder import ApprovalOutcome, ToolStep, argument_summary

STEERING_NOTE = "[Message from the user while you were working]"
WARN_REPEATS = 3
REFUSE_REPEATS = 5
# Tools whose repeated identical calls are normal: reading the growing output of a process, waiting.
REPEATABLE_TOOLS = frozenset({"read_process_output", "list_processes", "git_status"})


@dataclass
class CallNote:
    """What the gate learns about a call along the way, for its step in the trace."""

    summary: str = ""
    approval: ApprovalOutcome | None = None
    output_ref: int | None = None
    # Why the user declined the call, when they said so.
    declined_reason: str | None = None


async def call_tool(
    registry: RunRegistry, run: AgentRun, name: str, arguments: dict[str, Any]
) -> tuple[ToolOutput, bool]:
    """Run a tool through the permission gate and record it as a step of the run. Returns the output, with any
    images, and whether it is an error."""
    trace, run_id = run.trace, run.state.record_id
    # The position is taken before the call runs, so parallel calls keep the order the model gave them.
    position = trace.reserve(run_id) if trace is not None and run_id is not None else None
    started_at, started = datetime.now(UTC), time.monotonic()
    note = CallNote(summary=name)
    output, is_error = await gate_call(registry, run, name, arguments, note)
    if trace is not None and run_id is not None and position is not None:
        duration = int((time.monotonic() - started) * 1000)
        signal = step_signal(run, name, arguments, str(output), is_error, note.approval, note.declined_reason)
        step = ToolStep(
            position,
            started_at,
            duration,
            name,
            note.summary,
            is_error,
            note.approval,
            note.output_ref,
            signal.kind if signal else None,
            signal.note if signal else None,
        )
        await trace.record(run_id, step)
    return output, is_error


async def gate_call(
    registry: RunRegistry, run: AgentRun, name: str, arguments: dict[str, Any], note: CallNote
) -> tuple[ToolOutput, bool]:
    tool = find_tool(run, name)
    if tool is None:
        return ToolOutput(f"Unknown tool '{name}'."), True
    note.summary = argument_summary(tool.describe(arguments) if tool.describe else None, arguments, run.secrets)
    repeats = count_repeats(run, name, arguments)
    if repeats >= REFUSE_REPEATS:
        if run.goal is not None:
            pause = f"Paused: the agent called {name} {repeats} times in a row with the same arguments."
            await anyio.to_thread.run_sync(run.goal.pause, pause)
        return (
            ToolOutput(
                f"Refused: this is call {repeats} of {name} with the same arguments in a row. Repeating it will not "
                "change the result. Stop, explain to the user what you tried and what blocks you, and ask how to go on."
            ),
            True,
        )
    output, is_error = await gated_call(registry, run, tool, name, arguments, note)
    if repeats >= WARN_REPEATS:
        warning = (
            f"\n\n[Note from YAAP: you called {name} with the same arguments {repeats} times in a row; the result will "
            "not change. Try something else or ask the user.]"
        )
        output = with_text(output, str(output) + warning)
    if run.secrets:
        output = with_text(output, mask(str(output), run.secrets))
    output = await deliver_document_changes(run, name, arguments, keep_long_output(run, name, output, note))
    output = deliver_steering(run, name, arguments, output)
    # The output goes back to the model, so it leaves YAAP too: a listed pattern blocks it (secrets are masked above).
    blocked = text_refusal(str(output), OutgoingRules(patterns=run.model_policy.patterns))
    if blocked is not None:
        if run.state.steering_item_ids and run.services.steering is not None and run.state.record_id is not None:
            run.services.steering.restore(run.state.record_id)
            run.state.steering_item_ids = []
        return ToolOutput(f"The output of {name} was not passed on. {blocked}"), True
    if run.state.steering_item_ids and run.services.steering is not None and run.state.record_id is not None:
        run.services.steering.acknowledge(run.state.record_id, run.state.steering_item_ids)
        run.state.steering_item_ids = []
    return ToolOutput.of(output), is_error


def deliver_steering(run: AgentRun, name: str, arguments: dict[str, Any], output: str | ToolOutput) -> str | ToolOutput:
    """Messages the user sent while the run worked go to the model with this tool result."""
    if run.parent_token is not None:
        return output
    delivered = (
        run.services.steering.claim(run.conversation_id, run.state.record_id)
        if run.state.record_id is not None and run.services.steering is not None
        else []
    )
    texts = [text for _, text in delivered]
    run.state.steering_item_ids = [item_id for item_id, _ in delivered]
    if run.state.steering:
        texts.extend(run.state.steering)
        run.state.steering = []
    if not texts:
        return output
    run.events.put_nowait({"event": "steering", "call_key": call_key(name, arguments), "text": "\n\n".join(texts)})
    note = "".join(f"\n\n{STEERING_NOTE}\n{text}" for text in texts)
    return with_text(output, str(output) + note)


def keep_long_output(
    run: AgentRun, name: str, output: str | ToolOutput, note: CallNote | None = None
) -> str | ToolOutput:
    """A long output is stored whole; the model gets its start and end and reads the rest with read_output."""
    text = str(output)
    if name in WHOLE_TOOLS or len(text) <= STORE_ABOVE:
        return output
    if run.outputs is None or "read_output" not in run.tool_names:
        short = truncate(text)
    else:
        number = run.outputs.save(text)
        if note is not None:
            note.output_ref = number
        short = shortened(text, number)
    return with_text(output, short)


def count_repeats(run: AgentRun, name: str, arguments: dict[str, Any]) -> int:
    """How many times in a row this exact call has now come, the current one included."""
    if name in REPEATABLE_TOOLS:
        run.state.last_call, run.state.repeats = None, 0
        return 1
    key = call_key(name, arguments)
    run.state.repeats = run.state.repeats + 1 if key == run.state.last_call else 1
    run.state.last_call = key
    return run.state.repeats


def tool_facts(run: AgentRun, tool: Tool, name: str, arguments: dict[str, Any]) -> ToolFacts:
    """What the permission decision needs to know about this call."""
    return ToolFacts(
        name=name,
        permission=tool.permission,
        description=tool.describe(arguments) if tool.describe else name,
        read_only=tool.read_only,
        asks_itself=tool.asks_itself,
        takes_secrets=tool.takes_secrets,
        conditional_asks=(
            tool.permission == "conditional" and tool.needs_approval is not None and tool.needs_approval(run, arguments)
        ),
    )


def run_facts(run: AgentRun) -> RunFacts:
    """What the permission decision needs to know about the run, as it is at this call."""
    place: Place = "sandbox" if in_sandbox(run) else "computer" if on_target(run) else "container"
    return RunFacts(
        policy=run.policy,
        unattended=run.unattended,
        place=place,
        place_label=target_label(run) if place == "computer" else "",
        outside_sources=tuple(source.label for source in run.state.outside_sources),
        known_secrets=frozenset(run.secrets),
        model_policy=run.model_policy,
    )


async def approved(
    registry: RunRegistry, run: AgentRun, name: str, arguments: dict[str, Any], decision: PermissionDecision
) -> ApprovalAnswer:
    """Show the approval card (with the optional injection check's warning) and wait for the user's answer."""
    detail = truncate(json.dumps(arguments, indent=2, ensure_ascii=False), 2000)
    suspicion = None
    if decision.outside_content is not None and run.injection_check is not None:
        suspicion = await run.injection_check(decision.summary, detail, excerpts(run))
    return await registry.request_approval(run, name, decision.summary, detail, decision.outside_content, suspicion)


async def take_checkpoint(run: AgentRun, tool: Tool, name: str, arguments: dict[str, Any]) -> None:
    """The project folder as it is before this action. Only where the project folder is YAAP's own (the container,
    a sandbox, the runner): a computer over SSH would need its own snapshots."""
    if run.checkpoints is None or run.project_id is None or run.project_folder is None:
        return
    if run.target not in (None, CONTAINER) and not in_sandbox(run) and not in_runner(run):
        return
    summary = tool.describe(arguments) if tool.describe else f"{name}: {arguments.get('path') or ''}".rstrip(": ")
    key = call_key(name, arguments)
    checkpoint_id = await anyio.to_thread.run_sync(
        partial(
            run.checkpoints.take,
            run.project_id,
            run.project_folder,
            name,
            summary,
            conversation_id=run.conversation_id,
            run_record_id=run.state.record_id,
            key=key,
        )
    )
    if checkpoint_id is not None:
        run.events.put_nowait({"event": "checkpoint", "checkpoint_id": checkpoint_id, "call_key": key, "tool": name})


def attachment_label(run: AgentRun, arguments: dict[str, Any]) -> str:
    value = str(arguments.get("attachment") or "")
    if not value and len(run.attachments) != 1:
        return "the attachments"
    try:
        return f"attachment {find_attachment(run, value).name}"
    except ToolError:
        return f"attachment {value}"


async def gated_call(
    registry: RunRegistry,
    run: AgentRun,
    tool: Tool,
    name: str,
    arguments: dict[str, Any],
    note: CallNote | None = None,
) -> tuple[str | ToolOutput, bool]:
    """Decide on the call (app.agent.permissions), ask the user when it says so, then run the tool."""
    note = note if note is not None else CallNote()
    decision = decide(tool_facts(run, tool, name, arguments), arguments, run_facts(run))
    if decision.verdict == "refuse":
        # A call that would have asked, in a run without the user, is counted apart: an eval case reports it.
        note.approval = "asked" if decision.reason == "unattended" else "refused"
        return decision.message, True
    if decision.verdict == "ask":
        answer = await approved(registry, run, name, arguments, decision)
        note.approval = "approved" if answer.decision == "approve" else "declined"
        if note.approval == "declined":
            note.declined_reason = answer.reason
            because = f" Their reason: {answer.reason}" if answer.reason else ""
            return f"The user declined this action.{because} Do not retry it; ask the user what to do instead.", True
    if name in CHANGING_TOOLS:
        await take_checkpoint(run, tool, name, arguments)
    try:
        output = await with_document(run, name, arguments, tool.handler)
        if name in ("read_attachment", "search_attachment"):
            note_outside(run, attachment_label(run, arguments), str(output))
        return output, False
    except (ToolError, GitError, TargetError) as error:
        return str(error), True
    except (OSError, ValueError, TypeError) as error:
        return f"{name} failed: {error}", True
