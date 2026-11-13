"""Setting models: a setting's value and where it comes from."""

from typing import Any, Literal

from pydantic import BaseModel


SettingSource = Literal["default", "global", "environment", "project"]


class SettingOption(BaseModel):
    value: str
    label: str


class SettingOut(BaseModel):
    key: str
    label: str
    description: str
    # Fixed choices for this setting; empty when the value is free or comes from elsewhere (such as the models).
    options: list[SettingOption]
    value: Any
    source: SettingSource
    inherited_value: Any
    inherited_source: SettingSource
    # What is stored at this level itself; null when the level inherits.
    level_value: Any = None


class SettingValue(BaseModel):
    value: Any
