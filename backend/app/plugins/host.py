"""Loading plugins at start.

A plugin is switched on in Settings (a row in `plugins`); only switched-on plugins are imported, once, at start. Its
package's `register(yaap)` receives a PluginContext and registers tools, providers and credential types. They are
collected first and checked against the manifest and YAAP's own names; only when everything is right are they added
to the registries, so a plugin that fails leaves nothing behind. A plugin that fails at import or registration is
switched off with its error, and YAAP starts without it. Switching a plugin on or off takes effect at the next start:
Python cannot take an imported package back out of a running process safely.
"""

import importlib.util
import logging
import secrets
import sys
import traceback
import types
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.toolkit import Tool
from app.credentials.types import CredentialType
from app.plugins.manifest import ENTRY, FoundPlugin, KIND_NAME, TOOL_NAME, PluginManifest, discover
from app.plugins.page_access import PageGrant
from app.providers.catalog import ModelInfo
from app.providers.registry import ProviderRegistry, ProviderSpec
from app.storage.models import PluginState

logger = logging.getLogger(__name__)

PLUGINS_FOLDER = "plugins"
MODULE_PREFIX = "yaap_plugins"
MAX_ERROR_CHARS = 2000
STOPPED_WHILE_LOADING = (
    "YAAP stopped while loading this plugin, so it was switched off. Check what its import or register() does "
    "before switching it on again."
)


class PluginError(Exception):
    """A plugin registered something YAAP cannot accept; the message says what."""


@dataclass
class Registrations:
    tools: list[Tool] = field(default_factory=list)
    providers: list[tuple[ProviderSpec, list[ModelInfo]]] = field(default_factory=list)
    credential_types: list[CredentialType] = field(default_factory=list)


class PluginContext:
    """What a plugin's register() receives: the ways to extend YAAP, and where the plugin lives."""

    def __init__(self, manifest: PluginManifest, folder: Path) -> None:
        self.name = manifest.name
        self.version = manifest.version
        self.folder = folder
        self.logger = logging.getLogger(f"{MODULE_PREFIX}.{manifest.name}")
        self._manifest = manifest
        self._registrations = Registrations()

    def register_tool(self, tool: Tool) -> None:
        """A tool for the agent, used like YAAP's own: its permission decides whether it asks first."""
        if not isinstance(tool, Tool):
            raise PluginError("register_tool takes a Tool from app.plugins.interface.")
        if tool.name not in self._manifest.tools:
            raise PluginError(f"The tool {tool.name} is not listed under registers.tools in plugin.json.")
        if any(item.name == tool.name for item in self._registrations.tools):
            raise PluginError(f"The tool {tool.name} is registered twice.")
        if tool.permission not in ("auto", "ask", "conditional"):
            raise PluginError(f"The tool {tool.name} has an unknown permission {tool.permission!r}.")
        if tool.permission == "conditional" and tool.needs_approval is None:
            raise PluginError(f"The tool {tool.name} is conditional but has no needs_approval.")
        self._registrations.tools.append(tool)

    def register_provider(self, spec: ProviderSpec, models: list[ModelInfo]) -> None:
        """A model provider with its models, chosen in the model lists like any other."""
        if not isinstance(spec, ProviderSpec):
            raise PluginError("register_provider takes a ProviderSpec from app.plugins.interface.")
        if spec.name not in self._manifest.providers:
            raise PluginError(f"The provider {spec.name} is not listed under registers.providers in plugin.json.")
        if any(item.name == spec.name for item, _ in self._registrations.providers):
            raise PluginError(f"The provider {spec.name} is registered twice.")
        models = list(models)
        if not models or not all(isinstance(model, ModelInfo) for model in models):
            raise PluginError(f"The provider {spec.name} needs at least one ModelInfo.")
        wrong = [model.id for model in models if model.provider != spec.name]
        if wrong:
            raise PluginError(f"The models {', '.join(wrong)} must have provider {spec.name}.")
        self._registrations.providers.append((spec, models))

    def register_credential_type(self, credential_type: CredentialType) -> None:
        """A kind of key in the vault, stored encrypted and tested before it is saved."""
        if not isinstance(credential_type, CredentialType):
            raise PluginError("register_credential_type takes a CredentialType from app.plugins.interface.")
        kind = credential_type.kind
        if kind not in self._manifest.credential_types:
            raise PluginError(
                f"The credential type {kind} is not listed under registers.credential_types in plugin.json."
            )
        if any(item.kind == kind for item in self._registrations.credential_types):
            raise PluginError(f"The credential type {kind} is registered twice.")
        self._registrations.credential_types.append(credential_type)


