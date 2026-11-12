"""Test output that keeps what matters: the failures with their tracebacks, and the summary line.

A long test run is mostly passing tests the model does not need. The runner is recognised from the command and the
output is read tolerantly: when the expected shape is not found, nothing is returned and the caller keeps the output
as it is. The whole output stays readable with read_output.
"""

import re
import shlex
from collections.abc import Callable
from typing import Literal

TestRunner = Literal["pytest", "vitest", "jest", "go", "cargo", "dotnet"]

# What is kept of the failures and of the summary, in characters; together well below the store threshold.
FAILURE_BUDGET = 3_500
SUMMARY_BUDGET = 1_000

COMPOSITION = re.compile(r"[;&|`\n]|\$\(")
# A leading `cd <folder> &&` prints nothing and passes the exit code on, so the run behind it still counts.
LEADING_CD = re.compile(r"^(?:cd\s+[^\s;&|`$()]+\s*&&\s*)+")
ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
PYTHON = re.compile(r"^(python[\d.]*|py)$")
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
# Programs that run the next program: `npx vitest`, `uv run pytest`.
LAUNCHERS = frozenset({"npx", "bunx", "time"})
LAUNCHER_PAIRS = frozenset(
    {
        ("uv", "run"),
        ("poetry", "run"),
        ("pipenv", "run"),
        ("pdm", "run"),
        ("pnpm", "exec"),
        ("pnpm", "dlx"),
        ("npm", "exec"),
    }
)
DIRECT: dict[str, TestRunner] = {"pytest": "pytest", "py.test": "pytest", "vitest": "vitest", "jest": "jest"}
SUBCOMMANDS: dict[str, TestRunner] = {"go": "go", "cargo": "cargo", "dotnet": "dotnet"}


def program_name(token: str) -> str:
    name = re.split(r"[/\\]", token.strip("\"'"))[-1].lower()
    return re.sub(r"\.(exe|cmd|bat)$", "", name)


def skip_options(tokens: list[str]) -> list[str]:
    while tokens and tokens[0].startswith("-"):
        tokens = tokens[1:]
    return tokens


def runner_of(tokens: list[str]) -> TestRunner | None:
    while tokens and ASSIGNMENT.match(tokens[0]):
        tokens = tokens[1:]
    while tokens:
        head = program_name(tokens[0])
        if head in LAUNCHERS:
            tokens = skip_options(tokens[1:])
        elif len(tokens) > 1 and (head, program_name(tokens[1])) in LAUNCHER_PAIRS:
            tokens = skip_options(tokens[2:])
        else:
            break
    if not tokens:
        return None
    head = program_name(tokens[0])
    if head in DIRECT:
        return DIRECT[head]
    arguments = [program_name(token) for token in tokens[1:]]
    if PYTHON.match(head):
        after_module_flag = arguments[tokens[1:].index("-m") + 1 :] if "-m" in tokens[1:] else []
        return "pytest" if after_module_flag[:1] == ["pytest"] else None
    if head in SUBCOMMANDS and arguments[:1] == ["test"]:
        return SUBCOMMANDS[head]
    return None


def recognise_runner(command: str) -> TestRunner | None:
    """The test runner a command is, or None. Only a single invocation counts: in a compound command or a pipe the
    exit code and the output belong to more than the test run, so they are left as they are."""
    command = LEADING_CD.sub("", command.strip())
    if COMPOSITION.search(command):
        return None
    try:
        return runner_of(shlex.split(command, posix=False))
    except ValueError:
        return None


# Parsing helpers.


def split_blocks(lines: list[str], start: re.Pattern[str]) -> list[str]:
    """The text from each line matching `start` up to the next one; lines before the first are dropped."""
    blocks: list[list[str]] = []
    for line in lines:
        if start.search(line):
            blocks.append([])
        if blocks:
            blocks[-1].append(line)
    return ["\n".join(block).strip("\n") for block in blocks]


def region(lines: list[str], start: re.Pattern[str], end: re.Pattern[str]) -> list[str]:
    """The lines from the first match of `start` up to, not including, the next match of `end`."""
    for first, line in enumerate(lines):
        if start.search(line):
            for last in range(first + 1, len(lines)):
                if end.search(lines[last]):
                    return lines[first:last]
            return lines[first:]
    return []


def fit(text: str, budget: int) -> str:
    if len(text) <= budget:
        return text
    cut = text[:budget].rsplit("\n", 1)[0]
    return cut + "\n[… the rest is in the full output …]"


def joined_summary(summary: list[str]) -> str:
    return fit("\n".join(summary), SUMMARY_BUDGET)


def assemble(blocks: list[str], summary: list[str]) -> str:
    """The failures that fit the budget (at least the first, cut), how many are left out, and the summary."""
    kept: list[str] = []
    used = 0
    for block in blocks:
        if kept and used + len(block) > FAILURE_BUDGET:
            break
        kept.append(block if kept else fit(block, FAILURE_BUDGET))
        used += len(block) + 1
    text = "\n\n".join(kept)
    left = len(blocks) - len(kept)
    if left:
        text += f"\n\n[{left} more {'failure is' if left == 1 else 'failures are'} in the full output.]"
    return text + "\n\n" + joined_summary(summary)


# One reader per runner: lines -> (failure blocks, summary lines).

Reader = Callable[[list[str]], tuple[list[str], list[str]]]

