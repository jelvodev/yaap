"""The permission decision as a pure function: each rule of app.agent.permissions in one table."""

import dataclasses
from typing import Any, get_args

import pytest

from app.agent.permissions import (
    PermissionDecision,
    Reason,
    RunFacts,
    ToolFacts,
    Verdict,
    decide,
    outside_content_reason,
    permission_why,
)
from app.agent.policy import AccessLevel, ToolPolicy
from app.agent.tools import TOOLS_BY_NAME


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
