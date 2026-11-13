"""Learning from the work: signals on steps, proposals from a conversation, and the check before
accepting (a branch per Blueprint proposal, measured with the project's eval cases when it has any)."""

import asyncio
import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from app.agent.gate import call_tool
from app.agent.runs import RunConfig, RunRegistry, RunServices
from app.agent.tools import tools_for
from app.git.service import commit_all, run_git
from app.learning import service as learning_service
from app.learning.after_reply import review_due
from app.learning.proposals import ReviewInput, StepSignal, parse_drafts
from app.learning.signals import failed_test_command, known_match, prompt_file, step_signal
from app.providers.fake_provider import ScriptedResponse
from app.storage.models import AgentRunRecord, Conversation, Environment, Goal, LearningProposal, RunStep
from app.tracing.recorder import TraceRecorder
from tests.test_delegation import project_conversation
from tests.test_eval_runs import WRITE_HELLO, write_case

REASON = "Never push to main; open a pull request instead."
TEST_COMMAND = "pytest -q tests/test_cart.py"


# --- Signals --------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "command", "output", "is_error", "expected"),
    [
        ("run_command", TEST_COMMAND, "Exit code 1\nFAILED", False, TEST_COMMAND),
        ("run_command", "pytest  -q   tests/test_cart.py", "Exit code 2", False, TEST_COMMAND),
        ("run_command", TEST_COMMAND, "Exit code 0\n3 passed", False, None),
        ("run_command", TEST_COMMAND, "Exit code 0", True, TEST_COMMAND),
        ("run_command", "npm test", "Stopped after 120 seconds.", False, "npm test"),
        ("run_command", "ls -la", "Exit code 1", False, None),
        ("start_process", TEST_COMMAND, "Exit code 1", False, None),
    ],
)
def test_a_failed_test_command_is_recognised(name, command, output, is_error, expected):
    assert failed_test_command(name, {"command": command}, output, is_error) == expected


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        (".blueprint/instructions.md", True),
        (".blueprint/memory.md", True),
        ("./.blueprint/standards/python.md", True),
        (".blueprint/engineering/api.md", True),
        (".blueprint/skills/deploy/SKILL.md", False),
        (".blueprint/evals/greet.md", False),
        ("src/instructions.md", False),
    ],
)
def test_reading_a_file_the_prompt_holds_is_recognised(path, expected):
    assert prompt_file(path) is expected


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("package manager pnpm", "rule"),
        ("which package manager do we use", "rule"),
        ("pnpm", None),
        ("database migrations alembic", None),
        ("prices cents", "memory"),
    ],
)
def test_a_search_for_what_the_blueprint_holds_is_recognised(query, expected):
    known = [("rule", "Use pnpm as the package manager, never npm."), ("memory", "Prices are stored in cents.")]
    assert known_match(query, known) == expected


def test_parse_drafts_keeps_valid_items_at_their_level():
    signal = StepSignal(5, "declined", "git_push", "push main", REASON)
    found = ReviewInput(1, 1, 1, [signal], "", [], "", ["deploy"], "English")
    answer = json.dumps(
        [
            {
                "kind": "rule",
                "level": "environment",
                "text": "Open a pull request instead of pushing to main.",
                "signals": [1, 7],
            },
            {"kind": "memory", "level": "global", "section": "Odd", "text": "The cart tests need a running Redis."},
            {"kind": "skill_step", "skill": "deploy", "text": "Run the migrations before restarting the service."},
            {"kind": "skill_step", "skill": "missing", "text": "A step for a skill that does not exist."},
            {"kind": "rule", "level": "galaxy", "text": "A level that does not exist at all."},
        ]
    )
    drafts = parse_drafts(f"Proposals:\n{answer}", found)
    assert [(draft.kind, draft.level, draft.target) for draft in drafts] == [
        ("rule", "environment", ""),
        ("memory", "project", "Notes"),
        ("skill_step", "project", "skills/deploy/SKILL.md"),
    ]
    assert [signal.step_id for signal in drafts[0].signals] == [5]


# --- Fixtures -------------------------------------------------------------------------------------------------------


def project_folder(client, project: dict) -> Path:
    folder = Path(client.app.state.workspace_dir) / project["folder"]
    (folder / "README.md").write_text("A fixture project.\n", encoding="utf-8")
    commit_all(folder, "Start")
    return folder


def commits(folder: Path) -> int:
    return int(run_git(["rev-list", "--count", "HEAD"], folder).stdout.strip())


def branches(folder: Path) -> list[str]:
    return run_git(["branch", "--format=%(refname:short)"], folder).stdout.split()


