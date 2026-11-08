"""The repository maps already made, on disk under the data folder, one small file per key.

On disk rather than in memory, so a restart does not rebuild every map and no shared state lives in the process. The
folder holds at most MAX_ENTRIES maps; writing one more removes the least recently used. A map is a few kilobytes,
so the folder stays around a megabyte.
"""

import hashlib
import os
import time
from pathlib import Path

FOLDER_NAME = "repo-maps"
MAX_ENTRIES = 256


def store_folder(data_dir: Path) -> Path:
    return data_dir / FOLDER_NAME


class RepoMapStore:
    def __init__(self, folder: Path, max_entries: int = MAX_ENTRIES) -> None:
        self.folder = folder
        self.max_entries = max_entries

    def path(self, key: str) -> Path:
        return self.folder / f"{hashlib.sha256(key.encode('utf-8')).hexdigest()}.md"

    def get(self, key: str) -> str | None:
        """The map stored under key, marked as used now; None when there is none."""
        path = self.path(key)
        try:
            text = path.read_text(encoding="utf-8")
            os.utime(path)
        except FileNotFoundError:
            return None
        return text

    def put(self, key: str, text: str) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        path = self.path(key)
        partial = path.with_suffix(f".{os.getpid()}.{time.monotonic_ns()}.tmp")
        partial.write_text(text, encoding="utf-8", newline="\n")
        partial.replace(path)
        self.prune()

    def prune(self) -> None:
        maps = sorted(self.folder.glob("*.md"), key=modified)
        for path in maps[: max(len(maps) - self.max_entries, 0)]:
            path.unlink(missing_ok=True)


def modified(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except FileNotFoundError:
        return 0.0
