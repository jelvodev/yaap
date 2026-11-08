"""Context snapshots: what the model saw at each call.

A snapshot is a manifest that points to one blob per system part (with its source), one for the tool definitions and
one per message. Every blob is content-addressed (the SHA-256 of its canonical JSON) and gzip-compressed under
`data/traces/blobs/`, so what most calls share (the system prompt, the tools, the earlier messages) is stored once.

Before anything is written, known secret values are replaced by `[secret:NAME]`, as tool output is masked;
placeholders (`{{secret:NAME}}`) are not values and stay. File data (base64 images and documents) is left out: the
attachment stays in its conversation.

A blob is kept KEEP_DAYS after its last use (writing it again counts as use). A pinned snapshot (an eval case or a
replay that uses it) is kept with everything it points to, whatever its age. The store holds at most MAX_BYTES; above
that, the oldest unpinned blobs go first. `prune_traces_daily` runs from the minute loop, `prune_traces` at start.
"""

import gzip
import hashlib
import json
import os
import re
import secrets
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from app.providers.base import HistoryMessage, SystemPart, ToolDefinition
from app.secrets.service import mask

TRACES_FOLDER = "traces"
KEEP_DAYS = 30
MAX_BYTES = 2 * 1024**3
FILE_DATA_FIELDS = frozenset({"data"})
LEFT_OUT = "[file data left out of the snapshot]"
DIGEST = re.compile(r"[0-9a-f]{64}")
BLOB_SUFFIX = ".json.gz"
MANIFEST_VERSION = 1

JsonValue = str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]

_last_prune: list[datetime] = []


@dataclass(frozen=True)
class ContextSnapshot:
    """What a model call was sent, before it is stored."""

    system: Sequence[SystemPart]
    tools: Sequence[ToolDefinition]
    messages: Sequence[HistoryMessage]


@dataclass(frozen=True)
class SnapshotPart:
    source: str
    text: str


@dataclass(frozen=True)
class ManifestEntry:
    """One blob a manifest points to: a system part (with its source), the tool definitions, or a message."""

    key: str
    kind: Literal["system", "tools", "message"]
    source: str | None
    blob: str


@dataclass(frozen=True)
class StoredSnapshot:
    """A snapshot as read back. complete is False when a blob it points to is gone."""

    system: list[SnapshotPart]
    tools: JsonValue
    messages: list[JsonValue]
    complete: bool


def trace_dir(data_dir: Path) -> Path:
    return data_dir / TRACES_FOLDER


