"""Eval cases from the project's Blueprint layer: `.blueprint/evals/<name>.md`.

A case file has the Blueprint's file format (YAML front matter between --- lines, then Markdown), read with the
Blueprint's own parser. The body is the prompt; the front matter holds the start state, the agent and preset, the
tools allowed without asking, the calls the play approves after the run read outside content, the budget and the
checks. Validation is a pure function of the file's text: it collects every problem instead of stopping at the first,
so one look at a broken case shows all that is wrong.
"""

import os
import re
from pathlib import Path

from app.agent.policy import CaseApprovals, check_case_approvals
from app.blueprint.layers import MAX_FILE_BYTES, PROJECT_LAYER_FOLDER, BlueprintError, parse_document
from app.evals.models import (
    Budget,
    CaseFile,
    Check,
    CommandCheck,
    EvalCase,
    FileCheck,
    RubricCheck,
    StartState,
)
from app.evals.units import UnitError, parse_cost, parse_seconds, parse_steps, parse_tokens
from app.targets.safe_root import SafeRoot

EVALS_FOLDER = "evals"
CASE_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,59}$")
COMMIT = re.compile(r"^[0-9a-f]{7,40}$")
TOOL_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
MAX_PRESET_CHARS = 40
MAX_SUMMARY_CHARS = 300
MAX_CHECKS = 50
DEFAULT_COMMAND_TIMEOUT = 600.0
MAX_COMMAND_TIMEOUT = 3600.0

FRONT_MATTER_KEYS = ("summary", "start", "agent", "preset", "allowed_tools", "approve", "budget", "checks")
FILE_CONDITIONS = ("contains", "not_contains", "matches", "not_matches")
CHECK_KEYS: dict[str, tuple[str, ...]] = {
    "command": ("kind", "name", "run", "timeout"),
    "file": ("kind", "name", "path", "exists", *FILE_CONDITIONS),
    "rubric": ("kind", "name", "rubric", "files"),
}
BUDGET_KEYS = ("steps", "tokens", "cost", "time")


def evals_dir(project_folder: Path) -> Path:
    return project_folder / PROJECT_LAYER_FOLDER / EVALS_FOLDER


def read_cases(project_folder: Path, known_tools: frozenset[str] | None = None) -> list[CaseFile]:
    """Every case file of the project, valid or not, by name. A file that cannot be read is listed with why."""
    found: list[CaseFile] = []
    try:
        with SafeRoot(project_folder) as root:
            entries = sorted(root.entries(f"{PROJECT_LAYER_FOLDER}/{EVALS_FOLDER}", recursive=False))
            for relative, _info in entries:
                name = Path(relative).name
                if not name.endswith(".md"):
                    continue
                stem = Path(name).stem
                try:
                    descriptor = root.open_file(relative)
                    with os.fdopen(descriptor, "rb") as file:
                        opened = os.fstat(file.fileno())
                        if opened.st_size > MAX_FILE_BYTES:
                            found.append(
                                CaseFile(stem, relative, None, (f"The file is larger than {MAX_FILE_BYTES} bytes.",))
                            )
                            continue
                        data = file.read(MAX_FILE_BYTES + 1)
                    if len(data) > MAX_FILE_BYTES:
                        found.append(CaseFile(stem, relative, None, (f"The file is larger than {MAX_FILE_BYTES} bytes.",)))
                        continue
                    text = data.decode("utf-8")
                except (OSError, UnicodeDecodeError) as error:
                    found.append(CaseFile(stem, relative, None, (f"The file cannot be read: {error}",)))
                    continue
                found.append(parse_case(stem, text, relative, known_tools))
    except FileNotFoundError:
        return []
    except OSError:
        return []
    return found


def parse_case(name: str, text: str, path: str = "", known_tools: frozenset[str] | None = None) -> CaseFile:
    """The case in a file's text, or every problem with it. known_tools, when given, turns unknown tool names into
    warnings (tools of outside MCP servers are only known while their server runs)."""
    path = path or f"{PROJECT_LAYER_FOLDER}/{EVALS_FOLDER}/{name}.md"
    problems: list[str] = []
    if not CASE_NAME.match(name):
        problems.append("The file name must be lowercase letters, digits and hyphens, at most 60, ending in .md.")
    try:
        document = parse_document(text)
    except BlueprintError as error:
        return CaseFile(name, path, None, (*problems, str(error)))
    front = document.front_matter
    unknown = sorted(str(key) for key in front if key not in FRONT_MATTER_KEYS)
    if unknown:
        problems.append(f"Unknown keys in the front matter: {', '.join(unknown)}. Use {', '.join(FRONT_MATTER_KEYS)}.")
    prompt = document.body.strip()
    if not prompt:
        problems.append("The prompt is missing: write it as the body, below the front matter.")
    start = parse_start(front.get("start"), problems)
    budget = parse_budget(front.get("budget"), problems)
    checks = parse_checks(front.get("checks"), problems)
    if not checks and budget is None and "checks" not in front:
        problems.append("The case has nothing to judge it by: add checks, a budget, or both.")
    case = EvalCase(
        name=name,
        prompt=prompt,
        start=start or StartState(),
        checks=checks,
        summary=optional_text(front, "summary", MAX_SUMMARY_CHARS, problems) or "",
        agent=optional_name(front, "agent", problems),
        preset=optional_text(front, "preset", MAX_PRESET_CHARS, problems),
        allowed_tools=parse_tools(front.get("allowed_tools"), problems),
        approve=parse_approve(front, problems),
        budget=budget,
    )
    if problems or start is None:
        return CaseFile(name, path, None, tuple(problems))
    return CaseFile(name, path, case, (), tool_warnings(case, known_tools))


