"""The registry of all settings, keyed by setting key."""

from app.core.settings.definitions_general import GENERAL_DEFINITIONS
from app.core.settings.definitions_operations import OPERATIONS_DEFINITIONS
from app.core.settings.setting_types import SettingDefinition


SETTING_DEFINITIONS: dict[str, SettingDefinition] = {
    definition.key: definition
    for definition in (*GENERAL_DEFINITIONS, *OPERATIONS_DEFINITIONS)
}
