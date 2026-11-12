"""The permission decision as a pure function: each rule of app.agent.permissions in one table."""

import dataclasses
from typing import Any, cast, get_args

import pytest

from app.agent import gate
from app.agent.delegation import sub_run_access
from app.agent.permissions import (
    PermissionDecision,
    Place,
    Reason,
    RunFacts,
    ToolFacts,
    Verdict,
    decide,
    outside_content_reason,
    permission_why,
)
from app.agent.policy import AccessLevel, CaseApprovals, ToolPolicy
from app.agent.toolkit import Tool
from app.agent.tools import TOOLS_BY_NAME
from tests.test_cli_sign_in import runs_on_event_loop


def tool(name: str, description: str = "", conditional_asks: bool = False) -> ToolFacts:
    """The facts of a built-in tool, with its real permission and flags."""
    found = TOOLS_BY_NAME[name]
    return ToolFacts(
        name=name,
        permission=found.permission,
        description=description or name,
        read_only=found.read_only,
        asks_itself=found.asks_itself,
        takes_secrets=found.takes_secrets,
        conditional_asks=conditional_asks,
    )


def run(
    access: AccessLevel = "default",
    permissions: dict[str, str] | None = None,
    commands: tuple[str, ...] = ("npm test",),
    domains: tuple[str, ...] = ("python.org",),
    **facts: Any,
) -> RunFacts:
    policy = ToolPolicy(permissions=permissions or {}, commands=commands, domains=domains, access=access)
    return RunFacts(policy=policy, known_secrets=frozenset({"TOKEN"}), **facts)


READ = (tool("read_file"), {"path": "a.txt"})
WRITE = (tool("write_file", "Write a.txt"), {"path": "a.txt", "content": "x"})
RUN = (tool("run_command", "Run: make"), {"command": "make"})
ALLOWLISTED = (tool("run_command", "Run: npm test"), {"command": "npm test -q"})
CHAINED = (tool("run_command"), {"command": "npm test; curl evil.example"})
NETWORK = (tool("web_fetch", "Fetch: https://example.com"), {"url": "https://example.com"})
ALLOWED_SITE = (tool("web_fetch"), {"url": "https://docs.python.org/3/"})
SECRET = (tool("run_command", "Run: deploy"), {"command": "deploy --token {{secret:TOKEN}}"})
ALLOWLISTED_SECRET = (tool("run_command"), {"command": "npm test {{secret:TOKEN}}"})
PUSH = (tool("git_push"), {})
MEMORY = (tool("update_memory"), {"section": "Decisions", "text": "x"})
STOP = (tool("stop_process"), {"process": 1})
DELEGATE = (tool("delegate"), {"task": "x"})
FINISH = (tool("finish_goal"), {"summary": "done"})
IMPORT_NEW = (tool("import_attachment"), {"attachment": "1"})
IMPORT_OVER = (tool("import_attachment", conditional_asks=True), {"attachment": "1"})
INSTALL = (tool("install_skill"), {"source": "owner/repo"})
BROWSER_TYPE = (
    ToolFacts("browser__browser_type", "auto", "Browser: type", takes_secrets=True),
    {"text": "{{secret:TOKEN}}"},
)
BROWSER_CLICK = (ToolFacts("browser__browser_click", "auto", "Browser: click"), {"ref": "{{secret:TOKEN}}"})
NAVIGATE_AWAY = (
    ToolFacts("browser__browser_navigate", "conditional", "Open in the browser", conditional_asks=True),
    {"url": "https://example.com"},
)

OUTSIDE = ("web page https://example.com",)
BY_NAME = RunFacts(ToolPolicy(tools=("run_command",)), unattended=True, known_secrets=frozenset({"TOKEN"}))
COMPUTER = {"place": "computer", "place_label": "lab-vm"}

