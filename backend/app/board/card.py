"""A card file, `cards/<id>.md`: YAML front matter in a fixed key order, then the body in fixed sections.

Reading never raises on content: a file that is not a valid card becomes a `BrokenCard` with its error, which YAAP
shows and never rewrites. Writing is deterministic (`render_card`), so reading and writing a card again gives the
same bytes. The front matter is read with `yaml.safe_load` only. Cards can be written by anyone who can write the
branch, so their text is data, never instructions."""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, Strict, ValidationError, field_validator, model_validator

from app.board.errors import InvalidCard
from app.board.paths import MAX_FILE_BYTES, VERSION_NAME, classify

Column = Literal["inbox", "idea", "design", "plan", "build", "review", "done"]
COLUMNS: tuple[Column, ...] = ("inbox", "idea", "design", "plan", "build", "review", "done")
Kind = Literal["bug", "feature", "refactor", "chore"]
KINDS: tuple[Kind, ...] = ("bug", "feature", "refactor", "chore")
OriginType = Literal["user", "chat", "agent", "trigger", "issue", "review", "learning"]
SectionName = Literal["description", "definition_of_done", "specification", "plan", "evidence"]

# The body's sections in file order with their exact headings.
SECTIONS: tuple[tuple[SectionName, str], ...] = (
    ("description", "## Description"),
    ("definition_of_done", "## Definition of done"),
    ("specification", "## Specification"),
    ("plan", "## Plan"),
    ("evidence", "## Evidence"),
)
FRONT_KEYS = (
    "id",
    "title",
    "kind",
    "column",
    "version",
    "origin",
    "waits_for",
    "questions",
    "cost_usd",
    "links",
    "created",
    "updated",
)
MAX_SECTION_CHARS = 50_000
MAX_TITLE = 200
MAX_WAITS = 20
MAX_QUESTIONS = 20
MAX_CONVERSATIONS = 50

StrictId = Annotated[int, Strict(), Field(ge=1)]
SectionText = Annotated[str, Field(max_length=MAX_SECTION_CHARS)]


# --- Front matter and sections, shared with versions ---------------------------------------------------------------

FRONT_MATTER = re.compile(r"---[ \t]*\n(?:(.*?)\n)?---[ \t]*(?:\n|$)", re.DOTALL)
FENCE_OPEN = re.compile(r" {0,3}(`{3,}|~{3,})(.*)")


class FrontMatterError(ValueError):
    """The file has no readable front matter; the message says why and is safe to show."""


def split_front_matter(text: str) -> tuple[dict[str, object], str]:
    """The front matter as a mapping and the body after it. Expects `\\n` line endings."""
    found = FRONT_MATTER.match(text)
    if found is None:
        raise FrontMatterError("The file does not start with front matter between --- lines.")
    try:
        data = yaml.safe_load(found.group(1) or "")
    except yaml.YAMLError as error:
        raise FrontMatterError(yaml_problem(error)) from error
    if not isinstance(data, dict):
        raise FrontMatterError("The front matter is not a mapping of keys to values.")
    if not all(isinstance(key, str) for key in data):
        raise FrontMatterError("Every key in the front matter must be text.")
    return dict(data), text[found.end() :]


def yaml_problem(error: yaml.YAMLError) -> str:
    """What is wrong and on which line of the front matter, without quoting the file's text."""
    if isinstance(error, yaml.MarkedYAMLError) and error.problem_mark is not None:
        return f"The front matter is not valid YAML (line {error.problem_mark.line + 2}): {error.problem}."
    return "The front matter is not valid YAML."


class BoardDumper(yaml.SafeDumper):
    """YAML for board files: a moment in time as an unquoted UTC timestamp ending in Z."""


def represent_datetime(dumper: yaml.SafeDumper, value: datetime) -> yaml.ScalarNode:
    text = value.isoformat() if value.tzinfo is None else utc_text(value)
    return dumper.represent_scalar("tag:yaml.org,2002:timestamp", text)