def signalled_conversation(client) -> tuple[dict, int, Path]:
    """A project conversation whose run declined a call (with a reason) and failed the same test twice."""
    project, conversation_id = project_conversation(client)
    folder = project_folder(client, project)
    now = datetime.now(UTC)
    with client.app.state.session_factory() as session:
        run = AgentRunRecord(environment_id=1, conversation_id=conversation_id, project_id=project["id"], model="m")
        session.add(run)
        session.flush()
        steps = [
            ("git_push", "Push main to origin", "declined", "declined", REASON),
            ("run_command", TEST_COMMAND, None, None, None),
            ("run_command", TEST_COMMAND, None, "test_failed_twice", TEST_COMMAND),
        ]
        for position, (tool, summary, approval, signal, note) in enumerate(steps, start=1):
            session.add(
                RunStep(
                    run_id=run.id, position=position, kind="tool", started_at=now + timedelta(seconds=position),
                    tool=tool, summary=summary, approval=approval, signal=signal, signal_note=note,
                )
            )
        session.commit()
    return project, conversation_id, folder


def review(client, conversation_id: int) -> list[dict]:
    response = client.post(f"/api/v1/conversations/{conversation_id}/learning-review")
    assert response.status_code == 200, response.text
    return response.json()


def no_utility_model(monkeypatch) -> None:
    async def unavailable(*_args, **_kwargs) -> None:
        return None

    monkeypatch.setattr(learning_service, "run_utility", unavailable)


# --- Recording ------------------------------------------------------------------------------------------------------


def test_the_approval_reason_is_stored_on_the_step(client, tmp_path):
    _, conversation_id = project_conversation(client)
    folder = tmp_path / "project"
    folder.mkdir()
    factory = client.app.state.session_factory
    with factory() as session:
        record = AgentRunRecord(environment_id=1, conversation_id=conversation_id, model="m")
        session.add(record)
        session.commit()
        record_id = record.id
    registry = RunRegistry()
    config = RunConfig(
        conversation_id=conversation_id,
        environment_id=1,
        project_id=1,
        project_folder=folder,
        blueprint_layers=[],
        tool_names=tools_for(has_project=True, has_attachments=False),
    )
    run = registry.start(config, RunServices(trace=TraceRecorder(factory)))
    run.state.record_id = record_id

    async def scenario():
        async def deny():
            event = (await run.events.get()).model_dump()
            registry.decide(str(event["approval_id"]), "deny", f"  {REASON}  ")

        task = asyncio.create_task(deny())
        result = await call_tool(registry, run, "run_command", {"command": "git push origin main"})
        await task
        return result

    output, is_error = asyncio.run(scenario())
    assert is_error and REASON in str(output)
    with factory() as session:
        step = session.scalars(select(RunStep).where(RunStep.run_id == record_id)).one()
    assert (step.approval, step.signal, step.signal_note) == ("declined", "declined", REASON)
    assert run.state.approval_log[-1]["answer"] == REASON
    # The approvals API takes the reason too.
    response = client.post("/api/v1/approvals/gone", json={"decision": "deny", "reason": REASON})
    assert response.status_code == 404


def test_the_second_failure_of_a_test_command_is_a_signal(tmp_path):
    config = RunConfig(
        conversation_id=1,
        environment_id=1,
        project_id=1,
        project_folder=tmp_path,
        blueprint_layers=[],
        tool_names=tools_for(has_project=True, has_attachments=False),
    )
    run = RunRegistry().start(config)

    first = step_signal(run, "run_command", {"command": TEST_COMMAND}, "Exit code 1", False, None, None)
    second = step_signal(run, "run_command", {"command": TEST_COMMAND}, "Exit code 1", False, None, None)
    third = step_signal(run, "run_command", {"command": TEST_COMMAND}, "Exit code 1", False, None, None)
    assert first is None and third is None
    assert second is not None and (second.kind, second.note) == ("test_failed_twice", TEST_COMMAND)


# --- Proposals ------------------------------------------------------------------------------------------------------