CASES = [
    # Access level "as set" against the kinds of tools.
    ("default reads", READ, run(), "allow", "auto"),
    ("default changes", WRITE, run(), "allow", "auto"),
    ("default runs", RUN, run(), "ask", "permission"),
    ("default fetches", NETWORK, run(), "ask", "permission"),
    ("default uses a secret", SECRET, run(), "ask", "secret"),
    ("default pushes", PUSH, run(), "ask", "permission"),
    ("default adds to memory", MEMORY, run(), "allow", "auto"),
    # Full access.
    ("full reads", READ, run("full"), "allow", "auto"),
    ("full changes", WRITE, run("full"), "allow", "auto"),
    ("full runs", RUN, run("full"), "allow", "full_access"),
    ("full fetches", NETWORK, run("full"), "allow", "full_access"),
    ("full still asks for a secret", SECRET, run("full"), "ask", "secret"),
    ("full still asks to push", PUSH, run("full"), "ask", "permission"),
    ("full navigates the browser anywhere", NAVIGATE_AWAY, run("full"), "allow", "full_access"),
    # Read-only conversations.
    ("read only reads", READ, run("read_only"), "allow", "auto"),
    ("read only refuses changes", WRITE, run("read_only"), "refuse", "read_only"),
    ("read only refuses commands", RUN, run("read_only"), "refuse", "read_only"),
    ("read only fetches as usual", NETWORK, run("read_only"), "ask", "permission"),
    ("read only refuses a secret", SECRET, run("read_only"), "refuse", "read_only"),
    ("read only refuses pushing", PUSH, run("read_only"), "refuse", "read_only"),
    ("read only refuses memory", MEMORY, run("read_only"), "refuse", "read_only"),
    ("read only delegates", DELEGATE, run("read_only"), "allow", "auto"),
    ("read only finishes a goal", FINISH, run("read_only"), "allow", "auto"),
    # The policy's per-tool rules and allowlists.
    ("a tool set to ask asks", WRITE, run(permissions={"write_file": "ask"}), "ask", "permission"),
    ("a command set to auto runs", RUN, run(permissions={"run_command": "auto"}), "allow", "auto"),
    ("pushing cannot be set to auto", PUSH, run(permissions={"git_push": "auto"}), "ask", "permission"),
    ("an allowlisted command runs", ALLOWLISTED, run(), "allow", "allowlisted"),
    ("a chained command is not allowlisted", CHAINED, run(), "ask", "permission"),
    ("an allowlisted website is fetched", ALLOWED_SITE, run(), "allow", "allowlisted"),
    ("an allowlisted command with a secret asks", ALLOWLISTED_SECRET, run(), "ask", "secret"),
    ("a new import runs", IMPORT_NEW, run(), "allow", "auto"),
    ("an import that overwrites asks", IMPORT_OVER, run(), "ask", "permission"),
    # The injection guard after outside content.
    ("guard lets reading run", READ, run(outside_sources=OUTSIDE), "allow", "auto"),
    ("guard asks before a change", WRITE, run(outside_sources=OUTSIDE), "ask", "outside_content"),
    ("guard overrides the allowlist", ALLOWLISTED, run(outside_sources=OUTSIDE), "ask", "outside_content"),
    ("guard asks before memory", MEMORY, run(outside_sources=OUTSIDE), "ask", "outside_content"),
    ("guard leaves install_skill to itself", INSTALL, run(outside_sources=OUTSIDE), "allow", "auto"),
    ("full access silences the guard", WRITE, run("full", outside_sources=OUTSIDE), "allow", "full_access"),
    (
        "full access still asks to push after outside content",
        PUSH,
        run("full", outside_sources=OUTSIDE),
        "ask",
        "outside_content",
    ),
    # Unattended runs refuse what would ask.
    ("unattended reads", READ, run(unattended=True), "allow", "auto"),
    ("unattended refuses a command", RUN, run(unattended=True), "refuse", "unattended"),
    ("unattended runs an allowlisted command", ALLOWLISTED, run(unattended=True), "allow", "allowlisted"),
    ("unattended refuses a secret", SECRET, run(unattended=True), "refuse", "unattended"),
    # An eval case allows tools by name: any call of them runs, other calls that ask are still refused.
    ("allowed by name runs any command", RUN, BY_NAME, "allow", "allowlisted"),
    ("allowed by name leaves other tools", NETWORK, BY_NAME, "refuse", "unattended"),
    ("allowed by name still asks for a secret", SECRET, BY_NAME, "refuse", "unattended"),
    (
        "unattended refuses after outside content",
        WRITE,
        run(unattended=True, outside_sources=OUTSIDE),
        "refuse",
        "unattended",
    ),
    # A computer over SSH: changes cannot be rolled back.
    ("computer reads", READ, run(**COMPUTER), "allow", "auto"),
    ("computer asks before a change", WRITE, run(**COMPUTER), "ask", "cannot_roll_back"),
    ("computer asks for an allowlisted command", ALLOWLISTED, run(**COMPUTER), "ask", "cannot_roll_back"),
    ("computer asks before an import", IMPORT_NEW, run(**COMPUTER), "ask", "cannot_roll_back"),
    ("computer stops a process", STOP, run(**COMPUTER), "allow", "auto"),
    ("computer with full access changes", WRITE, run("full", **COMPUTER), "allow", "auto"),
    ("computer with full access runs", RUN, run("full", **COMPUTER), "allow", "full_access"),
    ("computer with full access asks for a secret", SECRET, run("full", **COMPUTER), "ask", "secret"),
    ("computer read only refuses a change", WRITE, run("read_only", **COMPUTER), "refuse", "read_only"),
    ("computer unattended refuses a change", WRITE, run(unattended=True, **COMPUTER), "refuse", "unattended"),
    ("computer leaves install_skill to itself", INSTALL, run(**COMPUTER), "allow", "asks_itself"),
    # The project's sandbox follows the project's permissions, as in the container.
    ("sandbox changes", WRITE, run(place="sandbox"), "allow", "auto"),
    ("sandbox asks for a command", RUN, run(place="sandbox"), "ask", "permission"),
    ("sandbox runs an allowlisted command", ALLOWLISTED, run(place="sandbox"), "allow", "allowlisted"),
    # Secret placeholders.
    (
        "a secret in a file is refused",
        (WRITE[0], {"path": "a", "content": "{{secret:TOKEN}}"}),
        run(),
        "refuse",
        "secret_not_allowed",
    ),
    ("an unknown secret is refused", (RUN[0], {"command": "x {{secret:NOPE}}"}), run(), "refuse", "unknown_secret"),
    ("the browser types a secret after asking", BROWSER_TYPE, run(), "ask", "secret"),
    ("the browser does not click a secret", BROWSER_CLICK, run(), "refuse", "secret_not_allowed"),
    (
        "full access does not allow a secret in a file",
        (WRITE[0], {"path": "a", "content": "{{secret:TOKEN}}"}),
        run("full"),
        "refuse",
        "secret_not_allowed",
    ),
]