@dataclass
class LoadedPlugin:
    manifest: PluginManifest
    folder: Path
    registrations: Registrations
    # Part of the page's address, new on every start: the page is served to whoever has the address, without a
    # session cookie, because a sandboxed frame does not send one with the page's own requests.
    page_token: str = field(default_factory=lambda: secrets.token_urlsafe(18))


def module_name(name: str) -> str:
    return f"{MODULE_PREFIX}.{name}"


def forget_modules(name: str) -> None:
    prefix = module_name(name)
    for key in [key for key in sys.modules if key == prefix or key.startswith(prefix + ".")]:
        del sys.modules[key]


def import_plugin(found: FoundPlugin) -> types.ModuleType:
    """Import data/plugins/<name> as the package yaap_plugins.<name>, so its own modules import each other."""
    if MODULE_PREFIX not in sys.modules:
        parent = types.ModuleType(MODULE_PREFIX)
        parent.__path__ = []
        sys.modules[MODULE_PREFIX] = parent
    name = module_name(found.name)
    spec = importlib.util.spec_from_file_location(
        name, found.folder / ENTRY, submodule_search_locations=[str(found.folder)]
    )
    if spec is None or spec.loader is None:
        raise PluginError("The package cannot be imported.")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def describe_failure(error: BaseException, folder: Path) -> str:
    """One line for Settings: the error and the plugin's own line it came from. The full trace goes to the log."""
    message = str(error).strip() or type(error).__name__
    text = message if isinstance(error, PluginError) else f"{type(error).__name__}: {message}"
    root = folder.resolve()
    frames = traceback.extract_tb(error.__traceback__)
    inside = [frame for frame in frames if Path(frame.filename).resolve().is_relative_to(root)]
    if inside and not isinstance(error, PluginError):
        frame = inside[-1]
        text += f" ({Path(frame.filename).resolve().relative_to(root).as_posix()}, line {frame.lineno})"
    return text[:MAX_ERROR_CHARS]