def test_the_fallback_proposes_a_project_rule_and_a_memory_line(client, monkeypatch):
    project, conversation_id, folder = signalled_conversation(client)
    no_utility_model(monkeypatch)

    proposals = review(client, conversation_id)

    rule, memory = sorted(proposals, key=lambda item: item["kind"] != "rule")
    assert (rule["kind"], rule["level"], rule["source"]) == ("rule", "project", "fallback")
    assert REASON in rule["text"] and rule["target"] == "standards/rules-project.md"
    assert rule["signals"][0]["signal"] == "declined" and rule["signals"][0]["note"] == REASON
    assert rule["branch"] == f"yaap-learning/{rule['id']}" and rule["branch"] in branches(folder)
    assert REASON in rule["diff"] and rule["diff"].startswith("--- a/standards/rules-project.md")
    assert (memory["kind"], memory["level"], memory["target"]) == ("memory", "project", "Lessons learned")
    assert TEST_COMMAND in memory["text"] and memory["measurement"]["status"] == "not_needed"
    # A project without eval cases: shown, not held back.
    assert rule["measurement"]["status"] == "not_measured" and "no eval cases" in rule["measurement"]["note"]
    with client.app.state.session_factory() as session:
        assert session.get_one(Conversation, conversation_id).learning_checked_at is not None
    # The layer's own branch and files are untouched while the proposal waits.
    assert run_git(["branch", "--show-current"], folder).stdout.strip() == "main"
    assert not (folder / ".blueprint" / "standards" / "rules-project.md").exists()
    # The same review again proposes nothing new.
    assert review(client, conversation_id) == []


def test_the_utility_model_proposes_at_the_level_it_names(client, fake):
    project, conversation_id, _folder = signalled_conversation(client)
    answer = [
        {
            "kind": "rule",
            "level": "environment",
            "text": "Never push to main; open a pull request.",
            "rationale": "The user declined a push to main.",
            "signals": [1],
        },
        {"kind": "memory", "section": "Lessons learned", "text": "The cart tests need Redis running.", "signals": [2]},
    ]
    fake.responses.append(ScriptedResponse(text=json.dumps(answer)))

    proposals = review(client, conversation_id)

    assert [(item["kind"], item["level"], item["source"]) for item in proposals] == [
        ("rule", "environment", "utility"),
        ("memory", "project", "utility"),
    ]
    assert proposals[0]["target"] == "standards/rules-environment.md"
    prompt = "\n".join(block.get("text", "") for message in fake.requests[-1].messages for block in message.content)
    assert REASON in prompt and TEST_COMMAND in prompt
    with client.app.state.session_factory() as session:
        slug = session.get_one(Environment, 1).slug
    environment_repo = Path(client.app.state.blueprint_dir) / slug
    assert proposals[0]["branch"] in branches(environment_repo)
    assert not (environment_repo / "standards" / "rules-environment.md").exists()


# --- Accepting and declining ----------------------------------------------------------------------------------------


def test_accepting_merges_one_commit_into_the_layer_and_removes_the_branch(client, monkeypatch):
    project, conversation_id, folder = signalled_conversation(client)
    no_utility_model(monkeypatch)
    proposals = review(client, conversation_id)
    rule = next(item for item in proposals if item["kind"] == "rule")
    memory = next(item for item in proposals if item["kind"] == "memory")
    before = commits(folder)

    response = client.post(f"/api/v1/projects/{project['id']}/learning/proposals/{rule['id']}/accept")

    assert response.status_code == 204, response.text
    assert commits(folder) == before + 1
    assert REASON in (folder / ".blueprint" / "standards" / "rules-project.md").read_text(encoding="utf-8")
    assert rule["branch"] not in branches(folder)
    # A memory line is written on accept, as one memory commit.
    assert client.post(f"/api/v1/projects/{project['id']}/learning/proposals/{memory['id']}/accept").status_code == 204
    assert commits(folder) == before + 2
    assert TEST_COMMAND in (folder / ".blueprint" / "memory.md").read_text(encoding="utf-8")
    assert client.get(f"/api/v1/projects/{project['id']}/learning/proposals").json() == []
    again = client.post(f"/api/v1/projects/{project['id']}/learning/proposals/{rule['id']}/accept")
    assert again.status_code == 404


def test_declining_leaves_the_layer_unchanged_and_removes_the_branch(client, monkeypatch):
    project, conversation_id, folder = signalled_conversation(client)
    no_utility_model(monkeypatch)
    rule = next(item for item in review(client, conversation_id) if item["kind"] == "rule")
    head = run_git(["rev-parse", "HEAD"], folder).stdout.strip()

    response = client.post(f"/api/v1/projects/{project['id']}/learning/proposals/{rule['id']}/decline")

    assert response.status_code == 204
    assert run_git(["rev-parse", "HEAD"], folder).stdout.strip() == head
    assert rule["branch"] not in branches(folder)
    assert not (folder / ".blueprint" / "standards" / "rules-project.md").exists()
    with client.app.state.session_factory() as session:
        assert session.get_one(LearningProposal, rule["id"]).status == "declined"


# --- Measuring ------------------------------------------------------------------------------------------------------


def wait_measured(client, project_id: int, proposal_id: int) -> dict:
    for _ in range(400):
        proposals = client.get(f"/api/v1/projects/{project_id}/learning/proposals").json()
        proposal = next(item for item in proposals if item["id"] == proposal_id)
        if proposal["measurement"]["status"] == "measured":
            return proposal
        time.sleep(0.05)
    raise AssertionError("The measurement did not finish.")