def parse_start(value: object, problems: list[str]) -> StartState | None:
    """`start: {commit: <sha>}` or `start: {checkpoint: <id>}`, exactly one."""
    if value is None:
        problems.append("The start state is missing: add start: {commit: <sha>} or start: {checkpoint: <id>}.")
        return None
    if not isinstance(value, dict) or set(value) - {"commit", "checkpoint"} or len(value) != 1:
        problems.append("start must name exactly one of commit or checkpoint, like start: {commit: 3f2a1bc}.")
        return None
    if "commit" in value:
        commit = str(value["commit"]).strip().lower()
        if not COMMIT.match(commit):
            problems.append("start.commit must be a commit hash of 7 to 40 hexadecimal characters, not a branch name.")
            return None
        return StartState(commit=commit)
    checkpoint = value["checkpoint"]
    if isinstance(checkpoint, bool) or not isinstance(checkpoint, int) or checkpoint <= 0:
        problems.append("start.checkpoint must be a checkpoint's number.")
        return None
    return StartState(checkpoint=checkpoint)


def optional_text(front: dict[str, object], key: str, limit: int, problems: list[str]) -> str | None:
    value = front.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > limit:
        problems.append(f"{key} must be text of at most {limit} characters.")
        return None
    return value.strip()


def optional_name(front: dict[str, object], key: str, problems: list[str]) -> str | None:
    value = optional_text(front, key, 60, problems)
    if value is not None and not CASE_NAME.match(value):
        problems.append(f"{key} must be the name of an agent file: lowercase letters, digits and hyphens.")
        return None
    return value


def parse_tools(value: object, problems: list[str]) -> tuple[str, ...] | None:
    if value is None:
        return None
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        problems.append("allowed_tools must be a list of tool names.")
        return None
    names = tuple(dict.fromkeys(item.strip() for item in value if item.strip()))
    wrong = [name for name in names if not TOOL_NAME.match(name)]
    if wrong:
        problems.append(f"allowed_tools has names that are not tool names: {', '.join(wrong)}.")
        return None
    return names


def parse_approve(front: dict[str, object], problems: list[str]) -> CaseApprovals | None:
    if "approve" not in front:
        return None
    approvals, found = check_case_approvals(front["approve"])
    problems.extend(found)
    return approvals


def tool_warnings(case: EvalCase, known_tools: frozenset[str] | None) -> tuple[str, ...]:
    if known_tools is None or case.allowed_tools is None:
        return ()
    unknown = sorted(set(case.allowed_tools) - known_tools)
    return (f"Unknown tools in allowed_tools: {', '.join(unknown)}.",) if unknown else ()


def parse_budget(value: object, problems: list[str]) -> Budget | None:
    """`budget:` with steps, tokens, cost and time, each with its unit (units.py)."""
    if value is None:
        return None
    if not isinstance(value, dict) or not value:
        problems.append(f"budget must name one or more of {', '.join(BUDGET_KEYS)}, each with a unit.")
        return None
    unknown = sorted(str(key) for key in value if key not in BUDGET_KEYS)
    if unknown:
        problems.append(f"budget has unknown parts: {', '.join(unknown)}. Use {', '.join(BUDGET_KEYS)}.")
    try:
        steps = parse_steps(value["steps"]) if "steps" in value else None
        tokens = parse_tokens(value["tokens"]) if "tokens" in value else None
        cost = parse_cost(value["cost"]) if "cost" in value else None
        seconds = parse_seconds(value["time"]) if "time" in value else None
    except UnitError as error:
        problems.append(f"budget: {error}.")
        return None
    return None if unknown else Budget(steps, tokens, cost, seconds)