BoardDumper.add_representer(datetime, represent_datetime)


def render_front_matter(data: Mapping[str, object]) -> str:
    """The front matter block, keys in the order given, ending with the closing `---` line."""
    dumped = yaml.dump(
        dict(data),
        Dumper=BoardDumper,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
        width=1_000_000,
    )
    return f"---\n{dumped}---\n"


def utc_text(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def as_utc(value: datetime) -> datetime:
    """A moment in UTC to the second; a moment without a zone is taken as UTC."""
    moment = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    return moment.replace(microsecond=0)


def closing_fence(line: str, fence: str) -> bool:
    stripped = line.strip()
    indent = len(line) - len(line.lstrip(" "))
    return indent <= 3 and stripped == fence[0] * len(stripped) and len(stripped) >= len(fence)


def opening_fence(line: str) -> str | None:
    found = FENCE_OPEN.fullmatch(line)
    if found is None or (found.group(1)[0] == "`" and "`" in found.group(2)):
        return None
    return found.group(1)


def unfenced_lines(text: str) -> list[tuple[str, bool]]:
    """Each line of text with whether it is outside fenced code (fence lines themselves count as inside)."""
    fence: str | None = None
    lines: list[tuple[str, bool]] = []
    for line in text.split("\n"):
        if fence is not None:
            lines.append((line, False))
            if closing_fence(line, fence):
                fence = None
            continue
        fence = opening_fence(line)
        lines.append((line, fence is None))
    return lines


def open_fence_at_end(text: str) -> str | None:
    """The fence still open at the end of text, or None."""
    fence: str | None = None
    for line in text.split("\n"):
        if fence is None:
            fence = opening_fence(line)
        elif closing_fence(line, fence):
            fence = None
    return fence


def trim_blank_lines(text: str) -> str:
    lines = text.split("\n")
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines)


def split_at_headings(body: str, headings: Mapping[str, str], first: str) -> dict[str, str]:
    """Split body at the exact heading lines (heading -> key) outside fenced code. Text before the first heading
    belongs to `first`; a heading that comes again adds to its section."""
    chunks: dict[str, list[list[str]]] = {key: [] for key in (first, *headings.values())}
    current: list[str] = []
    chunks[first].append(current)
    for line, outside in unfenced_lines(body):
        key = headings.get(line.rstrip()) if outside else None
        if key is not None:
            current = []
            chunks[key].append(current)
            continue
        current.append(line)
    return {
        key: "\n\n".join(text for text in (trim_blank_lines("\n".join(lines)) for lines in found) if text)
        for key, found in chunks.items()
    }


def closed_section(raw: str) -> str:
    """A section's text without leading and trailing blank lines, with a fence left open at its end closed."""
    text = trim_blank_lines(raw)
    fence = open_fence_at_end(text)
    return text if fence is None else f"{text}\n{fence}"


def join_at_headings(sections: Sequence[tuple[str, str]], always: str) -> str:
    """The body for (heading, text) pairs: empty sections are left out except `always`. A section that ends inside
    fenced code gets its closing fence, so the next heading is read as a heading again."""
    parts: list[str] = []
    for heading, raw in sections:
        text = closed_section(raw)
        if not text and heading != always:
            continue
        parts.append(f"{heading}\n\n{text}\n" if text else f"{heading}\n")
    return "\n".join(parts)


def contains_heading(text: str, headings: Sequence[str]) -> bool:
    return any(outside and line.rstrip() in headings for line, outside in unfenced_lines(text))


def validation_reason(error: ValidationError) -> str:
    """A short, safe sentence for the first problem: where it is and what is wrong, never the value."""
    first = error.errors()[0]
    where = ".".join(str(part) for part in first["loc"])
    message = str(first["msg"]).removeprefix("Value error, ")
    return f"{where}: {message}" if where else message


# --- The card ------------------------------------------------------------------------------------------------------


class FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Origin(FrozenModel):
    """Where the card came from; `ref` points at it (a conversation, a review, an issue)."""

    type: OriginType = "user"
    ref: str | None = Field(default=None, max_length=300)


class Question(FrozenModel):
    asked: datetime
    text: str = Field(min_length=1, max_length=1000)
    answer: str | None = Field(default=None, max_length=4000)

    @field_validator("asked")
    @classmethod
    def in_utc(cls, value: datetime) -> datetime:
        return as_utc(value)


class CardLinks(FrozenModel):
    conversations: list[StrictId] = Field(default_factory=list, max_length=MAX_CONVERSATIONS)
    plan_id: StrictId | None = None
    branch: str | None = Field(default=None, min_length=1, max_length=255, pattern=r"^[^\n\r]*$")
    pull_request: StrictId | None = None
    review_id: StrictId | None = None


class Card(FrozenModel):
    id: StrictId
    title: str
    kind: Kind = "feature"
    column: Column = "inbox"
    version: str | None = None
    origin: Origin = Field(default_factory=Origin)
    waits_for: list[StrictId] = Field(default_factory=list, max_length=MAX_WAITS)
    questions: list[Question] = Field(default_factory=list, max_length=MAX_QUESTIONS)
    cost_usd: float = 0.0
    links: CardLinks = Field(default_factory=CardLinks)
    created: datetime
    updated: datetime
    # Front matter keys YAAP does not know, kept and written back sorted.
    extra: dict[str, object] = Field(default_factory=dict)
    description: SectionText = ""
    definition_of_done: SectionText = ""
    specification: SectionText = ""
    plan: SectionText = ""
    evidence: SectionText = ""

    @field_validator("title")
    @classmethod
    def one_line_title(cls, value: str) -> str:
        title = value.strip()
        if "\n" in title or "\r" in title:
            raise ValueError("The title is one line.")
        if not 1 <= len(title) <= MAX_TITLE:
            raise ValueError(f"The title has 1 to {MAX_TITLE} characters.")
        return title

    @field_validator("version")
    @classmethod
    def version_name(cls, value: str | None) -> str | None:
        if value is not None and VERSION_NAME.fullmatch(value) is None:
            raise ValueError("A version name has 1 to 40 letters, digits, dots, dashes or underscores.")
        return value

    @field_validator("cost_usd", mode="before")
    @classmethod
    def cost_is_number(cls, value: object) -> object:
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise ValueError("The cost is a number.")
        return value

    @field_validator("cost_usd")
    @classmethod
    def cost_in_cents(cls, value: float) -> float:
        if not value >= 0 or value == float("inf"):
            raise ValueError("The cost is zero or more.")
        return round(value, 2)

    @field_validator("created", "updated")
    @classmethod
    def in_utc(cls, value: datetime) -> datetime:
        return as_utc(value)

    @field_validator("description", "definition_of_done", "specification", "plan", "evidence")
    @classmethod
    def no_section_headings(cls, value: str) -> str:
        if contains_heading(value, [heading for _, heading in SECTIONS]):
            raise ValueError("A section cannot hold another section's heading outside fenced code.")
        return value

    @field_validator("extra")
    @classmethod
    def unknown_keys_only(cls, value: dict[str, object]) -> dict[str, object]:
        if any(key in FRONT_KEYS for key in value):
            raise ValueError("Extra keys cannot repeat a card field.")
        return value

    @model_validator(mode="after")
    def waits_for_others(self) -> "Card":
        if len(set(self.waits_for)) != len(self.waits_for):
            raise ValueError("waits_for lists a card twice.")
        if self.id in self.waits_for:
            raise ValueError("A card cannot wait for itself.")
        return self


@dataclass(frozen=True)
class BrokenCard:
    """A card file that cannot be read as a card: shown with its error, never rewritten or deleted by YAAP."""

    path: str
    card_id: int | None
    error: str
    # The file's text, or None when it was too large to read.
    text: str | None = None


