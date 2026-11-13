"""A fake Docker Engine API for the sandbox helper's tests: containers and images in memory, served
through httpx.MockTransport, so no test needs Docker."""

import asyncio
import json
import re
from dataclasses import dataclass, field
from typing import Any

import httpx

from yaap_sandbox.sandboxes import IMAGE_LABEL


@dataclass
class FakeDocker:
    containers: dict[str, dict[str, Any]] = field(default_factory=dict)
    images: dict[str, dict[str, Any]] = field(default_factory=dict)
    # Every request that changes something: (method, path).
    changes: list[tuple[str, str]] = field(default_factory=list)
    created: list[dict[str, Any]] = field(default_factory=list)
    # What a registry would give: image name -> whether the pulled image carries the sandbox label.
    registry: dict[str, bool] = field(default_factory=dict)
    # The image names pulled, in order, and an error line the pull stream ends with, when set.
    pulls: list[str] = field(default_factory=list)
    pull_error: str = ""
    # While set, a pull waits for it: a download that takes a while.
    pull_gate: asyncio.Event | None = None

    def add_image(self, name: str = "yaap-sandbox:local", labelled: bool = True) -> None:
        labels = {IMAGE_LABEL: "1"} if labelled else {}
        self.images[name] = {"Id": "sha256:" + "a" * 64, "Config": {"Labels": labels}}

    def add_container(self, name: str, labels: dict[str, str], running: bool = True, mounts: list | None = None) -> None:
        self.containers[name] = {
            "Id": f"id-{name}",
            "Name": "/" + name,
            "Config": {"Labels": dict(labels), "Image": "other:latest"},
            "State": {"Status": "running" if running else "exited", "Running": running, "StartedAt": "2026-11-07T10:00:00Z"},
            "Mounts": mounts or [],
        }

    def stats(self) -> dict[str, Any]:
        return {
            "cpu_stats": {"cpu_usage": {"total_usage": 3_000_000}, "system_cpu_usage": 20_000_000, "online_cpus": 4},
            "precpu_stats": {"cpu_usage": {"total_usage": 1_000_000}, "system_cpu_usage": 10_000_000},
            "memory_stats": {"usage": 300 * 1024**2, "limit": 4 * 1024**3, "stats": {"inactive_file": 100 * 1024**2}},
        }

    def handle(self, request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        if method != "GET":
            self.changes.append((method, path))
        if found := re.fullmatch(r"/images/(.+)/json", path):
            image = self.images.get(found.group(1))
            return httpx.Response(200, json=image) if image else httpx.Response(404, json={"message": "no such image"})
        if path == "/images/create" and method == "POST":
            name = request.url.params["fromImage"]
            self.pulls.append(name)
            if name not in self.registry:
                return httpx.Response(404, json={"message": f"pull access denied for {name}"})
            if self.pull_error:
                lines = [{"status": "Pulling"}, {"error": self.pull_error}]
                return httpx.Response(200, text="".join(json.dumps(line) + "\n" for line in lines))
            self.add_image(name, labelled=self.registry[name])
            if name.endswith(":latest"):
                # The engine finds an image given without a tag under :latest.
                self.images[name.removesuffix(":latest")] = self.images[name]
            return httpx.Response(200, text=json.dumps({"status": "Pull complete"}) + "\n")
        if path == "/containers/json":
            wanted = json.loads(request.url.params["filters"])["label"]
            listed = []
            for name, container in self.containers.items():
                labels = container["Config"]["Labels"]
                if all(labels.get(item.split("=", 1)[0]) == item.split("=", 1)[1] for item in wanted):
                    state, mounts = container["State"]["Status"], container["Mounts"]
                    listed.append({"Names": ["/" + name], "Labels": labels, "State": state, "Mounts": mounts})
            return httpx.Response(200, json=listed)
        if path == "/containers/create" and method == "POST":
            body = json.loads(request.content)
            name = request.url.params["name"]
            if name in self.containers:
                return httpx.Response(409, json={"message": "name in use"})
            self.created.append(body)
            self.containers[name] = {
                "Id": f"id-{name}",
                "Name": "/" + name,
                "Config": {"Labels": body["Labels"], "Image": body["Image"], "Env": body["Env"]},
                "HostConfig": body["HostConfig"],
                "State": {"Status": "created", "Running": False, "StartedAt": None},
                "Mounts": [],
            }
            return httpx.Response(201, json={"Id": f"id-{name}"})
        found = re.fullmatch(r"/containers/([^/]+)(/[a-z]+)?", path)
        if not found:
            return httpx.Response(404, json={"message": "page not found"})
        name, action = found.group(1), found.group(2) or ""
        container = self.containers.get(name)
        if container is None:
            return httpx.Response(404, json={"message": f"No such container: {name}"})
        if action == "/json":
            return httpx.Response(200, json=container)
        if action == "/stats":
            return httpx.Response(200, json=self.stats())
        if action == "/start":
            container["State"] = {"Status": "running", "Running": True, "StartedAt": "2026-11-07T11:00:00Z"}
            return httpx.Response(204)
        if action == "/stop":
            container["State"] = {"Status": "exited", "Running": False, "StartedAt": container["State"]["StartedAt"]}
            return httpx.Response(204)
        if action == "" and method == "DELETE":
            del self.containers[name]
            return httpx.Response(204)
        return httpx.Response(404, json={"message": "page not found"})

    def transport(self) -> httpx.MockTransport:
        async def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/images/create" and self.pull_gate is not None:
                await self.pull_gate.wait()
            return self.handle(request)

        return httpx.MockTransport(handler)