def parse_checks(value: object, problems: list[str]) -> tuple[Check, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not value:
        problems.append("checks must be a list with at least one check.")
        return ()
    if len(value) > MAX_CHECKS:
        problems.append(f"A case has at most {MAX_CHECKS} checks.")
        return ()
    checks = [parse_check(f"checks[{index + 1}]", item, problems) for index, item in enumerate(value)]
    return tuple(check for check in checks if check is not None)


def parse_check(where: str, item: object, problems: list[str]) -> Check | None:
    if not isinstance(item, dict):
        problems.append(f"{where} must be a set of key: value pairs with a kind.")
        return None
    kind = item.get("kind")
    if kind not in CHECK_KEYS:
        problems.append(f"{where} has an unknown kind '{kind}'. Use {', '.join(CHECK_KEYS)}.")
        return None
    unknown = sorted(str(key) for key in item if key not in CHECK_KEYS[kind])
    if unknown:
        problems.append(f"{where} ({kind}) has unknown keys: {', '.join(unknown)}.")
        return None
    name = item.get("name")
    if name is not None and (not isinstance(name, str) or not name.strip()):
        problems.append(f"{where}.name must be text.")
        return None
    label = name.strip() if isinstance(name, str) else None
    if kind == "command":
        return parse_command_check(where, item, label, problems)
    if kind == "file":
        return parse_file_check(where, item, label, problems)
    return parse_rubric_check(where, item, label, problems)


def parse_command_check(where: str, item: dict[str, object], name: str | None, problems: list[str]) -> Check | None:
    run = item.get("run")
    if not isinstance(run, str) or not run.strip():
        problems.append(f"{where}.run must be the command to run.")
        return None
    timeout = DEFAULT_COMMAND_TIMEOUT
    if "timeout" in item:
        try:
            timeout = parse_seconds(item["timeout"])
        except UnitError as error:
            problems.append(f"{where}.timeout: {error}.")
            return None
        if timeout > MAX_COMMAND_TIMEOUT:
            problems.append(f"{where}.timeout can be at most 1 h.")
            return None
    return CommandCheck(name or f"command: {run.strip()}"[:120], run.strip(), timeout)


def parse_file_check(where: str, item: dict[str, object], name: str | None, problems: list[str]) -> Check | None:
    path = workspace_path(item.get("path"), f"{where}.path", problems)
    exists = item.get("exists", True)
    if not isinstance(exists, bool):
        problems.append(f"{where}.exists must be true or false.")
        return None
    texts = {key: text_items(item.get(key), f"{where}.{key}", problems) for key in FILE_CONDITIONS}
    conditions = {key: value for key, value in texts.items() if value is not None}
    if len(conditions) < len(FILE_CONDITIONS) or path is None:
        return None
    if exists and not any(conditions.values()):
        problems.append(f"{where} needs contains, not_contains, matches or not_matches, or exists: false.")
        return None
    if not exists and any(conditions.values()):
        problems.append(f"{where} with exists: false cannot also check the content.")
        return None
    for pattern in (*conditions["matches"], *conditions["not_matches"]):
        try:
            re.compile(pattern, re.MULTILINE)
        except re.error as error:
            problems.append(f"{where} has a regular expression that does not compile: {pattern!r} ({error}).")
            return None
    return FileCheck(
        name or f"file: {path}",
        path,
        exists,
        conditions["contains"],
        conditions["not_contains"],
        conditions["matches"],
        conditions["not_matches"],
    )


def parse_rubric_check(where: str, item: dict[str, object], name: str | None, problems: list[str]) -> Check | None:
    rubric = item.get("rubric")
    if not isinstance(rubric, str) or not rubric.strip():
        problems.append(f"{where}.rubric must say what a good outcome is.")
        return None
    paths = text_items(item.get("files"), f"{where}.files", problems)
    if paths is None:
        return None
    checked = [workspace_path(path, f"{where}.files", problems) for path in paths]
    if any(path is None for path in checked):
        return None
    first_line = rubric.strip().splitlines()[0]
    return RubricCheck(name or f"rubric: {first_line}"[:120], rubric.strip(), tuple(p for p in checked if p))


def text_items(value: object, where: str, problems: list[str]) -> tuple[str, ...] | None:
    """One text or a list of texts; None (with a problem) for anything else. Absent is an empty tuple."""
    if value is None:
        return ()
    items = [value] if isinstance(value, str) else value
    if not isinstance(items, list) or not all(isinstance(item, str) and item for item in items):
        problems.append(f"{where} must be text or a list of texts.")
        return None
    return tuple(items)


def workspace_path(value: object, where: str, problems: list[str]) -> str | None:
    """A relative path inside the workspace, with / as separator; never absolute, never up out of it, never .git."""
    if not isinstance(value, str) or not value.strip():
        problems.append(f"{where} must be a path in the project, like src/app.py.")
        return None
    path = value.strip()
    parts = path.split("/")
    if path.startswith("/") or "\\" in path or re.match(r"^[A-Za-z]:", path) or ".." in parts or ".git" in parts:
        problems.append(f"{where} must be a relative path inside the project, without .. or .git: {path!r}.")
        return None
    normalized = "/".join(part for part in parts if part not in ("", "."))
    if not normalized:
        problems.append(f"{where} must name a file, not the project folder itself.")
        return None
    return normalized
