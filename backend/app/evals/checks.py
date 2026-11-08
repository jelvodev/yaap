"""Running an eval case's checks against a workspace.

The workspace is reached through the target abstraction the agent's tools use: its files through TargetFiles (which
keeps every path inside the folder) and its commands through a CommandExecutor, so an eval run can point both at a
throwaway workspace in a runner or sandbox, and a test at a temporary folder. A rubric goes to a RubricScorer; the
budget is compared with the usage the run reports. Every check gives a result, also when it cannot be judged.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from app.agent.command_tools import command_environment
from app.evals.models import (
    Budget,
    CaseResult,
    CaseUsage,
    Check,
    CheckResult,
    CheckStatus,
    CommandCheck,
    EvalCase,
    FileCheck,
    RubricCheck,
    case_status,
)
from app.evals.rubric import RubricRequest, RubricScorer, ScorerError
from app.targets.base import CommandExecutor, TargetError, TargetFiles
from app.targets.container import LocalExecutor, LocalFiles

OUTPUT_TAIL = 4000
MAX_RUBRIC_FILE_CHARS = 20_000


@dataclass(frozen=True)
class CheckContext:
    """What the checks of one play of a case run against."""

    files: TargetFiles
    executor: CommandExecutor
    # The agent's final reply, which a rubric judges with the files it names.
    reply: str = ""
    usage: CaseUsage | None = None
    scorer: RubricScorer | None = None
    # The commands' environment; None is the one the agent's commands get (safe_subprocess_env, non-interactive).
    environment: Mapping[str, str] | None = None


def local_context(
    workspace: Path, reply: str = "", usage: CaseUsage | None = None, scorer: RubricScorer | None = None
) -> CheckContext:
    """Checks against a folder on this machine, with commands in YAAP's own process."""
    return CheckContext(LocalFiles(str(workspace)), LocalExecutor(str(workspace)), reply, usage, scorer)


async def evaluate_case(case: EvalCase, context: CheckContext) -> CaseResult:
    """Every check of the case in order, then the budget when it has one."""
    results = [await run_check(case, check, context) for check in case.checks]
    if case.budget is not None:
        results.append(budget_result(case.budget, context.usage))
    checked = tuple(results)
    return CaseResult(case.name, case_status(checked), checked)


async def run_check(case: EvalCase, check: Check, context: CheckContext) -> CheckResult:
    if isinstance(check, CommandCheck):
        return await command_result(check, context)
    if isinstance(check, FileCheck):
        return await file_result(check, context.files)
    return await rubric_result(case, check, context)


async def command_result(check: CommandCheck, context: CheckContext) -> CheckResult:
    environment = dict(context.environment) if context.environment is not None else command_environment()
    try:
        result = await context.executor.run(check.run, environment, check.timeout_seconds)
    except (TargetError, OSError) as error:
        return CheckResult("command", check.name, "error", f"The command could not be started: {error}")
    tail = output_tail(result.output)
    if result.timed_out:
        return CheckResult("command", check.name, "failed", f"Stopped after {check.timeout_seconds:.0f} s.\n{tail}")
    status: CheckStatus = "passed" if result.exit_code == 0 else "failed"
    return CheckResult("command", check.name, status, f"Exit code {result.exit_code}.\n{tail}".rstrip())


def output_tail(output: str) -> str:
    output = output.rstrip()
    return output if len(output) <= OUTPUT_TAIL else "[…]\n" + output[-OUTPUT_TAIL:]


async def read_text(files: TargetFiles, path: str) -> str | None:
    """A workspace file's text with \\n line ends, so `$` in a pattern means the same in CRLF files; None when it
    does not exist (or is a folder)."""
    entry = await files.stat(path)
    if entry is None or entry.is_dir:
        return None
    return (await files.read_bytes(path)).decode("utf-8", errors="replace").replace("\r\n", "\n")


async def file_result(check: FileCheck, files: TargetFiles) -> CheckResult:
    try:
        text = await read_text(files, check.path)
    except (TargetError, OSError) as error:
        return CheckResult("file", check.name, "error", f"{check.path} cannot be read: {error}")
    if not check.exists:
        if text is None:
            return CheckResult("file", check.name, "passed", f"{check.path} does not exist, as expected.")
        return CheckResult("file", check.name, "failed", f"{check.path} exists but should not.")
    if text is None:
        return CheckResult("file", check.name, "failed", f"{check.path} does not exist.")
    failures = content_failures(check, text)
    if failures:
        return CheckResult("file", check.name, "failed", f"{check.path}: {'; '.join(failures)}.")
    return CheckResult("file", check.name, "passed", f"{check.path} matches every condition.")


def content_failures(check: FileCheck, text: str) -> list[str]:
    """Which of the file check's conditions the text breaks."""
    failures = [f"does not contain {needle!r}" for needle in check.contains if needle not in text]
    failures += [f"contains {needle!r}" for needle in check.not_contains if needle in text]
    failures += [f"has no match for /{p}/" for p in check.matches if not re.search(p, text, re.MULTILINE)]
    for pattern in check.not_matches:
        found = re.search(pattern, text, re.MULTILINE)
        if found is not None:
            line = text.count("\n", 0, found.start()) + 1
            failures.append(f"matches /{pattern}/ on line {line}")
    return failures


async def rubric_result(case: EvalCase, check: RubricCheck, context: CheckContext) -> CheckResult:
    if context.scorer is None:
        return CheckResult("rubric", check.name, "error", "No scorer is available for rubric checks.")
    try:
        files = tuple([(path, await rubric_file(context.files, path)) for path in check.files])
        verdict = await context.scorer.score(RubricRequest(check.rubric, case.prompt, context.reply, files))
    except (TargetError, OSError) as error:
        return CheckResult("rubric", check.name, "error", f"A file of the rubric cannot be read: {error}")
    except ScorerError as error:
        return CheckResult("rubric", check.name, "error", str(error))
    status: CheckStatus = "passed" if verdict.passed else "failed"
    return CheckResult("rubric", check.name, status, verdict.reason, verdict.cost)


async def rubric_file(files: TargetFiles, path: str) -> str:
    text = await read_text(files, path)
    if text is None:
        return "(this file does not exist)"
    if len(text) > MAX_RUBRIC_FILE_CHARS:
        return text[:MAX_RUBRIC_FILE_CHARS] + f"\n[Shortened: the file has {len(text)} characters.]"
    return text


def budget_result(budget: Budget, usage: CaseUsage | None) -> CheckResult:
    if usage is None:
        return CheckResult("budget", "budget", "error", "No usage was reported to compare with the budget.")
    overruns = budget_overruns(budget, usage)
    if overruns:
        return CheckResult("budget", "budget", "failed", f"Over budget: {'; '.join(overruns)}.")
    return CheckResult("budget", "budget", "passed", "Within the budget.")


def budget_overruns(budget: Budget, usage: CaseUsage) -> list[str]:
    """Each limit the usage went over, with both numbers in the budget's unit."""
    overruns: list[str] = []
    if budget.steps is not None and usage.steps > budget.steps:
        overruns.append(f"{usage.steps} steps of {budget.steps} steps")
    if budget.tokens is not None and usage.tokens > budget.tokens:
        overruns.append(f"{usage.tokens:,} tokens of {budget.tokens:,} tokens")
    if budget.cost_usd is not None and usage.cost_usd > budget.cost_usd:
        overruns.append(f"{usage.cost_usd:.4f} USD of {budget.cost_usd:.4f} USD")
    if budget.seconds is not None and usage.seconds > budget.seconds:
        overruns.append(f"{usage.seconds:.0f} s of {budget.seconds:.0f} s")
    return overruns
