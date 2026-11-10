"""Model files fetched from Hugging Face: a pinned revision, a size and a SHA-256 per file.

Speech and semantic search share this module. A download goes into a staging folder next to its target and is moved
into place only when every file is complete and matches its hash, so a half-downloaded model is never loaded and
nothing that does not match what was pinned is ever kept.
"""

import hashlib
import logging
import shutil
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

Progress = Callable[[int], None]


@dataclass(frozen=True)
class ModelFile:
    name: str
    size: int
    sha256: str
    # The path in the repository when it differs from the name the file gets on disk.
    remote: str | None = None


@dataclass(frozen=True)
class RemoteModel:
    name: str
    repository: str
    revision: str
    files: tuple[ModelFile, ...]

    @property
    def size(self) -> int:
        return sum(item.size for item in self.files)

    def url(self, item: ModelFile) -> str:
        return f"https://huggingface.co/{self.repository}/resolve/{self.revision}/{item.remote or item.name}"


Fetch = Callable[[str, Path, ModelFile, Progress], None]


def fetch_file(url: str, destination: Path, expected: ModelFile, progress: Progress) -> None:
    """Download one file, reporting the bytes written; it must have the pinned size and SHA-256."""
    import httpx

    digest = hashlib.sha256()
    written = 0
    timeout = httpx.Timeout(30.0, read=120.0)
    with httpx.stream("GET", url, follow_redirects=True, timeout=timeout) as response:
        response.raise_for_status()
        with destination.open("wb") as handle:
            for chunk in response.iter_bytes(1 << 20):
                written += len(chunk)
                if written > expected.size:
                    raise ValueError(f"{expected.name} is larger than expected.")
                digest.update(chunk)
                handle.write(chunk)
                progress(len(chunk))
    if written != expected.size or digest.hexdigest() != expected.sha256:
        raise ValueError(f"{expected.name} does not match its pinned SHA-256.")


def is_complete(model: RemoteModel, folder: Path) -> bool:
    """Every file is there with its pinned size (the hash was checked when the file was downloaded)."""
    return all(
        (folder / item.name).is_file() and (folder / item.name).stat().st_size == item.size for item in model.files
    )


def staging_folder(target: Path) -> Path:
    return target.with_name(f"{target.name}.download")


def install_model(model: RemoteModel, target: Path, fetch: Fetch, progress: Progress) -> None:
    """Download every file into a staging folder, then move it to `target`. A failure leaves nothing behind."""
    staging = staging_folder(target)
    try:
        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True)
        for item in model.files:
            fetch(model.url(item), staging / item.name, item, progress)
        shutil.rmtree(target, ignore_errors=True)
        staging.rename(target)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


@dataclass
class Download:
    downloaded: int = 0
    error: str | None = None
    thread: threading.Thread | None = field(default=None, repr=False)

    @property
    def running(self) -> bool:
        return self.thread is not None and self.thread.is_alive()


class ModelDownloads:
    """Background downloads, at most one per target folder at a time."""

    def __init__(self, fetch: Fetch = fetch_file) -> None:
        self.fetch = fetch
        self.lock = threading.RLock()
        self.downloads: dict[Path, Download] = {}

    def get(self, target: Path) -> Download | None:
        with self.lock:
            return self.downloads.get(target)

    def start(self, model: RemoteModel, target: Path, then: Callable[[], None] | None = None) -> bool:
        """Start fetching `model` into `target`. False when one is running for that target already.

        `then` runs in the download's thread after the files are in place."""
        with self.lock:
            current = self.downloads.get(target)
            if current is not None and current.running:
                return False
            download = Download()
            download.thread = threading.Thread(
                target=self._run, args=(model, target, download, then), daemon=True, name=f"download-{model.name}"
            )
            self.downloads[target] = download
            download.thread.start()
        return True

    def _run(self, model: RemoteModel, target: Path, download: Download, then: Callable[[], None] | None) -> None:
        def progress(count: int) -> None:
            with self.lock:
                download.downloaded += count

        try:
            install_model(model, target, self.fetch, progress)
        except Exception as error:  # noqa: BLE001 - the state shows the reason and the user can try again
            with self.lock:
                download.error = str(error) or type(error).__name__
            return
        if then is not None:
            try:
                then()
            except Exception:  # noqa: BLE001 - the model is in place; a failing listener must not fail the download
                logger.exception("Work after the download of %s failed.", model.name)

    def forget(self, target: Path) -> None:
        with self.lock:
            self.downloads.pop(target, None)

    def wait(self, timeout: float = 10.0) -> None:
        """Wait for downloads in progress (tests)."""
        for download in list(self.downloads.values()):
            if download.thread is not None:
                download.thread.join(timeout)
