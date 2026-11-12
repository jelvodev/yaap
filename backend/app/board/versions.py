"""A version file, `versions/<name>.md`: front matter (`name`, `state`, `budget_usd`, `created`), then notes and an
optional `## Acceptance criteria` section. Read and written like a card (`app.board.card`): reading never raises on
content, writing is deterministic."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from pydantic import Field, ValidationError, field_validator

from app.board.card import (
    MAX_SECTION_CHARS,
    FrontMatterError,
    FrozenModel,
    as_utc,
    closed_section,
    contains_heading,
    join_at_headings,
    render_front_matter,
    split_at_headings,
    split_front_matter,
    too_large,
    validation_reason,
)
from app.board.errors import InvalidCard
from app.board.paths import MAX_FILE_BYTES, VERSION_NAME, classify

VersionState = Literal["open", "released"]
FRONT_KEYS = ("name", "state", "budget_usd", "created")
CRITERIA = "## Acceptance criteria"


class Version(FrozenModel):
    name: str
    state: VersionState = "open"
    budget_usd: float | None = None
    created: datetime
    extra: dict[str, object] = Field(default_factory=dict)
    notes: str = Field(default="", max_length=MAX_SECTION_CHARS)
    acceptance_criteria: str = Field(default="", max_length=MAX_SECTION_CHARS)

    @field_validator("name")
    @classmethod
    def version_name(cls, value: str) -> str:
        if VERSION_NAME.fullmatch(value) is None:
            raise ValueError("A version name has 1 to 40 letters, digits, dots, dashes or underscores.")
        return value

    @field_validator("budget_usd", mode="before")
    @classmethod
    def budget_is_number(cls, value: object) -> object:
        if value is not None and (isinstance(value, bool) or not isinstance(value, int | float)):
            raise ValueError("The budget is a number.")
        return value

    @field_validator("budget_usd")
    @classmethod
    def budget_in_cents(cls, value: float | None) -> float | None:
        if value is None:
            return None
        if not value >= 0 or value == float("inf"):
            raise ValueError("The budget is zero or more.")
        return round(value, 2)

    @field_validator("created")
    @classmethod
    def in_utc(cls, value: datetime) -> datetime:
        return as_utc(value)

    @field_validator("notes", "acceptance_criteria")
    @classmethod
    def no_criteria_heading(cls, value: str) -> str:
        if contains_heading(value, [CRITERIA]):
            raise ValueError("The text cannot hold the acceptance criteria heading outside fenced code.")
        return value

    @field_validator("extra")
    @classmethod
    def unknown_keys_only(cls, value: dict[str, object]) -> dict[str, object]:
        if any(key in FRONT_KEYS for key in value):
            raise ValueError("Extra keys cannot repeat a version field.")
        return value


@dataclass(frozen=True)
class BrokenVersion:
    """A version file that cannot be read: shown with its error, never rewritten by YAAP."""

    path: str
    name: str | None
    error: str
    text: str | None = None


def validate_version(data: Mapping[str, object]) -> Version:
    """A version from field values; raises InvalidCard with a safe sentence."""
    try:
        return Version.model_validate(dict(data))
    except ValidationError as error:
        raise InvalidCard(validation_reason(error)) from error


def parse_version(text: str, path: str) -> Version | BrokenVersion:
    """The version in a file's text; a BrokenVersion for anything else. Never raises on content."""
    found = classify(path)
    name = found.version if found is not None and found.kind == "version" else None
    if too_large(text):
        return BrokenVersion(path, name, f"The file is larger than {MAX_FILE_BYTES} bytes.")
    text = text.replace("\r\n", "\n")
    if name is None:
        return BrokenVersion(path, None, "This is not a version file name.", text)
    try:
        front, body = split_front_matter(text)
    except FrontMatterError as error:
        return BrokenVersion(path, name, str(error), text)
    if front.get("name") != name:
        return BrokenVersion(path, name, f"The name in the file is not {name}, its file name.", text)
    fields = {key: front[key] for key in FRONT_KEYS if key in front}
    extra = {key: value for key, value in front.items() if key not in FRONT_KEYS}
    sections = split_at_headings(body, {CRITERIA: "acceptance_criteria"}, "notes")
    try:
        return Version.model_validate({**fields, "extra": extra, **sections})
    except ValidationError as error:
        return BrokenVersion(path, name, validation_reason(error), text)


def render_version(version: Version) -> str:
    """The version's file text: front matter in the fixed key order, unknown keys sorted, the notes, the criteria."""
    data: dict[str, object] = {
        "name": version.name,
        "state": version.state,
        "budget_usd": version.budget_usd,
        "created": version.created,
    }
    for key in sorted(version.extra):
        data[key] = version.extra[key]
    notes = closed_section(version.notes)
    parts = [f"{notes}\n"] if notes else []
    criteria = join_at_headings([(CRITERIA, version.acceptance_criteria)], "")
    if criteria:
        parts.append(criteria)
    body = "\n".join(parts)
    text = f"{render_front_matter(data)}\n{body}"
    if too_large(text):
        raise InvalidCard(f"The version would be larger than {MAX_FILE_BYTES} bytes.")
    return text
