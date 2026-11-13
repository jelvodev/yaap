"""Environment models: the isolated workspaces projects and conversations belong to."""

from pydantic import BaseModel, Field


HEX_COLOUR = r"^#[0-9A-Fa-f]{6}$"


class EnvironmentOut(BaseModel):
    id: int
    name: str
    colour: str


class EnvironmentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    colour: str = Field(pattern=HEX_COLOUR)


class EnvironmentUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    colour: str | None = Field(default=None, pattern=HEX_COLOUR)