def cleaned(value: object, secret_values: dict[str, str]) -> JsonValue:
    """value as JSON, with secret values masked and file data left out."""
    if isinstance(value, str):
        return mask(value, secret_values)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, dict):
        return {
            str(key): LEFT_OUT if key in FILE_DATA_FIELDS and isinstance(item, str) else cleaned(item, secret_values)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [cleaned(item, secret_values) for item in value]
    return mask(str(value), secret_values)


def manifest_refs(manifest: JsonValue) -> list[str]:
    """The blobs a manifest points to."""
    if not isinstance(manifest, dict):
        return []
    refs: list[str] = []
    system = manifest.get("system")
    if isinstance(system, list):
        refs += [str(part["blob"]) for part in system if isinstance(part, dict) and isinstance(part.get("blob"), str)]
    tools = manifest.get("tools")
    if isinstance(tools, str):
        refs.append(tools)
    messages = manifest.get("messages")
    if isinstance(messages, list):
        refs += [message for message in messages if isinstance(message, str)]
    return refs


class SnapshotStore:
    def __init__(self, folder: Path) -> None:
        self.folder = folder
        self.blobs = folder / "blobs"
        self.pins = folder / "pins"

    def blob_path(self, digest: str) -> Path:
        return self.blobs / digest[:2] / f"{digest}{BLOB_SUFFIX}"

    def put(self, value: JsonValue) -> str:
        """Store value once; storing it again marks it as used now. Returns its digest."""
        data = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        digest = hashlib.sha256(data).hexdigest()
        path = self.blob_path(digest)
        if path.is_file():
            os.utime(path)
            return digest
        path.parent.mkdir(parents=True, exist_ok=True)
        partial = path.with_name(f"{path.name}.{secrets.token_hex(4)}.tmp")
        partial.write_bytes(gzip.compress(data, mtime=0))
        os.replace(partial, path)
        return digest

    def get(self, digest: str) -> JsonValue | None:
        if not DIGEST.fullmatch(digest):
            return None
        path = self.blob_path(digest)
        if not path.is_file():
            return None
        loaded: JsonValue = json.loads(gzip.decompress(path.read_bytes()).decode("utf-8"))
        return loaded

    def save(self, snapshot: ContextSnapshot, secret_values: dict[str, str]) -> str:
        """Store a model call's context; returns the digest of its manifest, which the step keeps."""
        system: list[JsonValue] = [
            {"source": part.source, "blob": self.put(cleaned(part.text, secret_values))} for part in snapshot.system
        ]
        tools = [
            {"name": tool.name, "description": tool.description, "input_schema": tool.input_schema}
            for tool in snapshot.tools
        ]
        messages: list[JsonValue] = [
            self.put(cleaned({"role": message.role, "content": message.content}, secret_values))
            for message in snapshot.messages
        ]
        manifest: JsonValue = {
            "version": MANIFEST_VERSION,
            "system": system,
            "tools": self.put(cleaned(tools, secret_values)),
            "messages": messages,
        }
        return self.put(manifest)

    def load(self, digest: str) -> StoredSnapshot | None:
        manifest = self.get(digest)
        if not isinstance(manifest, dict):
            return None
        complete = True
        system: list[SnapshotPart] = []
        parts = manifest.get("system")
        for part in parts if isinstance(parts, list) else []:
            if not isinstance(part, dict):
                continue
            text = self.get(str(part.get("blob")))
            complete = complete and isinstance(text, str)
            system.append(SnapshotPart(str(part.get("source")), text if isinstance(text, str) else ""))
        tools = self.get(str(manifest.get("tools")))
        stored = manifest.get("messages")
        messages = [self.get(str(ref)) for ref in (stored if isinstance(stored, list) else [])]
        complete = complete and tools is not None and all(message is not None for message in messages)
        return StoredSnapshot(system, tools, [message for message in messages if message is not None], complete)

    def entries(self, digest: str) -> list[ManifestEntry] | None:
        """What a snapshot's manifest points to, in order, without reading the blobs; None when it is gone."""
        manifest = self.get(digest)
        if not isinstance(manifest, dict):
            return None
        entries: list[ManifestEntry] = []
        parts = manifest.get("system")
        for index, part in enumerate(parts if isinstance(parts, list) else []):
            if isinstance(part, dict) and isinstance(part.get("blob"), str):
                entries.append(ManifestEntry(f"system-{index}", "system", str(part.get("source")), str(part["blob"])))
        tools = manifest.get("tools")
        if isinstance(tools, str):
            entries.append(ManifestEntry("tools", "tools", None, tools))
        messages = manifest.get("messages")
        for index, ref in enumerate(messages if isinstance(messages, list) else []):
            if isinstance(ref, str):
                entries.append(ManifestEntry(f"message-{index}", "message", None, ref))
        return entries

    # Pins: an owner ("eval-case:12", "replay:3") keeps a snapshot while it uses it.

    def pin_path(self, digest: str, owner: str) -> Path:
        return self.pins / f"{digest}.{hashlib.sha256(owner.encode('utf-8')).hexdigest()[:16]}"

    def pin(self, digest: str, owner: str) -> None:
        if not DIGEST.fullmatch(digest):
            raise ValueError("Not a snapshot digest.")
        self.pins.mkdir(parents=True, exist_ok=True)
        self.pin_path(digest, owner).write_text(owner, encoding="utf-8")

    def unpin(self, digest: str, owner: str) -> None:
        if DIGEST.fullmatch(digest):
            self.pin_path(digest, owner).unlink(missing_ok=True)

    def pinned(self) -> set[str]:
        if not self.pins.is_dir():
            return set()
        return {path.name.split(".")[0] for path in self.pins.iterdir() if DIGEST.fullmatch(path.name.split(".")[0])}

    def protected(self) -> set[str]:
        """Pinned snapshots and every blob they point to."""
        keep: set[str] = set()
        for digest in self.pinned():
            keep.add(digest)
            keep.update(manifest_refs(self.get(digest)))
        return keep

    def prune(self, now: float | None = None, keep_days: int = KEEP_DAYS, max_bytes: int = MAX_BYTES) -> int:
        """Remove unpinned blobs unused for keep_days, then the oldest until the store fits in max_bytes."""
        if not self.blobs.is_dir():
            return 0
        cutoff = (now if now is not None else time.time()) - keep_days * 86400
        keep = self.protected()
        kept_bytes, candidates = 0, []
        for path in self.blobs.glob(f"*/*{BLOB_SUFFIX}"):
            stat = path.stat()
            if path.name.removesuffix(BLOB_SUFFIX) in keep:
                kept_bytes += stat.st_size
            else:
                candidates.append((stat.st_mtime, stat.st_size, path))
        removed, remaining = 0, []
        for mtime, size, path in sorted(candidates):
            if mtime < cutoff:
                path.unlink(missing_ok=True)
                removed += 1
            else:
                remaining.append((size, path))
        total = kept_bytes + sum(size for size, _ in remaining)
        for size, path in remaining:
            if total <= max_bytes:
                break
            path.unlink(missing_ok=True)
            total -= size
            removed += 1
        return removed


def prune_traces(data_dir: Path) -> int:
    """At start: remove snapshots past their time or over the size limit. Returns how many blobs went."""
    return SnapshotStore(trace_dir(data_dir)).prune()


def prune_traces_daily(data_dir: Path) -> None:
    """Called from the minute loop: prunes at most once a day."""
    now = datetime.now(UTC)
    if _last_prune and now - _last_prune[0] < timedelta(days=1):
        return
    prune_traces(data_dir)
    _last_prune[:] = [now]
