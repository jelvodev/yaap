"""Project models: the project itself, moving it, and its memory file."""

from datetime import datetime

from pydantic import BaseModel, Field


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    # Clone this repository into the project folder; empty for a new, empty repository.
    repository_url: str | None = Field(default=None, max_length=400)
    # From a template: create the GitHub repository repository_name from this template repository
    # (owner/name), then clone it.
    template: str | None = Field(default=None, max_length=200, pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
    repository_name: str | None = Field(default=None, max_length=100, pattern=r"^[A-Za-z0-9._-]+$")
    private: bool = True


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    # Send these (null to clear) to change what new conversations in the project start with.
    default_agent: str | None = None
    default_preset: str | None = None


class ProjectMove(BaseModel):
    environment_id: int


class ProjectMoveOut(BaseModel):
    project: ProjectOut
    # Defaults that do not exist in the new environment and were cleared, for example "preset Deep".
    cleared: list[str]


class ProjectMemoryOut(BaseModel):
    content: str
    # The empty template when the project has no memory file yet.
    exists: bool


class ProjectMemoryIn(BaseModel):
    content: str = Field(max_length=65_536)


class MemoryEntryOut(BaseModel):
    # Its line and a hash of its text; an edit of a memory that changed meanwhile is refused with 409.
    id: str
    section: str
    text: str


class MemoryEntriesOut(BaseModel):
    # The standard sections first, then any other section the file has.
    sections: list[str]
    entries: list[MemoryEntryOut]
    # After adding an entry: an existing entry that says nearly the same, as a warning.
    similar: str | None = None


class MemoryEntryIn(BaseModel):
    section: str = Field(min_length=1, max_length=100)
    text: str = Field(min_length=1, max_length=1000)


class MemoryEntryEdit(BaseModel):
    text: str = Field(min_length=1, max_length=1000)


class ProjectOut(BaseModel):
    id: int
    environment_id: int
    name: str
    # Working folder relative to the workspace, for example "work/website".
    folder: str
    repository_url: str | None
    default_agent: str | None = None
    default_preset: str | None = None
    created_at: datetime
    # Whether the project opens its board (the project_board setting); off, a click only narrows the conversations.
    board_enabled: bool = True
