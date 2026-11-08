"""A plugin's manifest, `plugin.json` in its folder: read without importing any of the plugin's code.

    {
      "name": "hello",                       # the folder's name: lowercase letters, digits and _, a letter first
      "version": "1.0.0",
      "description": "What the plugin does.",
      "registers": {                         # everything register() adds; nothing else is accepted
        "tools": ["hello_time"],
        "providers": [],
        "credential_types": []
      },
      "page": "page/index.html",             # optional: a page shown in Settings, inside a subfolder
      "api": ["GET /api/v1/environments"]    # optional: the API routes the page may call through the SDK
    }
"""

import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from app.plugins.page_access import ApiRoute, RouteError, parse_routes

MANIFEST = "plugin.json"
ENTRY = "__init__.py"
PLUGIN_NAME = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
# Tool names as the model APIs accept them; `__` is kept for MCP servers (server__tool).
TOOL_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
KIND_NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
CATEGORIES = ("tools", "providers", "credential_types")
MAX_MANIFEST_BYTES = 64 * 1024


class ManifestError(Exception):
    """The manifest is missing or invalid; the message says what to fix."""


@dataclass(frozen=True)
class PluginManifest:
    name: str
    version: str
    description: str
    tools: tuple[str, ...] = ()
    providers: tuple[str, ...] = ()
    credential_types: tuple[str, ...] = ()
    # Path of the page's entry file inside the plugin folder, such as page/index.html.
    page: str | None = None
    # The API routes the page's token may call (app/plugins/page_access.py); none without a page.
    api: tuple[ApiRoute, ...] = ()

    @property
    def page_folder(self) -> str | None:
        return str(PurePosixPath(self.page).parent) if self.page else None

    @property
    def page_entry(self) -> str | None:
        return PurePosixPath(self.page).name if self.page else None


@dataclass(frozen=True)
class FoundPlugin:
    """A folder in data/plugins: its manifest, or the problem that keeps it from being switched on."""

    name: str
    folder: Path
    manifest: PluginManifest | None = None
    problem: str | None = None


def names(value: Any, label: str, pattern: re.Pattern[str]) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ManifestError(f"registers.{label} must be a list of names.")
    bad = [item for item in value if not pattern.match(item) or "__" in item]
    if bad:
        raise ManifestError(f"registers.{label} has names YAAP cannot use: {', '.join(bad)}.")
    if len(set(value)) != len(value):
        raise ManifestError(f"registers.{label} names something twice.")
    return tuple(value)


def check_page(value: Any, folder: Path) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ManifestError("page must be a path such as page/index.html.")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or "\\" in value or len(path.parts) < 2:
        raise ManifestError("page must be a file inside a subfolder of the plugin, such as page/index.html.")
    if any(part.startswith(".") for part in path.parts):
        raise ManifestError("page must not be in a hidden folder.")
    if not (folder / path).is_file():
        raise ManifestError(f"The page {value} does not exist.")
    return value


def parse_manifest(folder: Path) -> PluginManifest:
    path = folder / MANIFEST
    if not path.is_file():
        raise ManifestError(f"The folder has no {MANIFEST}.")
    if path.stat().st_size > MAX_MANIFEST_BYTES:
        raise ManifestError(f"{MANIFEST} is larger than 64 KB.")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ManifestError(f"{MANIFEST} is not valid JSON: {error}") from error
    if not isinstance(data, dict):
        raise ManifestError(f"{MANIFEST} must hold an object.")
    name = data.get("name")
    if name != folder.name:
        raise ManifestError(f"The name in {MANIFEST} must be the folder's name, {folder.name}.")
    version = data.get("version")
    if not isinstance(version, str) or not version.strip():
        raise ManifestError("version must be a text such as 1.0.0.")
    description = data.get("description", "")
    if not isinstance(description, str):
        raise ManifestError("description must be a text.")
    registers = data.get("registers", {})
    if not isinstance(registers, dict):
        raise ManifestError("registers must be an object with tools, providers and credential_types.")
    unknown = sorted(set(registers) - set(CATEGORIES))
    if unknown:
        raise ManifestError(f"registers has unknown parts: {', '.join(unknown)}.")
    if not (folder / ENTRY).is_file():
        raise ManifestError(f"The folder has no {ENTRY}; a plugin is a Python package.")
    page = check_page(data.get("page"), folder)
    try:
        api = parse_routes(data.get("api"))
    except RouteError as error:
        raise ManifestError(str(error)) from error
    if api and page is None:
        raise ManifestError("api lists the routes a page may call, so it needs a page.")
    return PluginManifest(
        name=name,
        version=version.strip(),
        description=description.strip(),
        tools=names(registers.get("tools"), "tools", TOOL_NAME),
        providers=names(registers.get("providers"), "providers", KIND_NAME),
        credential_types=names(registers.get("credential_types"), "credential_types", KIND_NAME),
        page=page,
        api=api,
    )


def discover(plugins_dir: Path) -> list[FoundPlugin]:
    """Every plugin folder, sorted by name. Reads manifests only: no plugin code is imported here."""
    if not plugins_dir.is_dir():
        return []
    found = []
    for folder in sorted(plugins_dir.iterdir(), key=lambda item: item.name):
        if not folder.is_dir() or folder.name.startswith((".", "_")):
            continue
        if not PLUGIN_NAME.match(folder.name):
            found.append(
                FoundPlugin(
                    folder.name,
                    folder,
                    problem="The folder's name must be lowercase letters, digits and _, starting with a letter.",
                )
            )
            continue
        try:
            found.append(FoundPlugin(folder.name, folder, manifest=parse_manifest(folder)))
        except ManifestError as error:
            found.append(FoundPlugin(folder.name, folder, problem=str(error)))
    return found
