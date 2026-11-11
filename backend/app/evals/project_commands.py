"""The project's test command, for an eval case's command check.

Three sources, nearest first: a test command the conversation itself ran and that succeeded (it is known to work
in this project, often narrowed to the tests that matter); a test command in the project's command allowlist (the
user said it may run without asking); and what the project's files suggest (package.json, pyproject.toml and the
like). Each is a pure function of its input, so the choice is table-tested.
"""

import json
import re
import stat
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from app.targets.safe_root import SafeRoot

TestCommandSource = Literal["conversation", "settings", "detected"]

TEST_COMMAND = re.compile(
    r"(?:^|[\s/;&|(])(?:pytest|py\.test|tox|nox|vitest|jest|phpunit|rspec)(?:\s|$)"
    r"|\b(?:npm|pnpm|yarn|bun)\s+(?:run\s+)?test(?::\S+)?(?:\s|$)"
    r"|\b(?:go|cargo|mvn|gradle|gradlew|dotnet|mix|make)\s+test\b"
    r"|\bmanage\.py\s+test\b"
    r"|\bpython\d?(?:\.\d+)?\s+-m\s+(?:pytest|unittest)\b"
)
# A command with a secret placeholder runs with the secret's value; a case file must never carry one.
SECRET_PLACEHOLDER = "{{secret:"
NPM_DEFAULT_TEST = "no test specified"
MAX_COMMAND_CHARS = 300


@dataclass(frozen=True)
class RanCommand:
    """A run_command call of the conversation: the command text and whether it failed."""

    command: str
    is_error: bool


@dataclass(frozen=True)
class TestCommand:
    # Not a test class, although pytest would collect it by its name in test modules that import it.
    __test__ = False

    command: str
    source: TestCommandSource


def is_test_command(command: str) -> bool:
    text = command.strip()
    if not text or SECRET_PLACEHOLDER in text or len(text) > MAX_COMMAND_CHARS or "\n" in text:
        return False
    return TEST_COMMAND.search(text) is not None


def find_test_command(ran: Sequence[RanCommand], allowlist: Sequence[str], folder: Path) -> TestCommand | None:
    """The test command for a case: the last one the conversation ran with success, else one from the allowlist,
    else one the project's files suggest; None when there is none."""
    for call in reversed(ran):
        if not call.is_error and is_test_command(call.command):
            return TestCommand(call.command.strip(), "conversation")
    for entry in allowlist:
        if is_test_command(entry):
            return TestCommand(entry.strip(), "settings")
    detected = detect_test_command(folder)
    return TestCommand(detected, "detected") if detected else None


def detect_test_command(folder: Path) -> str | None:
    """What the project's files suggest, in a fixed order; None when nothing does."""
    try:
        with SafeRoot(folder) as root:
            package = node_test_command(root)
            if package:
                return package
            if is_python_tested(root):
                return "pytest -q"
            if root.is_file("Cargo.toml"):
                return "cargo test"
            if root.is_file("go.mod"):
                return "go test ./..."
            if has_make_target(root, "Makefile", "test"):
                return "make test"
    except OSError:
        return None
    return None


def node_test_command(root: SafeRoot) -> str | None:
    """`npm test` (or pnpm or yarn, by the lock file) when package.json has a real test script."""
    try:
        contents = root.read_limited("package.json", 256 * 1024)
        if contents is None:
            return None
        package = json.loads(contents.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    scripts = package.get("scripts") if isinstance(package, dict) else None
    script = scripts.get("test") if isinstance(scripts, dict) else None
    if not isinstance(script, str) or not script.strip() or NPM_DEFAULT_TEST in script:
        return None
    if root.is_file("pnpm-lock.yaml"):
        return "pnpm test"
    if root.is_file("yarn.lock"):
        return "yarn test"
    return "npm test"


def is_python_tested(root: SafeRoot) -> bool:
    if root.is_file("pytest.ini") or root.is_file("conftest.py"):
        return True
    for name, marker in (("pyproject.toml", "[tool.pytest"), ("setup.cfg", "[tool:pytest]"), ("tox.ini", "[pytest]")):
        if marker in read_small(root, name):
            return True
    return root.is_dir("tests") and any(
        name.startswith("tests/test_") and name.endswith(".py") and not is_directory
        for name, info in root.entries("tests", recursive=False)
        for is_directory in (stat.S_ISDIR(info.st_mode),)
    )


def has_make_target(root: SafeRoot, path: str, target: str) -> bool:
    return re.search(rf"^{re.escape(target)}\s*:", read_small(root, path), re.MULTILINE) is not None


def read_small(root: SafeRoot, path: str, limit: int = 256 * 1024) -> str:
    """A small text file's content, or empty when it is absent, too large or unreadable."""
    try:
        content = root.read_limited(path, limit)
        return content.decode("utf-8", errors="replace") if content is not None else ""
    except OSError:
        return ""