class PluginHost:
    """The plugins of this process: found in data/plugins, and loaded at start when switched on."""

    def __init__(self, sdk_folder: Path | None = None) -> None:
        self.folder: Path | None = None
        # Where the frontend build put plugin-sdk.js and plugin-sdk.d.ts; None when YAAP runs without a build.
        self.sdk_folder = sdk_folder
        self.loaded: dict[str, LoadedPlugin] = {}
        self._credential_types: dict[str, CredentialType] | None = None
        self._providers: ProviderRegistry | None = None

    def page_grant(self, plugin: str) -> PageGrant | None:
        """What the page of a loaded plugin may call; None when no such plugin with a page is loaded."""
        loaded = self.loaded.get(plugin)
        if loaded is None or not loaded.manifest.page:
            return None
        return PageGrant(loaded.manifest.name, loaded.manifest.api)

    def discover(self) -> list[FoundPlugin]:
        return discover(self.folder) if self.folder is not None else []

    def find(self, name: str) -> FoundPlugin | None:
        return next((found for found in self.discover() if found.name == name), None)

    def load_enabled(
        self,
        plugins_dir: Path,
        session_factory: Callable[[], Session],
        credential_types: dict[str, CredentialType],
        providers: ProviderRegistry,
    ) -> None:
        """Import every switched-on plugin; one that fails is switched off with its error."""
        self.folder = plugins_dir
        self._credential_types = credential_types
        self._providers = providers
        plugins_dir.mkdir(parents=True, exist_ok=True)
        found = {item.name: item for item in self.discover()}
        with session_factory() as session:
            query = select(PluginState).where(PluginState.enabled.is_(True)).order_by(PluginState.name)
            rows = session.scalars(query).all()
            for row in rows:
                if row.loading_since is not None:
                    switch_off(session, row, STOPPED_WHILE_LOADING)
                    continue
                item = found.get(row.name)
                if item is None:
                    switch_off(session, row, f"The folder data/plugins/{row.name} is gone.")
                    continue
                if item.manifest is None:
                    switch_off(session, row, item.problem or "The manifest is invalid.")
                    continue
                # Marked before importing: should the import take YAAP down, the next start switches it off.
                row.loading_since = datetime.now(UTC)
                session.commit()
                try:
                    self.load(item)
                except (Exception, SystemExit) as error:
                    logger.exception("Plugin %s failed to load and was switched off.", row.name)
                    switch_off(session, row, describe_failure(error, item.folder))
                    continue
                row.loading_since = None
                row.error = None
                row.failed_at = None
                session.commit()
                logger.info("Plugin %s %s loaded.", row.name, item.manifest.version)

    def load(self, found: FoundPlugin) -> None:
        manifest = found.manifest
        assert manifest is not None
        try:
            module = import_plugin(found)
            register = getattr(module, "register", None)
            if not callable(register):
                raise PluginError("The package has no register(yaap) function.")
            context = PluginContext(manifest, found.folder)
            result = register(context)
            if result is not None and hasattr(result, "__await__"):
                getattr(result, "close", lambda: None)()
                raise PluginError("register(yaap) must be a plain function, not async.")
            registrations = context._registrations
            self.check(manifest, registrations)
        except BaseException:
            forget_modules(found.name)
            raise
        self.apply(registrations)
        self.loaded[found.name] = LoadedPlugin(manifest, found.folder, registrations)

    def check(self, manifest: PluginManifest, registrations: Registrations) -> None:
        """Everything the manifest lists is registered, and nothing clashes with what YAAP already has."""
        from app.agent.tools import TOOLS_BY_NAME

        assert self._credential_types is not None and self._providers is not None
        missing = [
            *(f"tool {name}" for name in manifest.tools if name not in {tool.name for tool in registrations.tools}),
            *(
                f"provider {name}"
                for name in manifest.providers
                if name not in {spec.name for spec, _ in registrations.providers}
            ),
            *(
                f"credential type {kind}"
                for kind in manifest.credential_types
                if kind not in {item.kind for item in registrations.credential_types}
            ),
        ]
        if missing:
            raise PluginError(f"plugin.json lists what register() did not register: {', '.join(missing)}.")
        for tool in registrations.tools:
            if not TOOL_NAME.match(tool.name):
                raise PluginError(f"The tool name {tool.name} cannot be used.")
            if tool.name in TOOLS_BY_NAME:
                raise PluginError(f"A tool named {tool.name} exists already.")
        known_models = {model.id for model in self._providers.models}
        for spec, models in registrations.providers:
            if self._providers.find_provider(spec.name) is not None:
                raise PluginError(f"A provider named {spec.name} exists already.")
            kinds = set(self._credential_types) | {item.kind for item in registrations.credential_types}
            if spec.credential_kind is not None and spec.credential_kind not in kinds:
                raise PluginError(
                    f"The provider {spec.name} needs credential type {spec.credential_kind}, which YAAP does not know."
                )
            clashes = [model.id for model in models if model.id in known_models]
            if clashes:
                raise PluginError(f"Models that exist already: {', '.join(clashes)}.")
        for credential_type in registrations.credential_types:
            if not KIND_NAME.match(credential_type.kind):
                raise PluginError(f"The credential type {credential_type.kind} cannot be used.")
            if credential_type.kind in self._credential_types or credential_type.kind == "smb_password":
                raise PluginError(f"A credential type {credential_type.kind} exists already.")

    def apply(self, registrations: Registrations) -> None:
        from app.agent.tools import register_tool

        assert self._credential_types is not None and self._providers is not None
        for credential_type in registrations.credential_types:
            self._credential_types[credential_type.kind] = credential_type
        for spec, models in registrations.providers:
            self._providers.add(spec, models)
        for tool in registrations.tools:
            register_tool(tool)

    def unload_all(self) -> None:
        """Take every registration back out, at shutdown. Tests start several apps in one process."""
        from app.agent.tools import unregister_tool

        for name, plugin in list(self.loaded.items()):
            for tool in plugin.registrations.tools:
                unregister_tool(tool.name)
            for spec, _ in plugin.registrations.providers:
                if self._providers is not None and spec in self._providers.specs:
                    self._providers.remove(spec)
            for credential_type in plugin.registrations.credential_types:
                if self._credential_types is not None:
                    self._credential_types.pop(credential_type.kind, None)
            forget_modules(name)
        self.loaded.clear()


def switch_off(session: Session, row: PluginState, error: str) -> None:
    row.enabled = False
    row.loading_since = None
    row.error = error
    row.failed_at = datetime.now(UTC)
    row.changed_at = datetime.now(UTC)
    session.commit()
