"""The few Docker Engine API calls the helper makes, over HTTP to the docker-socket-proxy.

Paths carry no API version, so the engine's own version answers; the proxy only lets through the container and image
sections (docker-compose.yml).
"""

import json
from typing import Any

import httpx


class DockerError(Exception):
    """Docker answered with an error; the message is Docker's own."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


def base_url(docker_host: str) -> str:
    """tcp://docker-proxy:2375 (DOCKER_HOST's form) as an http address."""
    if docker_host.startswith("tcp://"):
        return "http://" + docker_host.removeprefix("tcp://")
    return docker_host


class DockerApi:
    def __init__(self, url: str, transport: httpx.AsyncBaseTransport | None = None, timeout: float = 60) -> None:
        # unix:///var/run/docker.sock for development only; docker-compose.yml always goes through the proxy.
        if url.startswith("unix://") and transport is None:
            transport = httpx.AsyncHTTPTransport(uds=url.removeprefix("unix://"))
            url = "http://docker"
        self.client = httpx.AsyncClient(base_url=base_url(url), transport=transport, timeout=timeout)

    async def close(self) -> None:
        await self.client.aclose()

    async def call(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            response = await self.client.request(method, path, **kwargs)
        except httpx.HTTPError as error:
            raise DockerError(503, f"Docker cannot be reached: {error}") from error
        if response.status_code >= 400:
            try:
                message = str(response.json().get("message") or response.text)
            except ValueError:
                message = response.text
            raise DockerError(response.status_code, message)
        return response

    async def inspect_container(self, name: str) -> dict[str, Any] | None:
        try:
            return (await self.call("GET", f"/containers/{name}/json")).json()
        except DockerError as error:
            if error.status == 404:
                return None
            raise

    async def inspect_image(self, name: str) -> dict[str, Any] | None:
        try:
            return (await self.call("GET", f"/images/{name}/json")).json()
        except DockerError as error:
            if error.status == 404:
                return None
            raise

    async def list_containers(self, labels: list[str]) -> list[dict[str, Any]]:
        filters = json.dumps({"label": labels})
        return (await self.call("GET", "/containers/json", params={"all": "true", "filters": filters})).json()

    async def create_container(self, name: str, body: dict[str, Any]) -> str:
        response = await self.call("POST", "/containers/create", params={"name": name}, json=body)
        return str(response.json()["Id"])

    async def start_container(self, name: str) -> None:
        await self.call("POST", f"/containers/{name}/start")

    async def stop_container(self, name: str, seconds: int = 5) -> None:
        await self.call("POST", f"/containers/{name}/stop", params={"t": str(seconds)})

    async def remove_container(self, name: str) -> None:
        try:
            await self.call("DELETE", f"/containers/{name}", params={"force": "true", "v": "true"})
        except DockerError as error:
            if error.status != 404:
                raise

    async def stats(self, name: str) -> dict[str, Any]:
        # Without one-shot Docker takes two samples, so the CPU share can be computed.
        return (await self.call("GET", f"/containers/{name}/stats", params={"stream": "false"})).json()