@pytest.mark.parametrize(
    ("call", "facts", "verdict", "reason"),
    [pytest.param(call, facts, verdict, reason, id=name) for name, call, facts, verdict, reason in CASES],
)
def test_the_decision_follows_the_rules(
    call: tuple[ToolFacts, dict[str, Any]], facts: RunFacts, verdict: Verdict, reason: Reason
) -> None:
    decision = decide(call[0], call[1], facts)
    assert (decision.verdict, decision.reason) == (verdict, reason)
    assert bool(decision.message) == (verdict == "refuse")
    assert bool(decision.summary) == (verdict == "ask")


def test_the_card_names_the_secret_and_that_a_computer_cannot_roll_back() -> None:
    decision = decide(*SECRET, run("full", **COMPUTER))
    assert decision.summary == "Run: deploy (uses secret TOKEN) — on lab-vm, which YAAP cannot roll back"
    assert decision.secret_names == ("TOKEN",)


def test_the_card_carries_the_outside_content_also_when_something_else_asks() -> None:
    decision = decide(*SECRET, run(outside_sources=OUTSIDE))
    assert decision.reason == "secret"
    assert decision.outside_content == "Asked because this run read outside content: web page https://example.com."


def test_reading_tools_never_carry_the_guard() -> None:
    assert decide(*READ, run(outside_sources=OUTSIDE)).outside_content is None


