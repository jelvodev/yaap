"""Setting definitions, resolved values and the key naming of a provider connection."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from app.providers.registry import ProviderRegistry


Level = Literal["global", "environment", "project"]
Source = Literal["default", "global", "environment", "project"]
Validator = Callable[[Any, ProviderRegistry], str | None]


@dataclass(frozen=True)
class SettingOption:
    value: str
    label: str


def no_extra_validation(_value: Any, _providers: ProviderRegistry) -> str | None:
    return None


@dataclass(frozen=True)
class SettingDefinition:
    key: str
    label: str
    description: str
    levels: frozenset[str]
    default: Any
    validate: Validator = no_extra_validation
    # Fixed choices. When set, a value must be one of them; for a merged dictionary, the keys it can hold.
    options: tuple[SettingOption, ...] = ()
    # A dictionary merged key by key over the levels, instead of the nearest level replacing the whole value.
    merge: bool = False

    def check(self, value: Any, providers: ProviderRegistry) -> str | None:
        if self.options and not self.merge and value not in {option.value for option in self.options}:
            return f"Choose one of: {', '.join(option.label for option in self.options)}."
        return self.validate(value, providers)


@dataclass(frozen=True)
class ResolvedSetting:
    definition: SettingDefinition
    value: Any
    source: Source
    inherited_value: Any
    inherited_source: Source
    # What is stored at the requested level itself, or None when that level inherits.
    level_value: Any = None


def connection_setting_key(provider: str) -> str:
    return f"{provider}_connection"