PYTEST_SUMMARY = re.compile(
    r"^=* ?(\d+ (failed|passed|errors?|skipped|xfailed|xpassed|deselected|warnings?)|no tests ran)\b.* in "
    r"\d+(\.\d+)?s\b.*$"
)
PYTEST_HEADER = re.compile(r"^=+ .+ =+$")
PYTEST_FAILURES = re.compile(r"^=+ (FAILURES|ERRORS) =+$")
PYTEST_CASE = re.compile(r"^_{2,} .+ _{2,}$")


def read_pytest(lines: list[str]) -> tuple[list[str], list[str]]:
    summary = [line for line in lines if PYTEST_SUMMARY.match(line)][-1:]
    blocks: list[str] = []
    for index, line in enumerate(lines):
        if PYTEST_FAILURES.match(line):
            body = region(lines[index:], PYTEST_FAILURES, PYTEST_HEADER)[1:]
            blocks += split_blocks(body, PYTEST_CASE)
    return blocks, summary


VITEST_START = re.compile(r"^\s*⎯+ (Failed Tests|Failed Suites)\b")
VITEST_END = re.compile(r"^\s*Test Files\b")
VITEST_CASE = re.compile(r"^\s*FAIL\s")
VITEST_SUMMARY = re.compile(r"^\s*(Test Files|Tests|Duration)\s")


def read_vitest(lines: list[str]) -> tuple[list[str], list[str]]:
    blocks = split_blocks(region(lines, VITEST_START, VITEST_END), VITEST_CASE)
    return blocks, [line.strip() for line in lines if VITEST_SUMMARY.match(line)]


JEST_START = re.compile(r"^\s*● (?!Console)")
JEST_END = re.compile(r"^(Summary of all failing tests|Test Suites:)")
JEST_SUMMARY = re.compile(r"^(Test Suites|Tests|Snapshots|Time):\s")


def read_jest(lines: list[str]) -> tuple[list[str], list[str]]:
    blocks = split_blocks(region(lines, JEST_START, JEST_END), JEST_START)
    return blocks, [line for line in lines if JEST_SUMMARY.match(line)]


GO_FAIL = re.compile(r"^--- FAIL: (\S+)")
GO_PACKAGE = re.compile(r"^(ok|FAIL)\s+\S+\s+.*\d+(\.\d+)?s\b")
GO_PANIC = re.compile(r"^panic: ")
GO_PANIC_END = re.compile(r"^(FAIL|exit status)\b")


def read_go(lines: list[str]) -> tuple[list[str], list[str]]:
    blocks: list[str] = []
    for index, line in enumerate(lines):
        found = GO_FAIL.match(line)
        if found:
            starts = (back for back in range(index, -1, -1) if lines[back].split() == ["===", "RUN", found[1]])
            start = next(starts, index)
            end = index + 1
            while end < len(lines) and lines[end][:1] in (" ", "\t"):
                end += 1
            blocks.append("\n".join(lines[start:end]).strip("\n"))
        elif GO_PANIC.match(line):
            ends = (ahead for ahead in range(index + 1, len(lines)) if GO_PANIC_END.match(lines[ahead]))
            blocks.append("\n".join(lines[index : next(ends, len(lines))]).strip("\n"))
    return blocks, [line for line in lines if GO_PACKAGE.match(line)]


CARGO_LIST = re.compile(r"^failures:$")
CARGO_CASE = re.compile(r"^---- .+ ----$")
CARGO_RESULT = re.compile(r"^test result: ")


def read_cargo(lines: list[str]) -> tuple[list[str], list[str]]:
    # The first `failures:` opens the details, the second one the list of names that follows them.
    details = region(lines, CARGO_CASE, CARGO_LIST)
    summary = [line for line in lines if CARGO_RESULT.match(line) or line.startswith("error: test failed")]
    return split_blocks(details, CARGO_CASE), summary


DOTNET_FAIL = re.compile(r"^\s+Failed \S.*\[\d")
DOTNET_NEXT = re.compile(r"^(\s+(Failed|Passed|Skipped) \S.*\[\d|(Passed|Failed)!\s|Test Run |\s*Total tests:)")
DOTNET_SUMMARY = re.compile(r"^((Passed|Failed)!\s+- |Test Run (Successful|Failed))")


def read_dotnet(lines: list[str]) -> tuple[list[str], list[str]]:
    blocks: list[list[str]] = []
    open_block = False
    for line in lines:
        if DOTNET_FAIL.match(line):
            blocks.append([line])
            open_block = True
        elif DOTNET_NEXT.match(line):
            open_block = False
        elif open_block:
            blocks[-1].append(line)
    return ["\n".join(block).strip("\n") for block in blocks], [line for line in lines if DOTNET_SUMMARY.match(line)]


READERS: dict[TestRunner, Reader] = {
    "pytest": read_pytest,
    "vitest": read_vitest,
    "jest": read_jest,
    "go": read_go,
    "cargo": read_cargo,
    "dotnet": read_dotnet,
}
def essentials(runner: TestRunner, output: str, exit_code: int | None) -> str | None:
    """A failing run: the failures with their tracebacks and the summary. A passing run: the summary.
    None when the expected shape is not found."""
    lines = ANSI.sub("", output).replace("\r\n", "\n").replace("\r", "\n").split("\n")
    blocks, summary = READERS[runner](lines)
    if not summary:
        return None
    if exit_code == 0:
        return joined_summary(summary) if not blocks else None
    return assemble(blocks, summary) if blocks else None