def test_refusals_tell_the_model_what_to_do() -> None:
    assert decide(*WRITE, run("read_only")).message.startswith("Refused: this conversation is read only, so write_file")
    unknown = decide(RUN[0], {"command": "x {{secret:NOPE}}"}, run())
    assert unknown.message == "Unknown secret NOPE. Available: TOKEN."
    unattended = decide(*WRITE, run(unattended=True, outside_sources=OUTSIDE))
    assert "write_file needs approval (Asked because this run read outside content" in unattended.message


def test_the_guard_reason_shortens_a_long_list() -> None:
    assert outside_content_reason(()) is None
    labels = tuple(f"page {number}" for number in range(6))
    assert outside_content_reason(labels) == (
        "Asked because this run read outside content: page 0, page 1, page 2, page 3 and 2 more."
    )


def test_a_decision_cannot_be_changed() -> None:
    decision = decide(*RUN, run())
    with pytest.raises(dataclasses.FrozenInstanceError):
        decision.verdict = "allow"  # type: ignore[misc]  # checking that the frozen dataclass refuses the change
    assert isinstance(decision, PermissionDecision)


WHY_CASES = [
    ("read_only", "write_file", "access", "conversation"),
    ("permission", "run_command", "tool_permissions", None),
    ("permission", "git_push", None, None),
    ("allowlisted", "run_command", "command_allowlist", None),
    ("allowlisted", "web_fetch", "web_allowlist", None),
    ("outside_content", "write_file", "injection_check", None),
    ("case_approved", "write_file", None, None),
    ("full_access", "run_command", "access", "conversation"),
    ("secret", "run_command", None, None),
    ("cannot_roll_back", "write_file", None, None),
    ("unattended", "run_command", None, None),
    ("model_policy", "call_agent", "model_policy", None),
    ("secret_not_allowed", "write_file", None, None),
    ("unknown_secret", "run_command", None, None),
    ("auto", "read_file", None, None),
    ("asks_itself", "install_skill", None, None),
]


@pytest.mark.parametrize(
    ("reason", "tool_name", "setting", "layer"),
    [pytest.param(*case, id=f"{case[0]}-{case[1]}") for case in WHY_CASES],
)
def test_every_reason_has_a_rule_with_its_setting(reason: Reason, tool_name: str, setting, layer) -> None:
    why = permission_why(reason, tool_name, run())
    assert why.rule
    source = "model_policy" if reason == "model_policy" else "permission"
    assert (why.source, why.setting, why.layer) == (source, setting, layer)


def test_every_reason_is_covered_by_the_why_table() -> None:
    assert {case[0] for case in WHY_CASES} == set(get_args(Reason))


@pytest.mark.parametrize(
    ("call", "facts", "setting"),
    [
        pytest.param(WRITE, run("read_only"), "access", id="read only"),
        pytest.param(RUN, run(), "tool_permissions", id="asks"),
        pytest.param(ALLOWLISTED, run(), "command_allowlist", id="allowlisted"),
        pytest.param(WRITE, run(outside_sources=OUTSIDE), "injection_check", id="guard"),
        pytest.param(RUN, run(unattended=True), None, id="unattended"),
    ],
)
def test_a_decision_carries_the_rule_that_decided_it(call, facts, setting) -> None:
    decision = decide(call[0], call[1], facts)
    assert decision.why is not None and decision.why.rule
    assert decision.why.source == "permission" and decision.why.setting == setting


