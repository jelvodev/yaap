"""Run the helper: `python -m yaap_sandbox` (docker/sandbox-helper/Dockerfile)."""

import asyncio
import logging
import os
from pathlib import Path

import uvicorn

from yaap_sandbox.app import TOKEN_FILE, create_app, ensure_token
from yaap_sandbox.docker_api import DockerApi, DockerError
from yaap_sandbox.relay import start_relay
from yaap_sandbox.sandboxes import Config, SandboxError, Sandboxes

log = logging.getLogger("yaap_sandbox")


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    docker = DockerApi(os.environ.get("DOCKER_HOST", "tcp://docker-proxy:2375"))
    sandboxes = Sandboxes(docker, Config.from_environment())
    # The socket proxy may start a moment after the helper.
    for attempt in range(30):
        try:
            await sandboxes.discover()
            break
        except (SandboxError, DockerError) as error:
            if attempt == 29:
                raise
            log.info("Waiting for Docker: %s", error)
            await asyncio.sleep(2)
    log.info("Instance %s, workspace on the host at %s", sandboxes.instance, sandboxes.config.workspace_host)
    token = ensure_token(Path(os.environ.get("YAAP_SANDBOX_TOKEN_FILE", TOKEN_FILE)))
    relay = await start_relay(sandboxes, token, port=int(os.environ.get("YAAP_SANDBOX_RELAY_PORT", "2222")))
    server = uvicorn.Server(
        uvicorn.Config(create_app(sandboxes, token), host="0.0.0.0", port=int(os.environ.get("YAAP_SANDBOX_PORT", "8000")))
    )
    try:
        await server.serve()
    finally:
        relay.close()
        await docker.close()


if __name__ == "__main__":
    asyncio.run(main())