def test_a_proposal_is_measured_with_the_eval_cases_with_and_without_it(client, fake, monkeypatch):
    project, conversation_id, folder = signalled_conversation(client)
    commit = run_git(["rev-parse", "HEAD"], folder).stdout.strip()
    write_case(folder, "greet", commit)
    commit_all(folder, "An eval case")
    no_utility_model(monkeypatch)
    rule = next(item for item in review(client, conversation_id) if item["kind"] == "rule")
    assert rule["measurement"]["status"] == "ready"
    fake.responses.extend([WRITE_HELLO] * 6)

    started = client.post(f"/api/v1/projects/{project['id']}/learning/proposals/{rule['id']}/measure")

    assert started.status_code == 200, started.text
    assert started.json()["measurement"]["status"] in ("measuring", "measured")
    measured = wait_measured(client, project["id"], rule["id"])["measurement"]
    assert measured["pass_rate_without"] == measured["pass_rate_with"] == 1.0 and measured["effect"] == "same"
    variant = client.get(f"/api/v1/projects/{project['id']}/evals/runs/{measured['variant_run_id']}").json()
    assert variant["variant"]["ref"] == rule["branch"] and variant["variant"]["layer"] == "project"
    systems = ["\n".join(part.text for part in request.system) for request in fake.requests[-6:]]
    assert sum(REASON in system for system in systems) == 3


# --- Checking the rules ---------------------------------------------------------------------------------------------


def test_a_rule_check_proposes_its_removal_and_accepting_removes_it(client):
    project, _conversation_id = project_conversation(client)
    folder = project_folder(client, project)
    standard = folder / ".blueprint" / "standards" / "style.md"
    standard.parent.mkdir(parents=True, exist_ok=True)
    content = "---\nsummary: Style\nrules:\n- Use tabs.\n- Keep lines short.\n---\n\nStyle.\n"
    standard.write_text(content, encoding="utf-8")
    commit_all(folder, "Style")
    rules = client.get(f"/api/v1/projects/{project['id']}/learning/rules").json()
    assert {"level": "project", "path": "standards/style.md", "rule": "Use tabs."} in rules

    checked = client.post(
        f"/api/v1/projects/{project['id']}/learning/rule-checks",
        json={"level": "project", "path": "standards/style.md", "rule": "Use tabs."},
    )

    assert checked.status_code == 200, checked.text
    proposal = checked.json()
    assert proposal["kind"] == "rule_removal" and proposal["measurement"]["status"] == "not_measured"
    accepted = client.post(f"/api/v1/projects/{project['id']}/learning/proposals/{proposal['id']}/accept")
    assert accepted.status_code == 204
    text = standard.read_text(encoding="utf-8")
    assert "Use tabs." not in text and "Keep lines short." in text


def test_contradictions_across_levels_are_reported(client, fake):
    project, _conversation_id = project_conversation(client)
    folder = project_folder(client, project)
    remembered = client.post("/api/v1/blueprint/rule", json={"rule": "Indent with tabs.", "apply": True})
    assert remembered.status_code == 200, remembered.text
    standard = folder / ".blueprint" / "standards" / "style.md"
    standard.parent.mkdir(parents=True, exist_ok=True)
    standard.write_text("---\nrules:\n- Indent with four spaces.\n---\n\nStyle.\n", encoding="utf-8")
    fake.responses.append(ScriptedResponse(text=json.dumps([{"rules": [1, 2], "explanation": "Tabs against spaces."}])))

    report = client.post(f"/api/v1/projects/{project['id']}/learning/contradictions").json()

    assert report["available"] is True
    (found,) = report["contradictions"]
    assert {rule["level"] for rule in found["rules"]} == {"global", "project"}
    assert found["explanation"] == "Tabs against spaces."


# --- After a goal ---------------------------------------------------------------------------------------------------


def test_a_review_is_due_after_a_goal_ended_with_new_signals(client):
    _project, conversation_id, _folder = signalled_conversation(client)
    factory = client.app.state.session_factory
    assert review_due(factory, conversation_id) is False
    with factory() as session:
        session.add(
            Goal(
                conversation_id=conversation_id, objective="Fix the cart", status="done", max_turns=5, max_cost=1.0,
                turns=2, finished_at=datetime.now(UTC) + timedelta(seconds=10),
            )
        )
        session.commit()
    assert review_due(factory, conversation_id) is True
    with factory() as session:
        session.get_one(Conversation, conversation_id).learning_checked_at = datetime.now(UTC) + timedelta(minutes=1)
        session.commit()
    assert review_due(factory, conversation_id) is False