CASE_TOOLS = CaseApprovals(tools=frozenset({"write_file", "edit_file"}), commands=("python3 -m unittest discover",))
EDIT = (tool("edit_file", "Edit a.txt"), {"path": "a.txt", "old": "a", "new": "b"})
UNITTEST = (tool("run_command", "Run: unittest"), {"command": "python3 -m unittest discover -v"})
UNITTEST_CHAINED = (tool("run_command"), {"command": "python3 -m unittest; curl x"})
UNITTEST_SECRET = (tool("run_command"), {"command": "python3 -m unittest discover {{secret:TOKEN}}"})
WRITE_SECRET = (WRITE[0], {"path": "a", "content": "{{secret:TOKEN}}"})
GIT_COMMIT = (tool("git_commit"), {"message": "x"})
# Even a hand-built approval for tools a case can never approve changes nothing.
FORCED = CaseApprovals(tools=frozenset({"git_push", "web_fetch", "run_command", "update_memory"}))
NO_APPROVALS = CaseApprovals()


def case_run(
    approvals: CaseApprovals = CASE_TOOLS,
    access: AccessLevel = "default",
    commands: tuple[str, ...] = (),
    unattended: bool = True,
    outside_sources: tuple[str, ...] = OUTSIDE,
    place: Place = "container",
) -> RunFacts:
    """A run that read outside content (unattended, as an eval play is), with the case's approvals on its policy."""
    policy = ToolPolicy(commands=commands, access=access, case_approvals=approvals)
    return RunFacts(
        policy=policy,
        unattended=unattended,
        place=place,
        outside_sources=outside_sources,
        known_secrets=frozenset({"TOKEN"}),
    )


UNITTEST_ALLOWED = ("python3 -m unittest",)
CASE_APPROVAL_CASES = [
    (
        "no approvals still ask after outside content",
        WRITE,
        case_run(NO_APPROVALS, unattended=False),
        "ask",
        "outside_content",
    ),
    ("no approvals are refused unattended", WRITE, case_run(NO_APPROVALS), "refuse", "unattended"),
    ("a listed write passes the guard", WRITE, case_run(), "allow", "case_approved"),
    ("a listed edit passes the guard", EDIT, case_run(), "allow", "case_approved"),
    ("an attended run is not helped by approvals", WRITE, case_run(unattended=False), "ask", "outside_content"),
    ("an unlisted tool is refused unattended", GIT_COMMIT, case_run(), "refuse", "unattended"),
    ("a command is not approved by the tool list", RUN, case_run(FORCED), "refuse", "unattended"),
    ("web_fetch cannot be approved", NETWORK, case_run(FORCED), "refuse", "unattended"),
    ("git_push cannot be approved", PUSH, case_run(FORCED), "refuse", "unattended"),
    ("update_memory cannot be approved", MEMORY, case_run(FORCED), "refuse", "unattended"),
    (
        "an approved allowlisted command passes",
        UNITTEST,
        case_run(commands=UNITTEST_ALLOWED),
        "allow",
        "case_approved",
    ),
    ("an approved command still asks when it is not allowlisted", UNITTEST, case_run(), "refuse", "unattended"),
    (
        "a chained command is not approved",
        UNITTEST_CHAINED,
        case_run(commands=UNITTEST_ALLOWED),
        "refuse",
        "unattended",
    ),
    (
        "a command with a secret still asks for it",
        UNITTEST_SECRET,
        case_run(commands=UNITTEST_ALLOWED),
        "refuse",
        "unattended",
    ),
    ("a secret in a file is refused first", WRITE_SECRET, case_run(), "refuse", "secret_not_allowed"),
    ("read only refuses before approvals", WRITE, case_run(access="read_only"), "refuse", "read_only"),
    ("a computer that cannot roll back still asks", WRITE, case_run(place="computer"), "refuse", "unattended"),
    ("approvals without outside content change nothing", WRITE, case_run(outside_sources=()), "allow", "auto"),
]