@dataclass(frozen=True)
class ChecklistItem:
    done: bool
    text: str


def validate_card(data: Mapping[str, object]) -> Card:
    """A card from field values; raises InvalidCard with a safe sentence."""
    try:
        return Card.model_validate(dict(data))
    except ValidationError as error:
        raise InvalidCard(validation_reason(error)) from error


def revise(card: Card, changes: Mapping[str, object]) -> Card:
    """The card with changes applied and checked again; raises InvalidCard."""
    return validate_card({**card.model_dump(), **changes})


def split_sections(body: str) -> dict[SectionName, str]:
    headings = {heading: key for key, heading in SECTIONS}
    found = split_at_headings(body, headings, "description")
    return {key: found[key] for key, _ in SECTIONS}


def join_sections(sections: Mapping[SectionName, str]) -> str:
    return join_at_headings([(heading, sections.get(key, "")) for key, heading in SECTIONS], "## Description")


CHECKLIST_LINE = re.compile(r"\s*[-*+] \[([ xX])\] (.*)")


def parse_checklist(text: str) -> list[ChecklistItem]:
    """The task-list items (`- [ ] ...`, `- [x] ...`) of a section, outside fenced code."""
    items: list[ChecklistItem] = []
    for line, outside in unfenced_lines(text.replace("\r\n", "\n")):
        found = CHECKLIST_LINE.fullmatch(line) if outside else None
        if found is not None:
            items.append(ChecklistItem(found.group(1) != " ", found.group(2).strip()))
    return items


def too_large(text: str) -> bool:
    return len(text.encode("utf-8")) > MAX_FILE_BYTES


def parse_card(text: str, path: str) -> Card | BrokenCard:
    """The card in a file's text; a BrokenCard for anything that is not a valid card. Never raises on content."""
    found = classify(path)
    card_id = found.card_id if found is not None and found.kind == "card" else None
    if too_large(text):
        return BrokenCard(path, card_id, f"The file is larger than {MAX_FILE_BYTES} bytes.")
    text = text.replace("\r\n", "\n")
    if card_id is None:
        return BrokenCard(path, None, "This is not a card file name.", text)
    try:
        front, body = split_front_matter(text)
    except FrontMatterError as error:
        return BrokenCard(path, card_id, str(error), text)
    if front.get("id") != card_id or isinstance(front.get("id"), bool):
        return BrokenCard(path, card_id, f"The id in the file is not {card_id}, its file name.", text)
    fields = {key: front[key] for key in FRONT_KEYS if key in front}
    extra = {key: value for key, value in front.items() if key not in FRONT_KEYS}
    try:
        return Card.model_validate({**fields, "extra": extra, **split_sections(body)})
    except ValidationError as error:
        return BrokenCard(path, card_id, validation_reason(error), text)


def render_card(card: Card) -> str:
    """The card's file text: front matter in the fixed key order, unknown keys sorted, then the sections."""
    data: dict[str, object] = {
        "id": card.id,
        "title": card.title,
        "kind": card.kind,
        "column": card.column,
        "version": card.version,
        "origin": {"type": card.origin.type, "ref": card.origin.ref},
        "waits_for": list(card.waits_for),
        "questions": [
            {"asked": question.asked, "text": question.text, "answer": question.answer} for question in card.questions
        ],
        "cost_usd": card.cost_usd,
        "links": {
            "conversations": list(card.links.conversations),
            "plan_id": card.links.plan_id,
            "branch": card.links.branch,
            "pull_request": card.links.pull_request,
            "review_id": card.links.review_id,
        },
        "created": card.created,
        "updated": card.updated,
    }
    for key in sorted(card.extra):
        data[key] = card.extra[key]
    sections: dict[SectionName, str] = {key: getattr(card, key) for key, _ in SECTIONS}
    text = f"{render_front_matter(data)}\n{join_sections(sections)}"
    if too_large(text):
        raise InvalidCard(f"The card would be larger than {MAX_FILE_BYTES} bytes.")
    return text
