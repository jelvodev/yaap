"""Application entry point: `uvicorn app.main:app`."""

import asyncio
from datetime import UTC, datetime
import os
import signal
import threading
from collections.abc import AsyncIterator
from functools import partial
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import APIRouter, FastAPI, HTTPException, status
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.agent import mcp_server
from app.agent import outside_mcp
from app.a2a import server as a2a_server
from app.agent.runs import RunRegistry
from app.api import (
    activity,
    agents,
    approvals,
    attachments,
    audit,
    backups,
    blueprint,
    checkpoints,
    knowledge,
    catalog,
    cli_sign_in as cli_sign_in_api,
    conversations,
    credentials,
    environments,
    eval_cases,
    git,
    presets,
    projects,
    search,
    secrets,
    settings,
    updates,
    usage,
)
from app.config import environment_value, load_settings
from app.api import models as models_api
from app.core.embeddings import load_default_embedder, model_folder
from app.core.local_models import SEARCH_MODEL, ModelService
from app.core.model_files import ModelDownloads, fetch_file
from app.core.search_model import start_when_first_start_is_done
from app.core.process_hardening import harden_process
from app.core.replies import ReplyRegistry
from app.core.restore import apply_pending_restore
from app.agent.outputs import prune_outputs
from app.mcp_client.manager import McpManager
from app.browser.service import find_command as find_browser_command
from app.version import VERSION
from app.websearch.service import SearxngSearch
from app.core.settings import SETTING_DEFINITIONS, resolve_setting
from app.providers.endpoints import SETTING_KEY as ENDPOINTS_KEY
from app.providers.endpoints import parse_endpoints, refresh_endpoints
from app.providers.ollama_provider import refresh as refresh_ollama
from app.api import mcp as mcp_api
from app.api import openai_endpoints as openai_endpoints_api
from app.api import tasks as tasks_api
from app.api import estimates as estimates_api
from app.api import reviews as reviews_api
from app.api import second_opinion as second_opinion_api
from app.api import github as github_api
from app.api import targets as targets_api
from app.api import vault as vault_api
from app.api import files as files_api
from app.api import speech as speech_api
from app.api import memory_merges as memory_merges_api
from app.api import session_import as session_import_api
from app.api import documents as documents_api
from app.api import sandbox as sandbox_api
from app.api import auth as auth_api
from app.auth.account import default_hasher
from app.auth.boundary import BrowserBoundary, allowed_hosts, parse_allowed_hosts
from app.auth.gate import AuthGate
from app.api import outside as outside_api
from app.api import plugins as plugins_api
from app.api import preview as preview_api
from app.api import token_views as token_views_api
from app.api import behind as behind_api
from app.api import traces as traces_api
from app.api import telemetry as telemetry_api
from app.api import evals as evals_api
from app.api import replays as replays_api
from app.api import learning as learning_api
from app.api import blueprint_import as blueprint_import_api
from app.api import blueprint_starter as blueprint_starter_api
from app.api import first_run as first_run_api
from app.core.first_run import FirstRunService
from app.plugins.host import PLUGINS_FOLDER, PluginHost
from app.preview.proxy import start_listener as start_preview_listener
from app.preview.service import PreviewService, config_from_environment as preview_config
from app.sandbox.client import HelperClient
from app.sandbox.host_path import discover_host_data
from app.sandbox.service import SANDBOXES
from app.lsp.pool import LANGUAGE_SERVERS
from app.runner.service import RUNNERS, runner_enabled
from app.scheduling.service import recover_runs, run_periodically as run_scheduler
from app.evals.runs import EvalService, recover_runs as recover_eval_runs
from app.replays.service import ReplayService, recover_replays
from app.core.reply_services import ReplyServices
from app.core.message_queue import restore_interrupted_handoffs
from app.core.search_index import SearchIndexer
from app.core.shutdown import stop_replies_and_their_work
from app.credentials.crypto import CredentialCipher
from app.backups.runs import recover_backup_runs
from app.updates.background import check_at_start
from app.updates.check import UpdateService
from app.agents.records import close_stale_runs
from app.vault.rotation import rotate_all
from app.credentials.types import CredentialType, default_credential_types
from app.environments.service import seed_default_environments
from app.git.periodic import fetch_periodically
from app.providers.cli_install import CliTools
from app.providers.cli_sign_in import CliSignIns
from app.providers.registry import ProviderRegistry, default_provider_registry
from app.agent.processes import ProcessManager
from app.agent.shells import ShellSessions
from app.storage.db import create_database_engine, create_session_factory, run_migrations
from app.tracing.snapshots import prune_traces


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Before anything is started: no process of the same user may read YAAP's /proc files or memory.
    harden_process()
    settings = load_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    # A restore prepared before the last stop goes in place before the database opens.
    apply_pending_restore(settings.data_dir)
    settings.workspace_dir.mkdir(parents=True, exist_ok=True)
    settings.blueprint_dir.mkdir(parents=True, exist_ok=True)
    settings.files_dir.mkdir(parents=True, exist_ok=True)
    app.state.data_dir = settings.data_dir
    app.state.files_dir = settings.files_dir
    app.state.workspace_dir = settings.workspace_dir
    app.state.blueprint_dir = settings.blueprint_dir
    # Full tool outputs are kept for 30 days, context snapshots 30 days unless pinned.
    prune_outputs(settings.data_dir)
    prune_traces(settings.data_dir)
    run_migrations(settings.database_url)
    engine = create_database_engine(settings.database_url)
    app.state.session_factory = create_session_factory(engine)
    restore_interrupted_handoffs(app.state.session_factory)
    app.state.cipher = CredentialCipher(settings.master_key, settings.previous_master_key)
    with app.state.session_factory() as session:
        seed_default_environments(session)
        # A key rotation: every value moves to the new key before anything uses it.
        app.state.key_rotation = rotate_all(session, app.state.cipher) if settings.previous_master_key else None
        ollama_url = str(resolve_setting(session, SETTING_DEFINITIONS["ollama_url"], None).value or "")
        endpoints = parse_endpoints(resolve_setting(session, SETTING_DEFINITIONS[ENDPOINTS_KEY], None).value)
    app.state.master_key_source = settings.master_key_source
    # Plugins switched on in Settings; one that fails is switched off and YAAP starts without it.
    app.state.plugins.load_enabled(
        settings.data_dir / PLUGINS_FOLDER, app.state.session_factory, app.state.credential_types, app.state.providers
    )
    if ollama_url and app.state.providers.ollama is not None:
        # Local models are known again after a restart; read in the background, as Ollama may be down.
        asyncio.get_running_loop().run_in_executor(None, refresh_ollama, app.state.providers.ollama, ollama_url)
    if endpoints:
        # Read in the background, as an endpoint may be down; its models are known again once it answers.
        asyncio.get_running_loop().run_in_executor(None, refresh_endpoints, app.state.providers, endpoints)
    periodic_fetch = asyncio.create_task(
        fetch_periodically(app.state.session_factory, app.state.cipher, settings.blueprint_dir, settings.workspace_dir)
    )
    app.state.processes.bind(app.state.session_factory)
    idle_processes = asyncio.create_task(app.state.processes.stop_idle_periodically())
    # Shell sessions live with their conversation and end after two idle hours.
    app.state.shells.bind(app.state.session_factory, settings.data_dir)
    idle_shells = asyncio.create_task(app.state.shells.end_idle_periodically())
    idle_mcp = asyncio.create_task(app.state.mcp.stop_idle_periodically())
    # Sandboxes per project and the runner per environment, through the yaap-sandbox helper.
    helper = HelperClient.from_environment()
    SANDBOXES.bind(app.state.session_factory, helper, app.state.processes)
    RUNNERS.bind(app.state.session_factory, app.state.cipher, settings.workspace_dir)
    idle_sandboxes = asyncio.create_task(SANDBOXES.stop_idle_periodically())
    # Where the data folder is on the user's computer, for the project part of the system prompt; asked of the helper
    # in the background, as it may start after YAAP. None leaves that line out.
    app.state.host_data_dir = await discover_host_data(None, settings.data_dir) if helper is None else None
    host_data = asyncio.create_task(learn_host_data(app, helper, settings.data_dir)) if helper is not None else None
    # Language servers stop after the same idle time as the runner they run in.
    idle_language_servers = asyncio.create_task(LANGUAGE_SERVERS.stop_idle_periodically())
    # Scheduled tasks: runs cut off by a restart end; the loop starts what is due.
    recover_runs(app)
    with app.state.session_factory() as session:
        # Agent runs and sub-runs cut off by a restart end as incomplete.
        close_stale_runs(session)
        # Eval runs cut off by a restart end as stopped.
        recover_eval_runs(session)
        # Replays too; their workspaces and pins go.
        recover_replays(session, settings.workspace_dir, settings.data_dir)
    # Backups on a schedule: a backup cut off by a restart failed; a missed one runs once.
    app.state.backup_since = datetime.now(UTC)
    with app.state.session_factory() as session:
        recover_backup_runs(session)
    scheduler = asyncio.create_task(run_scheduler(app)) if app.state.scheduler else None
    app.state.updates = UpdateService(app.state.session_factory, app.state.cipher)
    start_check = asyncio.create_task(check_at_start(app.state.updates)) if app.state.update_check_at_start else None
    # CLIs installed by an older release are installed again from this release's lock.
    app.state.cli_sign_ins.refresh_tools()
    models_dir = settings.data_dir / "models"
    app.state.indexer = SearchIndexer(
        app.state.session_factory,
        settings.blueprint_dir,
        settings.workspace_dir,
        app.state.load_embedder or partial(load_default_embedder, model_folder(models_dir)),
        data_dir=settings.data_dir,
    )
    # The search model and the speech models; the index picks the search model up when it arrives or goes.
    app.state.models = ModelService(
        models_dir,
        ModelDownloads(app.state.model_fetch),
        on_change=lambda name: app.state.indexer.reload_embedder() if name == SEARCH_MODEL else None,
    )
    search_model = asyncio.create_task(
        start_when_first_start_is_done(
            app.state.first_run, app.state.models, app.state.session_factory, settings.blueprint_dir
        )
    )
    indexing = asyncio.create_task(app.state.indexer.run_periodically()) if app.state.background_indexing else None
    # The preview of a project's app on its own port, off when YAAP_PREVIEW_PORT is 0 or empty.
    preview_listener = await start_preview_listener(app.state.preview)
    yield
    if preview_listener is not None:
        await preview_listener.stop()
    search_model.cancel()
    if indexing is not None:
        indexing.cancel()
    periodic_fetch.cancel()
    idle_processes.cancel()
    idle_shells.cancel()
    idle_mcp.cancel()
    idle_sandboxes.cancel()
    if host_data is not None:
        host_data.cancel()
    idle_language_servers.cancel()
    if scheduler is not None:
        scheduler.cancel()
    if start_check is not None:
        start_check.cancel()
    app.state.outside_clients.close_all(app)
    await app.state.mcp.shutdown()
    await app.state.processes.shutdown()
    await app.state.cli_sign_ins.stop()
    await LANGUAGE_SERVERS.stop_all()
    # Before the engine goes: work still running would keep a connection, and the database file, open.
    await stop_replies_and_their_work(app.state.replies, (app.state.evals.work, app.state.replay_service.work))
    app.state.plugins.unload_all()
    engine.dispose()


