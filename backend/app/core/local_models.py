"""The models YAAP downloads into its data folder: what each one is, its state, and downloading and removing it.

The search model (bge-m3) and the speech models are listed together. The files are fetched by `model_files`, one
download per model at a time, in a background thread. When the search model arrives or is removed, the listener is
told, so the index can start using it or let go of it.
"""

import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from app.core.embeddings import BGE_M3, model_folder
from app.core.model_files import ModelDownloads, RemoteModel, is_complete, staging_folder
from app.speech.whisper import MODELS as WHISPER_MODELS

ModelState = Literal["not_downloaded", "downloading", "ready", "failed"]

SEARCH_MODEL = "bge-m3"
WHISPER_FOLDER = "whisper"


class UnknownModel(KeyError):
    """No model has this name."""


class ModelBusy(RuntimeError):
    """The model is being downloaded and cannot be removed now."""


@dataclass(frozen=True)
class ModelEntry:
    name: str
    label: str
    purpose: str
    model: RemoteModel
    folder: Path


@dataclass(frozen=True)
class ModelStatus:
    name: str
    label: str
    purpose: str
    state: ModelState
    size: int
    downloaded: int
    message: str


def whisper_folder(models_dir: Path) -> Path:
    return models_dir / WHISPER_FOLDER


def catalog(models_dir: Path) -> dict[str, ModelEntry]:
    entries = {
        SEARCH_MODEL: ModelEntry(
            SEARCH_MODEL, "Search model (bge-m3)", "Search by meaning", BGE_M3, model_folder(models_dir)
        )
    }
    for name, model in WHISPER_MODELS.items():
        key = f"whisper-{name}"
        label = "Speech model (English only)" if name.endswith(".en") else "Speech model (multilingual)"
        entries[key] = ModelEntry(key, label, "Speaking to YAAP", model, whisper_folder(models_dir) / name)
    return entries


class ModelService:
    def __init__(
        self,
        models_dir: Path,
        downloads: ModelDownloads | None = None,
        on_change: Callable[[str], None] | None = None,
    ) -> None:
        self.models_dir = models_dir
        self.downloads = downloads or ModelDownloads()
        self.entries = catalog(models_dir)
        # Told the name of a model that arrived or was removed.
        self.on_change = on_change or (lambda _name: None)

    def entry(self, name: str) -> ModelEntry:
        try:
            return self.entries[name]
        except KeyError as error:
            raise UnknownModel(name) from error

    def is_ready(self, name: str) -> bool:
        entry = self.entry(name)
        return is_complete(entry.model, entry.folder)

    def status(self, name: str) -> ModelStatus:
        entry = self.entry(name)
        size = entry.model.size

        def make(state: ModelState, downloaded: int, message: str) -> ModelStatus:
            return ModelStatus(name, entry.label, entry.purpose, state, size, downloaded, message)

        download = self.downloads.get(entry.folder)
        if download is not None and download.running:
            return make("downloading", download.downloaded, "Downloading in the background.")
        if is_complete(entry.model, entry.folder):
            return make("ready", size, "Ready.")
        if download is not None and download.error:
            return make("failed", 0, f"The download failed: {download.error}")
        return make("not_downloaded", 0, "Not downloaded.")

    def statuses(self) -> list[ModelStatus]:
        return [self.status(name) for name in self.entries]

    def start_download(self, name: str) -> ModelStatus:
        """Start fetching the model in the background, unless it is there or on its way."""
        entry = self.entry(name)
        if not is_complete(entry.model, entry.folder):
            self.downloads.start(entry.model, entry.folder, then=lambda: self.on_change(name))
        return self.status(name)

    def remove(self, name: str) -> ModelStatus:
        entry = self.entry(name)
        download = self.downloads.get(entry.folder)
        if download is not None and download.running:
            raise ModelBusy(f"{entry.label} is being downloaded; wait for it to finish.")
        shutil.rmtree(entry.folder, ignore_errors=True)
        shutil.rmtree(staging_folder(entry.folder), ignore_errors=True)
        self.downloads.forget(entry.folder)
        self.on_change(name)
        return self.status(name)
