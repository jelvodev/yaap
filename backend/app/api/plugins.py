"""Settings › System › Plugins: the plugins in data/plugins, switching them on or off, their pages and the SDK."""

import mimetypes
import secrets
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel
from sqlalchemy import select

from app.api.dependencies import SessionDependency
from app.plugins.host import LoadedPlugin, PluginHost
from app.plugins.manifest import FoundPlugin
from app.plugins.page_access import SDK_SCRIPT
from app.storage.models import PluginState

router = APIRouter(tags=["plugins"])
# Not under /api: the SDK holds no data, and a plugin page loads it as a module from its opaque origin.
sdk_router = APIRouter(tags=["plugins"])
SDK_DECLARATIONS = "/plugin-sdk.d.ts"
NO_SDK = "The plugin SDK is made by the frontend build (npm run build); this YAAP runs without it."


class PluginOut(BaseModel):
    name: str
    version: str
    description: str
    # What the manifest says the plugin registers.
    tools: list[str]
    providers: list[str]
    credential_types: list[str]
    has_page: bool
    # The switch in Settings.
    enabled: bool
    # Whether this run of YAAP imported it; differs from enabled until the next start.
    loaded: bool
    restart_needed: bool
    # Why YAAP switched it off: the error at import or registration.
    error: str | None
    failed_at: datetime | None
    # Why it cannot be switched on: a missing or invalid manifest.
    problem: str | None
    # Where its page is served while it is loaded; new on every start.
    page_url: str | None
    # The API routes its page may call through the plugin SDK, as listed under api in plugin.json. YAAP's page makes
    # those calls for the frame, with the session, and the gate holds them to this list.
    api_routes: list[str]


class PluginSwitchIn(BaseModel):
    enabled: bool


class PluginRestartOut(BaseModel):
    restarting: bool


def host(request: Request) -> PluginHost:
    return request.app.state.plugins


def page_base(name: str, plugin: LoadedPlugin) -> str:
    return f"/api/v1/plugins/{name}/page/{plugin.page_token}/"


def plugin_out(found: FoundPlugin | None, name: str, row: PluginState | None, loaded: LoadedPlugin | None) -> PluginOut:
    manifest = found.manifest if found is not None else None
    shown = manifest or (loaded.manifest if loaded is not None else None)
    enabled = bool(row and row.enabled)
    problem = found.problem if found is not None else "The folder is gone from data/plugins."
    serves_page = loaded is not None and bool(loaded.manifest.page)
    return PluginOut(
        name=name,
        version=shown.version if shown else "",
        description=shown.description if shown else "",
        tools=list(shown.tools) if shown else [],
        providers=list(shown.providers) if shown else [],
        credential_types=list(shown.credential_types) if shown else [],
        has_page=bool(shown and shown.page),
        enabled=enabled,
        loaded=loaded is not None,
        restart_needed=enabled != (loaded is not None),
        error=row.error if row else None,
        failed_at=row.failed_at if row else None,
        problem=problem,
        page_url=page_base(name, loaded) if serves_page else None,
        api_routes=[route.text for route in shown.api] if shown else [],
    )


def all_plugins(request: Request, session: SessionDependency) -> list[PluginOut]:
    plugins = host(request)
    found = {item.name: item for item in plugins.discover()}
    rows = {row.name: row for row in session.scalars(select(PluginState))}
    # A row whose folder is gone still shows while it matters: switched on, loaded, or with an error.
    names = set(found) | set(plugins.loaded) | {name for name, row in rows.items() if row.enabled or row.error}
    return [plugin_out(found.get(name), name, rows.get(name), plugins.loaded.get(name)) for name in sorted(names)]


@router.get("/plugins", response_model=list[PluginOut])
def list_plugins(request: Request, session: SessionDependency) -> list[PluginOut]:
    """Every plugin folder in data/plugins, read from its manifest; nothing is imported to list them."""
    return all_plugins(request, session)