async def learn_host_data(app: FastAPI, helper: HelperClient, data_dir: Path) -> None:
    app.state.host_data_dir = await discover_host_data(helper, data_dir)


class ImmutableAssets(StaticFiles):
    """Vite puts a content hash in every asset name, so an asset never changes and may be cached for good."""

    def file_response(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response


# index.html names the current assets; the browser must check it on every load, or it keeps running an old build.
REVALIDATE = {"Cache-Control": "no-cache"}


def mount_frontend(app: FastAPI, static_dir: Path) -> None:
    """Serve the built frontend, with index.html for every client-side route."""
    app.mount("/assets", ImmutableAssets(directory=static_dir / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def serve_frontend(path: str) -> FileResponse:
        if path.startswith("api/"):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found.")
        candidate = (static_dir / path).resolve()
        if path and candidate.is_file() and candidate.is_relative_to(static_dir):
            return FileResponse(candidate, headers=REVALIDATE)
        return FileResponse(static_dir / "index.html", headers=REVALIDATE)


def restart_soon() -> None:
    """Stop YAAP shortly, after the response that asked for it has gone out. Shutting down stops replies and
    processes; Docker's restart policy starts YAAP again."""
    threading.Timer(1.5, os.kill, (os.getpid(), signal.SIGTERM)).start()


def create_app(
    credential_types: dict[str, CredentialType] | None = None,
    providers: ProviderRegistry | None = None,
    static_dir: Path | None = None,
) -> FastAPI:
    app = FastAPI(title="YAAP", version=VERSION, lifespan=lifespan)
    # Every API route needs a signed-in browser or an API token.
    app.add_middleware(AuthGate)
    # Added last, so it runs first: the host allowlist and the origin check guard everything, the frontend included.
    extra_hosts = parse_allowed_hosts(environment_value("YAAP_ALLOWED_HOSTS"))
    # The preview's origin is refused as well: its pages are the project's code (app/preview).
    preview = preview_config()
    refused_ports = frozenset({preview.port}) if preview.enabled else frozenset()
    app.add_middleware(BrowserBoundary, allowed=allowed_hosts(extra_hosts), refused_ports=refused_ports)
    # Argon2id for the account's password; tests use cheaper parameters.
    app.state.password_hasher = default_hasher()
    app.state.credential_types = credential_types or default_credential_types()
    app.state.providers = providers or default_provider_registry()
    app.state.first_run = FirstRunService(app.state.providers)
    app.state.runs = RunRegistry()
    # Outside MCP clients with an API token; A2A calls go through this transport in tests.
    app.state.outside_clients = outside_mcp.OutsideClients()
    app.state.a2a_transport = None
    # The plugin SDK is part of the built frontend, served next to it for plugin pages.
    app.state.plugins = PluginHost(sdk_folder=static_dir)
    app.state.mcp = McpManager()
    # The agent's browser: Playwright MCP in the runner, or in YAAP when the runner is off
    # and Playwright is installed here.
    app.state.browser_command = find_browser_command(in_runner=runner_enabled())
    # Web search through the SearXNG service next to YAAP (docker-compose.yml); without its address, no web_search.
    search_url = environment_value("YAAP_SEARCH_URL").strip()
    app.state.web_search = SearxngSearch(search_url) if search_url else None
    # Background processes agents start (dev servers); they outlive a reply.
    app.state.processes = ProcessManager()
    app.state.preview = PreviewService(app.state.processes, preview, allowed_hosts(extra_hosts))
    app.state.shells = ShellSessions()
    app.state.cli_sign_ins = CliSignIns.of(
        (spec.sign_in for spec in app.state.providers.specs if spec.sign_in is not None), CliTools()
    )
    app.state.replies = ReplyRegistry()
    # Eval runs of projects' cases, played through the reply service in the background.
    app.state.evals = EvalService(lambda: ReplyServices.from_state(app.state))
    # Replays of a reply from one of its model calls, unattended like eval plays.
    app.state.replay_service = ReplayService(lambda: ReplyServices.from_state(app.state))
    # Conversation titles from the utility model after the first reply (tests switch this off).
    app.state.utility_titles = True
    app.state.memory_suggestions = True
    # Memory tidy-ups after every 20 new entries (tests switch this off).
    app.state.memory_tidy = True
    # Reviews by another vendor after goals and scheduled tasks that changed code (tests switch this off).
    app.state.reviews_after_goals = True
    app.state.scheduler = True
    # The update check a few seconds after start (tests switch this off; the schedule is the scheduler's).
    app.state.update_check_at_start = True
    # The search index: the embedding model, loaded on first use, and passes in the background
    # (tests run passes themselves).
    # None: the local model from the data folder. Tests and the footprint run set their own.
    app.state.load_embedder = None
    # How model files are fetched (tests do not touch the network).
    app.state.model_fetch = fetch_file
    app.state.background_indexing = True
    # After a restore is prepared YAAP stops, and Docker starts it again (tests replace this).
    app.state.request_restart = restart_soon
    # How processes inside the container (Claude Code) reach this server, for the MCP endpoint.
    app.state.internal_url = environment_value("YAAP_INTERNAL_URL", "http://127.0.0.1:8080").rstrip("/")

    api = APIRouter(prefix="/api/v1")
    api.include_router(auth_api.router)
    api.include_router(environments.router)
    api.include_router(credentials.router)
    api.include_router(catalog.router)
    api.include_router(cli_sign_in_api.router)
    api.include_router(openai_endpoints_api.router)
    api.include_router(projects.router)
    api.include_router(blueprint.router)
    api.include_router(git.router)
    api.include_router(conversations.router)
    api.include_router(attachments.router)
    api.include_router(presets.router)
    api.include_router(agents.router)
    api.include_router(settings.router)
    api.include_router(usage.router)
    api.include_router(audit.router)
    api.include_router(search.router)
    api.include_router(backups.router)
    api.include_router(updates.router)
    api.include_router(approvals.router)
    api.include_router(activity.router)
    api.include_router(token_views_api.router)
    api.include_router(behind_api.router)
    api.include_router(checkpoints.router)
    api.include_router(secrets.router)
    api.include_router(vault_api.router)
    api.include_router(targets_api.router)
    api.include_router(github_api.router)
    api.include_router(knowledge.router)
    api.include_router(mcp_api.router)
    api.include_router(tasks_api.router)
    api.include_router(estimates_api.router)
    api.include_router(reviews_api.router)
    api.include_router(second_opinion_api.router)
    api.include_router(files_api.router)
    api.include_router(speech_api.router)
    api.include_router(models_api.router)
    api.include_router(memory_merges_api.router)
    api.include_router(session_import_api.router)
    api.include_router(documents_api.router)
    api.include_router(sandbox_api.router)
    api.include_router(outside_api.router)
    api.include_router(plugins_api.router)
    api.include_router(preview_api.router)
    api.include_router(traces_api.router)
    api.include_router(eval_cases.router)
    api.include_router(telemetry_api.router)
    api.include_router(evals_api.router)
    api.include_router(replays_api.router)
    api.include_router(learning_api.router)
    api.include_router(blueprint_import_api.router)
    api.include_router(blueprint_starter_api.router)
    api.include_router(first_run_api.router)
    app.include_router(api)
    app.include_router(plugins_api.sdk_router)
    app.include_router(mcp_server.router)
    # YAAP for other machines: behind API tokens, not under /api/v1.
    app.include_router(outside_mcp.router)
    app.include_router(a2a_server.router)

    if static_dir is not None and (static_dir / "index.html").is_file():
        mount_frontend(app, static_dir.resolve())
    return app


def static_dir_from_environment() -> Path | None:
    value = environment_value("YAAP_STATIC_DIR").strip()
    return Path(value) if value else None


app = create_app(static_dir=static_dir_from_environment())
