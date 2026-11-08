"""Folder and setting checks for targets, without other dependencies so the settings can use them."""

import re
from typing import Any

WINDOWS_PATH = re.compile(r"^[A-Za-z]:[\\/]")


def target_folder_problem(folder: str) -> str | None:
    """A folder on a target must be absolute (C:\\... or /...) and cannot go up with '..'."""
    if not (WINDOWS_PATH.match(folder) or folder.startswith("/")):
        return f"{folder}: use an absolute folder, such as C:\\Users\\you\\Projects or /home/you/projects."
    if ".." in re.split(r"[\\/]", folder):
        return f"{folder}: a folder cannot go up with '..'."
    if len(folder) > 300 or "\n" in folder:
        return f"{folder}: use a single line of at most 300 characters."
    return None


def validate_target_setting(value: Any, _providers: Any = None) -> str | None:
    """The project setting target: {} for YAAP's container, {"id": <target>, "folder": <folder on it>}, or
    {"sandbox": true, "network": <bool>} for the project's sandbox."""
    if value == {}:
        return None
    if isinstance(value, dict) and "sandbox" in value:
        if value.get("sandbox") is not True or set(value) - {"sandbox", "network"} or not isinstance(value.get("network", False), bool):
            return 'Use {"sandbox": true, "network": false} for the project\'s sandbox.'
        return None
    if not isinstance(value, dict) or set(value) - {"id", "folder"}:
        return "Use {} for YAAP's container, or a target id and a folder on it."
    target_id = value.get("id")
    if not isinstance(target_id, int) or isinstance(target_id, bool) or target_id < 1:
        return "Choose a target."
    folder = value.get("folder", "")
    if not isinstance(folder, str) or not folder.strip():
        return "Name the project's folder on the target, such as C:\\Users\\you\\Projects\\site."
    return target_folder_problem(folder.strip())
