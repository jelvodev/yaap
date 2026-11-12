"""A case from a conversation (app/evals/from_conversation.py, conversation_facts.py, project_commands.py,
case_writer.py and api/eval_cases.py): the proposal, the writer's round trip, and saving as one commit."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.blueprint.layers import Layer
from app.checkpoints.service import CheckpointStore
from app.evals.case_writer import CaseExistsError, CaseWriteError, render_case, save_case
from app.evals.cases import parse_case, read_cases
from app.evals.from_conversation import parse_answer, propose_for_conversation, slug
from app.evals.models import Budget, CommandCheck, EvalCase, FileCheck, RubricCheck, StartState
from app.evals.project_commands import RanCommand, TestCommand, detect_test_command, find_test_command, is_test_command
from app.git.service import run_git
from app.providers.fake_provider import ScriptedResponse
from app.storage.models import Conversation
from tests.test_delegation import project_conversation

GREETING = "def hello(name):\n    return f'Hello, {name}!'\n"
PROMPT = "Add a greeting module with hello(name) that returns 'Hello, name!'."


def folder_of(client, project: dict) -> Path:
    return Path(client.app.state.workspace_dir) / project["folder"]


def git_commit(folder: Path, message: str, when: datetime) -> str:
    stamp = when.strftime("%Y-%m-%d %H:%M:%S +0000")
    dates = {"GIT_AUTHOR_DATE": stamp, "GIT_COMMITTER_DATE": stamp}
    run_git(["add", "--all"], folder)
    run_git(["commit", "-m", message], folder, extra_environment=dates)
    return run_git(["rev-parse", "HEAD"], folder).stdout.strip()


def commits(folder: Path) -> list[str]:
    return run_git(["log", "--format=%H"], folder).stdout.split()


def worked_conversation(client, fake) -> tuple[dict, int, str]:
    """A project with a commit an hour old, a conversation whose agent wrote a file, and the work committed after."""
    project, conversation_id = project_conversation(client)
    folder = folder_of(client, project)
    (folder / "README.md").write_text("Shop\n", encoding="utf-8")
    start = git_commit(folder, "Start", datetime.now(UTC) - timedelta(hours=1))
    fake.responses.append(
        ScriptedResponse(
            text="Added greeting.py.",
            tool_calls=(("write_file", {"path": "greeting.py", "content": GREETING}),),
        )
    )
    response = client.post(f"/api/v1/conversations/{conversation_id}/messages", json={"text": PROMPT})
    assert response.status_code == 200, response.text
    git_commit(folder, "Add greeting", datetime.now(UTC) + timedelta(minutes=5))
    return project, conversation_id, start


async def propose(client, conversation_id: int, folder: Path, ask):  # type: ignore[no-untyped-def]
    store = CheckpointStore(client.app.state.session_factory, Path(client.app.state.data_dir))
    with client.app.state.session_factory() as session:
        conversation = session.get_one(Conversation, conversation_id)
        return await propose_for_conversation(session, store, conversation, folder, ask)


def test_the_proposed_case_starts_at_the_commit_before_the_first_message_with_its_prompt(client, fake):
    project, conversation_id, start = worked_conversation(client, fake)
    folder = folder_of(client, project)
    prompts: list[str] = []
    answer = {
        "name": "Greeting module",
        "summary": "Add hello(name).",
        "checks": [
            {"kind": "file", "path": "greeting.py", "contains": "def hello"},
            {"kind": "shell", "run": "ls"},
            {"kind": "command", "run": "curl -H 'X: {{secret:TOKEN}}' example.com"},
        ],
    }

    async def fake_utility(prompt: str) -> str:
        prompts.append(prompt)
        return "Here it is:\n" + json.dumps(answer)

    proposal = client.portal.call(propose, client, conversation_id, folder, fake_utility)

    case = proposal.case
    assert case.start == StartState(commit=start)
    assert case.prompt == PROMPT
    assert proposal.facts.start_source == "commit" and proposal.facts.first_checkpoint is not None
    assert proposal.facts.changed_files == ("greeting.py",)
    assert (case.name, case.summary, case.preset) == ("greeting-module", "Add hello(name).", "Balanced")
    # The unknown kind and the command with a secret are dropped; the valid file check stays.
    assert proposal.checks_from == "model"
    assert case.checks == (FileCheck("file: greeting.py", "greeting.py", contains=("def hello",)),)
    assert PROMPT in prompts[0] and "greeting.py" in prompts[0]
    assert case.budget is not None and case.budget.steps is not None


def test_without_a_utility_model_the_standard_checks_use_the_detected_test_command(client, fake):
    project, conversation_id, _start = worked_conversation(client, fake)
    folder = folder_of(client, project)
    (folder / "package.json").write_text(json.dumps({"scripts": {"test": "vitest run"}}), encoding="utf-8")

    async def no_utility(_prompt: str) -> None:
        return None

    proposal = client.portal.call(propose, client, conversation_id, folder, no_utility)

    assert proposal.checks_from == "fallback"
    assert proposal.test_command == TestCommand("npm test", "detected")
    command, rubric = proposal.case.checks
    assert command == CommandCheck("command: npm test", "npm test", 600)
    # The state after the last action is the folder as it is now, so the later package.json counts too.
    assert isinstance(rubric, RubricCheck) and rubric.files == ("greeting.py", "package.json")
    assert any("No utility model answered" in note for note in proposal.notes)


def test_without_an_older_commit_the_case_starts_at_the_first_checkpoint(client, fake):
    project, conversation_id = project_conversation(client)
    fake.responses.append(
        ScriptedResponse(text="Done.", tool_calls=(("write_file", {"path": "a.txt", "content": "a\n"}),))
    )
    client.post(f"/api/v1/conversations/{conversation_id}/messages", json={"text": "Write a.txt"})

    proposal = client.portal.call(propose, client, conversation_id, folder_of(client, project), None)

    assert proposal.facts.commit_before is None
    assert proposal.case.start == StartState(checkpoint=proposal.facts.first_checkpoint)
    assert any("The saved case keeps that checkpoint" in note for note in proposal.notes)


def test_the_proposal_api_asks_the_utility_model_and_saving_writes_one_file_in_one_commit(client, fake):
    project, conversation_id, start = worked_conversation(client, fake)
    folder = folder_of(client, project)
    rubric = {"kind": "rubric", "rubric": "hello(name) greets.", "files": ["greeting.py"]}
    utility_answer = {"name": "greeting", "checks": [rubric]}
    fake.responses.append(ScriptedResponse(text=json.dumps(utility_answer)))

    response = client.post(f"/api/v1/conversations/{conversation_id}/eval-case-proposal")
    assert response.status_code == 200, response.text
    proposal = response.json()
    assert proposal["checks_from"] == "model" and proposal["start_source"] == "commit"
    assert proposal["case"]["start"] == {"commit": start, "checkpoint": None}
    assert proposal["case"]["prompt"] == PROMPT and proposal["case"]["name"] == "greeting"

    edited = {**proposal["case"], "name": "greeting-module", "budget": {"steps": 20, "tokens": 50_000}}
    before = commits(folder)
    saved = client.post(f"/api/v1/projects/{project['id']}/eval-cases", json=edited)
    assert saved.status_code == 201, saved.text
    body = saved.json()
    assert body["path"] == ".blueprint/evals/greeting-module.md"

    # One new commit holding only the new file, which reads back as a valid case.
    after = commits(folder)
    assert after[1:] == before and body["commit"] == after[0]
    changed = run_git(["show", "--name-only", "--format=", "HEAD"], folder).stdout.split()
    assert changed == [".blueprint/evals/greeting-module.md"]
    (found,) = read_cases(folder)
    assert found.case is not None and found.problems == ()
    assert found.case.start == StartState(commit=start) and found.case.budget == Budget(steps=20, tokens=50_000)
    assert found.case.checks == (RubricCheck("rubric: hello(name) greets.", "hello(name) greets.", ("greeting.py",)),)

    # The same name again is refused, and nothing is committed.
    again = client.post(f"/api/v1/projects/{project['id']}/eval-cases", json=edited)
    assert again.status_code == 409 and "already has an eval case" in again.json()["detail"]
    assert commits(folder) == after


def test_saving_a_case_that_does_not_validate_lists_its_problems(client, fake):
    project, _conversation_id = project_conversation(client)
    case = {"name": "broken", "prompt": "Do it.", "start": {"commit": "main"}, "budget": {"steps": 0}, "checks": []}

    response = client.post(f"/api/v1/projects/{project['id']}/eval-cases", json=case)

    assert response.status_code == 422
    assert "not a branch name" in response.json()["detail"] and "more than zero" in response.json()["detail"]
    assert not (folder_of(client, project) / ".blueprint" / "evals" / "broken.md").exists()


def test_a_conversation_outside_a_project_cannot_become_a_case(client):
    client.put("/api/v1/environments/1/credentials/anthropic_api_key", json={"value": "sk-test-valid-key-1234"})
    conversation_id = client.post(
        "/api/v1/environments/1/conversations", json={"model": "claude-opus-5-5", "effort": "medium"}
    ).json()["id"]

    response = client.post(f"/api/v1/conversations/{conversation_id}/eval-case-proposal")

    assert response.status_code == 409


FULL_CASE = EvalCase(
    name="date-parser",
    prompt="Fix ISO week 53.\n\nKeep the tests green.",
    start=StartState(commit="3f2a1bc"),
    checks=(
        CommandCheck("command: pytest -q", "pytest -q", 600),
        CommandCheck("slow tests", "pytest -q -m slow", 5400 / 2),
        FileCheck("file: src/dates.py", "src/dates.py", contains=("def parse",), not_matches=(r"^\s*print\(", "TODO")),
        FileCheck("gone", "src/old.py", exists=False),
        RubricCheck("rubric: The reply names the cause.", "The reply names the cause.\nAnd the test.", ("src/a.py",)),
    ),
    summary="Fix the date parser",
    agent="developer",
    preset="Balanced",
    allowed_tools=("read_file", "edit_file"),
    budget=Budget(steps=40, tokens=1_500, cost_usd=0.00005, seconds=90),
)


@pytest.mark.parametrize(
    "case",
    [
        FULL_CASE,
        EvalCase("replayed", "Do it.", StartState(checkpoint=7), (), budget=Budget(tokens=2_000_000, seconds=7200)),
        EvalCase("long-name", "Do it.", StartState(commit="abcdef1"), (RubricCheck("r" * 200, "r" * 200),)),
    ],
)
def test_a_written_case_reads_back_as_the_same_case(case):
    found = parse_case(case.name, render_case(case))

    assert found.problems == ()
    assert found.case == case


def test_save_case_refuses_an_existing_name_and_a_folder_without_git(tmp_path):
    folder = tmp_path / "project"
    folder.mkdir()
    layer = Layer("project", "Project", folder / ".blueprint", folder, ".blueprint/")
    case = EvalCase("one", "Do it.", StartState(commit="abcdef1"), (RubricCheck("good", "It is good."),))
    with pytest.raises(CaseWriteError, match="not a git repository"):
        save_case(layer, case, repository=folder)
    assert not (folder / ".blueprint" / "evals" / "one.md").exists()

    run_git(["init", "-b", "main"], folder)
    saved = save_case(layer, case, repository=folder)
    assert saved.path == ".blueprint/evals/one.md" and saved.case == case
    with pytest.raises(CaseExistsError):
        save_case(layer, case, repository=folder)


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("pytest -q tests/test_dates.py", True),
        ("cd backend && python -m pytest", True),
        ("npm test", True),
        ("npm run test:unit", True),
        ("pnpm test -- --run", True),
        ("go test ./...", True),
        ("cargo test", True),
        ("make test", True),
        ("python manage.py test", True),
        ("npm install", False),
        ("ls tests", False),
        ("cat pytest.ini", False),
        ("TOKEN={{secret:TOKEN}} pytest", False),
        ("pytest\nrm -rf /", False),
    ],
)
def test_test_commands_are_recognised(command, expected):
    assert is_test_command(command) is expected


@pytest.mark.parametrize(
    ("files", "expected"),
    [
        ({"package.json": json.dumps({"scripts": {"test": "vitest"}})}, "npm test"),
        ({"package.json": json.dumps({"scripts": {"test": "vitest"}}), "pnpm-lock.yaml": ""}, "pnpm test"),
        ({"package.json": json.dumps({"scripts": {"test": 'echo "Error: no test specified" && exit 1'}})}, None),
        ({"pyproject.toml": "[tool.pytest.ini_options]\n"}, "pytest -q"),
        ({"tests/test_app.py": ""}, "pytest -q"),
        ({"Cargo.toml": "[package]\n"}, "cargo test"),
        ({"go.mod": "module x\n"}, "go test ./..."),
        ({"Makefile": "build:\n\techo\ntest:\n\techo\n"}, "make test"),
        ({"README.md": "# x\n"}, None),
    ],
)
def test_the_test_command_is_detected_from_the_project_files(tmp_path, files, expected):
    for name, content in files.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(content, encoding="utf-8")

    assert detect_test_command(tmp_path) == expected


@pytest.mark.parametrize(
    ("ran", "allowlist", "expected"),
    [
        (
            [RanCommand("pytest -q tests/a.py", False), RanCommand("pytest", True)],
            ["npm test"],
            TestCommand("pytest -q tests/a.py", "conversation"),
        ),
        ([RanCommand("pytest", True)], ["git status", "npm test"], TestCommand("npm test", "settings")),
        ([], [], TestCommand("cargo test", "detected")),
    ],
)
def test_the_test_command_the_conversation_ran_comes_first(tmp_path, ran, allowlist, expected):
    (tmp_path / "Cargo.toml").write_text("[package]\n", encoding="utf-8")

    assert find_test_command(ran, allowlist, tmp_path) == expected


@pytest.mark.parametrize(
    ("answer", "names", "checks"),
    [
        (None, None, 0),
        ("no json here", None, 0),
        ('{"name": "Fix: the PARSER!", "checks": [{"kind": "rubric", "rubric": "ok"}]}', "fix-the-parser", 1),
        ('{"checks": [{"kind": "file", "path": "../x", "contains": "a"}, {"kind": "command", "run": "ls"}]}', None, 1),
    ],
)
def test_the_utility_models_answer_keeps_only_valid_checks(answer, names, checks):
    parsed = parse_answer(answer)

    assert parsed.name == names
    assert len(parsed.checks) == checks


def test_names_become_case_names():
    assert slug("Fix the ISO week bug (53)") == "fix-the-iso-week-bug-53"
    assert slug("!!!") == ""