@pytest.mark.parametrize(
    ("call", "facts", "verdict", "reason"),
    [pytest.param(call, facts, verdict, reason, id=name) for name, call, facts, verdict, reason in CASE_APPROVAL_CASES],
)
def test_case_approvals_pass_the_guard_only_in_an_unattended_play(
    call: tuple[ToolFacts, dict[str, Any]], facts: RunFacts, verdict: Verdict, reason: Reason
) -> None:
    decision = decide(call[0], call[1], facts)
    assert (decision.verdict, decision.reason) == (verdict, reason)


def test_a_case_approved_call_says_why_the_guard_did_not_ask() -> None:
    decision = decide(*WRITE, case_run())
    assert decision.why is not None and "eval case approves write_file" in decision.why.rule
    assert decision.outside_content is not None


@pytest.mark.parametrize("call", [WRITE, EDIT, RUN, UNITTEST, PUSH, NETWORK], ids=lambda call: call[0].name)
def test_approvals_without_outside_content_equal_no_approvals(call: tuple[ToolFacts, dict[str, Any]]) -> None:
    assert decide(*call, case_run(outside_sources=())) == decide(*call, case_run(NO_APPROVALS, outside_sources=()))


EVERY_CALL = [
    value
    for value in dict(globals()).values()
    if isinstance(value, tuple) and value[:1] and isinstance(value[0], ToolFacts)
]


@pytest.mark.parametrize("access", ["default", "full", "read_only"])
@pytest.mark.parametrize("outside", [(), OUTSIDE], ids=["no outside content", "outside content"])
@pytest.mark.parametrize("place", ["container", "sandbox", "computer"])
def test_an_attended_run_decides_the_same_with_or_without_approvals(
    access: AccessLevel, outside: tuple[str, ...], place: Place
) -> None:
    assert len(EVERY_CALL) > 10
    approvals = dataclasses.replace(FORCED, tools=FORCED.tools | CASE_TOOLS.tools, commands=("make", "npm test"))
    for call in EVERY_CALL:
        plain = run(access, outside_sources=outside, place=place)
        approved = dataclasses.replace(plain, policy=dataclasses.replace(plain.policy, case_approvals=approvals))
        assert decide(*call, approved) == decide(*call, plain), call[0].name


@pytest.mark.parametrize(
    ("decision", "approval"),
    [
        pytest.param(PermissionDecision("allow", "case_approved"), "case_approved", id="a case-approved call"),
        pytest.param(PermissionDecision("allow", "auto"), None, id="a call that ran without asking"),
        pytest.param(PermissionDecision("refuse", "unattended", "No."), "asked", id="an unattended call"),
    ],
)
@runs_on_event_loop
async def test_the_gate_records_how_a_call_was_let_through(
    monkeypatch: pytest.MonkeyPatch, decision: PermissionDecision, approval: str | None
) -> None:
    async def handler(arguments: dict[str, Any]) -> str:
        return "done"

    async def passthrough(run: object, name: str, arguments: dict[str, Any], run_handler: Any) -> str:
        return "done"

    monkeypatch.setattr(gate, "tool_facts", lambda *args: WRITE[0])
    monkeypatch.setattr(gate, "run_facts", lambda run: case_run())
    monkeypatch.setattr(gate, "decide", lambda *args: decision)
    monkeypatch.setattr(gate, "with_document", passthrough)
    echo = Tool("echo", "Echo.", {}, "auto", handler)
    note = gate.CallNote()
    await gate.gated_call(cast(Any, None), cast(Any, None), echo, "echo", {}, note)
    assert note.approval == approval


@pytest.mark.parametrize(
    ("parent", "child", "expected"),
    [
        ("read_only", "read_only", "read_only"),
        ("read_only", "default", "read_only"),
        ("read_only", "full", "read_only"),
        ("default", "read_only", "read_only"),
        ("default", "default", "default"),
        ("default", "full", "default"),
        ("full", "read_only", "read_only"),
        ("full", "default", "default"),
        ("full", "full", "full"),
    ],
)
def test_a_sub_run_never_gets_more_access_than_its_parent(
    parent: AccessLevel, child: AccessLevel, expected: AccessLevel
) -> None:
    assert sub_run_access(parent, child) == expected