@router.put("/plugins/{name}", response_model=PluginOut)
def switch_plugin(name: str, payload: PluginSwitchIn, request: Request, session: SessionDependency) -> PluginOut:
    """Switch a plugin on or off; it takes effect at the next start of YAAP."""
    found = host(request).find(name)
    row = session.get(PluginState, name)
    if found is None and row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No plugin with this name in data/plugins.")
    if payload.enabled and (found is None or found.manifest is None):
        problem = found.problem if found is not None else "The folder is gone from data/plugins."
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"This plugin cannot be switched on: {problem}")
    if row is None:
        row = PluginState(name=name, enabled=False)
        session.add(row)
    row.enabled = payload.enabled
    row.changed_at = datetime.now(UTC)
    if payload.enabled:
        # Switching on again is the user's answer to the error shown.
        row.error = None
        row.failed_at = None
    session.commit()
    return next(plugin for plugin in all_plugins(request, session) if plugin.name == name)


@router.post("/plugins/restart", response_model=PluginRestartOut)
def restart_for_plugins(request: Request) -> PluginRestartOut:
    """Restart YAAP so plugin switches take effect. Replies in progress stop; Docker starts YAAP again."""
    request.app.state.request_restart()
    return PluginRestartOut(restarting=True)


def page_policy(origin: str, base: str) -> str:
    """Like the policy of `html` previews, but the page may load only its own files and the plugin SDK:
    no outside addresses, no calls to YAAP's API (the SDK asks YAAP's page around the frame by postMessage), no forms,
    no frames. The sandbox gives it an opaque origin even when the address is opened on its own."""
    sdk = f"{origin}{SDK_SCRIPT}"
    return (
        f"default-src 'none'; script-src 'unsafe-inline' {base} {sdk}; style-src 'unsafe-inline' {base}; "
        f"img-src data: blob: {base}; font-src data: {base}; media-src blob: {base}; "
        f"connect-src {base}; "
        "form-action 'none'; frame-src 'none'; base-uri 'none'; frame-ancestors 'self'; "
        "sandbox allow-scripts allow-modals"
    )


@router.get("/plugins/{name}/page/{token}/{path:path}", include_in_schema=False)
def plugin_page(name: str, token: str, path: str, request: Request) -> FileResponse:
    """A file of a loaded plugin's page. The address carries a token from GET /plugins instead of a session."""
    plugin = host(request).loaded.get(name)
    if plugin is None or not plugin.manifest.page or not secrets.compare_digest(token, plugin.page_token):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found.")
    root = (plugin.folder / str(plugin.manifest.page_folder)).resolve()
    relative = path or str(plugin.manifest.page_entry)
    candidate = (root / relative).resolve()
    hidden = any(part.startswith(".") for part in Path(relative).parts)
    if hidden or not candidate.is_relative_to(root) or not candidate.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found.")
    origin = str(request.base_url).rstrip("/")
    return FileResponse(
        candidate,
        media_type=media_type_of(candidate),
        headers={
            "Content-Security-Policy": page_policy(origin, origin + page_base(name, plugin)),
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
            "Cache-Control": "no-store",
            # The page runs on an opaque origin, so its ES modules and fetches of its own files are cross-origin
            # requests in CORS mode; the address's token is what guards them. Static files only: no API response
            # carries CORS headers.
            "Access-Control-Allow-Origin": "*",
        },
    )


def media_type_of(path: Path) -> str:
    """Browsers run an ES module only with a JavaScript type, so .js and .mjs do not depend on the system's table."""
    if path.suffix in (".js", ".mjs"):
        return "text/javascript; charset=utf-8"
    return mimetypes.guess_type(path.name)[0] or "application/octet-stream"


def sdk_file(request: Request, name: str, media_type: str) -> Response:
    folder = host(request).sdk_folder
    path = folder / name if folder is not None else None
    if path is None or not path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, NO_SDK)
    return FileResponse(
        path,
        media_type=media_type,
        headers={
            "Access-Control-Allow-Origin": "*",
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-cache",
        },
    )


@sdk_router.get(SDK_SCRIPT, include_in_schema=False)
def plugin_sdk(request: Request) -> Response:
    """The plugin SDK, one ES module generated from the OpenAPI description by the frontend build."""
    return sdk_file(request, SDK_SCRIPT.lstrip("/"), "text/javascript; charset=utf-8")


@sdk_router.get(SDK_DECLARATIONS, include_in_schema=False)
def plugin_sdk_declarations(request: Request) -> Response:
    """The SDK's TypeScript declarations, for plugins written in TypeScript."""
    return sdk_file(request, SDK_DECLARATIONS.lstrip("/"), "text/plain; charset=utf-8")
