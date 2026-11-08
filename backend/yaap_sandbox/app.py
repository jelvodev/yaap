"""The helper's HTTP API for YAAP (port 8000), behind a bearer token.

    GET    /health                    whether Docker answers and the sandbox image is there
    GET    /sandboxes                 this instance's sandboxes and their state
    GET    /sandboxes/{key}           one sandbox: state, network, CPU and memory
    POST   /sandboxes/{key}/start     start it, creating it first when missing or no longer as asked
    POST   /sandboxes/{key}/stop      stop it
    DELETE /sandboxes/{key}           remove it (the project folder stays: it lives in YAAP's workspace)
    GET    /host/data                 where YAAP's /data is on the host, as Docker reports it (empty when unknown)

Only keys are accepted, never container names or ids, and every call goes through `Sandboxes`, which refuses
containers without YAAP's label.
"""

import hmac
import os
import secrets
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from yaap_sandbox.docker_api import DockerError
from yaap_sandbox.host_data import yaap_data_source
from yaap_sandbox.sandboxes import Sandboxes, SandboxError, StartSpec

TOKEN_FILE = "/run/yaap-sandbox/token"


class StartIn(BaseModel):
    folder: str = Field(min_length=1, max_length=400)
    network: bool = False
    authorized_key: str = Field(min_length=20, max_length=400)
    host_key: str = Field(min_length=100, max_length=5000)


def ensure_token(path: Path) -> str:
    """The token YAAP must send: read from the shared volume, or made there on first start."""
    if path.is_file() and path.read_text(encoding="utf-8").strip():
        return path.read_text(encoding="utf-8").strip()
    path.parent.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(32)
    path.write_text(token + "\n", encoding="utf-8")
    # YAAP runs as another user and mounts the volume read-only; only the two containers mount it.
    os.chmod(path, 0o644)
    return token


def create_app(sandboxes: Sandboxes, token: str) -> FastAPI:
    app = FastAPI(title="yaap-sandbox", docs_url=None, redoc_url=None, openapi_url=None)

    def authorized(authorization: Annotated[str, Header()] = "") -> None:
        given = authorization.removeprefix("Bearer ").strip()
        if not given or not hmac.compare_digest(given, token):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not allowed.")

    @app.exception_handler(SandboxError)
    async def sandbox_error(_request: Request, error: SandboxError) -> JSONResponse:
        return JSONResponse({"detail": str(error)}, status_code=error.status)

    @app.exception_handler(DockerError)
    async def docker_error(_request: Request, error: DockerError) -> JSONResponse:
        return JSONResponse({"detail": f"Docker: {error}"}, status_code=502)

    guarded = [Depends(authorized)]

    @app.get("/health", dependencies=guarded)
    async def health() -> dict[str, Any]:
        try:
            await sandboxes.check_image()
        except SandboxError as error:
            return {"ok": False, "detail": str(error)}
        return {"ok": True, "instance": sandboxes.instance, "image": sandboxes.config.image}

    @app.get("/sandboxes", dependencies=guarded)
    async def list_sandboxes() -> list[dict[str, Any]]:
        return await sandboxes.list_all()

    @app.get("/sandboxes/{key}", dependencies=guarded)
    async def sandbox_status(key: str) -> dict[str, Any]:
        return await sandboxes.status(key)

    @app.post("/sandboxes/{key}/start", dependencies=guarded)
    async def start(key: str, payload: StartIn) -> dict[str, Any]:
        spec = StartSpec(payload.folder, payload.network, payload.authorized_key, payload.host_key)
        return await sandboxes.start(key, spec)

    @app.post("/sandboxes/{key}/stop", dependencies=guarded)
    async def stop(key: str) -> dict[str, Any]:
        return await sandboxes.stop(key)

    @app.delete("/sandboxes/{key}", dependencies=guarded)
    async def remove(key: str) -> dict[str, Any]:
        return await sandboxes.remove(key)

    @app.get("/host/data", dependencies=guarded)
    async def host_data() -> dict[str, str]:
        return {"source": await yaap_data_source(sandboxes.docker)}

    return app
