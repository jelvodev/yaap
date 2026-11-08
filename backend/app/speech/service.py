"""Speech input in YAAP: the model's state, its download on first use, and transcription.

One service per application, kept on `app.state.speech`. The models live under `<data>/models/whisper/<name>/`; a
download goes into `<name>.download/` first and is moved into place only when every file is complete and matches
its pinned hash, so a half-downloaded model is never loaded. A loaded model is unloaded again after 15 minutes
without use, to give its memory (about 500 MB) back.
"""

import logging
import shutil
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import numpy as np

from app.speech.whisper import (
    MODELS,
    SAMPLE_RATE,
    AudioError,
    ModelFile,
    Progress,
    Transcriber,
    WhisperModelSpec,
    decode_audio,
    fetch_file,
    load_whisper,
    package_available,
)

logger = logging.getLogger(__name__)

State = Literal["ready", "missing", "downloading", "failed", "unavailable"]

MIN_SECONDS = 0.3
MAX_SECONDS = 600
IDLE_SECONDS = 15 * 60
MB = 1_000_000


@dataclass(frozen=True)
class SpeechStatus:
    state: State
    model: str
    size: int
    downloaded: int
    message: str


@dataclass(frozen=True)
class Transcription:
    text: str
    language: str | None
    seconds: float
    model: str


class SpeechNotReady(RuntimeError):
    def __init__(self, status: SpeechStatus) -> None:
        super().__init__(status.message)
        self.status = status


@dataclass
class Download:
    downloaded: int = 0
    error: str | None = None
    thread: threading.Thread | None = None


Fetch = Callable[[str, Path, ModelFile, Progress], None]


class SpeechService:
    def __init__(
        self,
        folder: Path,
        models: dict[str, WhisperModelSpec] = MODELS,
        load_model: Callable[[Path], Transcriber] = load_whisper,
        fetch: Fetch = fetch_file,
        decode: Callable[[bytes], np.ndarray] = decode_audio,
        available: Callable[[], bool] = package_available,
        idle_seconds: float = IDLE_SECONDS,
    ) -> None:
        self.folder = folder
        self.models = models
        self.load_model = load_model
        self.fetch = fetch
        self.decode = decode
        self.available = available
        self.idle_seconds = idle_seconds
        self.lock = threading.RLock()
        # One transcription at a time: a model already uses several CPU threads.
        self.run_lock = threading.Lock()
        self.downloads: dict[str, Download] = {}
        self.loaded: dict[str, Transcriber] = {}
        self.last_used: dict[str, float] = {}
        self.timers: dict[str, threading.Timer] = {}

    def model_dir(self, name: str) -> Path:
        return self.folder / name

    def is_complete(self, spec: WhisperModelSpec) -> bool:
        folder = self.model_dir(spec.name)
        return all((folder / item.name).is_file() and (folder / item.name).stat().st_size == item.size for item in spec.files)

    def status(self, name: str) -> SpeechStatus:
        spec = self.models[name]
        size_text = f"{round(spec.size / MB)} MB"
        if not self.available():
            return SpeechStatus(
                "unavailable", name, spec.size, 0,
                "Speech input is not available: the faster-whisper package is not installed in YAAP.",
            )
        with self.lock:
            download = self.downloads.get(name)
            if download is not None and download.thread is not None and download.thread.is_alive():
                done = round(download.downloaded / MB)
                return SpeechStatus(
                    "downloading", name, spec.size, download.downloaded,
                    f"Downloading the speech model ({done} of {size_text}). This happens once.",
                )
            if self.is_complete(spec):
                return SpeechStatus("ready", name, spec.size, spec.size, "The speech model is ready.")
            if download is not None and download.error:
                return SpeechStatus(
                    "failed", name, spec.size, 0, f"The speech model could not be downloaded: {download.error}"
                )
        return SpeechStatus(
            "missing", name, spec.size, 0,
            f"The speech model ({spec.name}, {size_text}) is downloaded once, on first use, into the data folder.",
        )

    def start_download(self, name: str) -> SpeechStatus:
        """Start fetching the model in the background, unless it is there, on its way or cannot run."""
        with self.lock:
            current = self.status(name)
            if current.state in ("ready", "downloading", "unavailable"):
                return current
            download = Download()
            download.thread = threading.Thread(target=self._download, args=(name, download), daemon=True)
            self.downloads[name] = download
            download.thread.start()
        return self.status(name)

    def _download(self, name: str, download: Download) -> None:
        spec = self.models[name]
        staging = self.folder / f"{name}.download"
        try:
            shutil.rmtree(staging, ignore_errors=True)
            staging.mkdir(parents=True)

            def progress(count: int) -> None:
                with self.lock:
                    download.downloaded += count

            for item in spec.files:
                self.fetch(spec.url(item), staging / item.name, item, progress)
            target = self.model_dir(name)
            shutil.rmtree(target, ignore_errors=True)
            staging.rename(target)
            logger.info("Speech model %s downloaded to %s.", name, target)
        except Exception as error:  # noqa: BLE001 - the state shows the reason; the user can try again
            logger.warning("Speech model %s could not be downloaded: %s", name, error)
            shutil.rmtree(staging, ignore_errors=True)
            with self.lock:
                download.error = str(error) or type(error).__name__

    def wait(self, timeout: float = 10.0) -> None:
        """Wait for downloads in progress (tests)."""
        for download in list(self.downloads.values()):
            if download.thread is not None:
                download.thread.join(timeout)

    def transcribe(self, name: str, audio: bytes, language: str | None) -> Transcription:
        status = self.status(name)
        if status.state != "ready":
            raise SpeechNotReady(status)
        samples = self.decode(audio)
        seconds = len(samples) / SAMPLE_RATE
        if seconds < MIN_SECONDS:
            raise AudioError("The recording is too short; hold the microphone a little longer.")
        if seconds > MAX_SECONDS:
            raise AudioError(f"The recording is longer than {MAX_SECONDS // 60} minutes; record it in parts.")
        with self.run_lock:
            model = self.loaded.get(name)
            if model is None:
                model = self.load_model(self.model_dir(name))
                self.loaded[name] = model
            # The English-only model understands English only.
            text, heard = model.transcribe(samples, "en" if name.endswith(".en") else language)
            self._used(name)
        return Transcription(text=text, language=heard, seconds=round(seconds, 2), model=name)

    def _used(self, name: str) -> None:
        self.last_used[name] = time.monotonic()
        timer = self.timers.pop(name, None)
        if timer is not None:
            timer.cancel()
        timer = threading.Timer(self.idle_seconds, self._unload_if_idle, args=(name,))
        timer.daemon = True
        self.timers[name] = timer
        timer.start()

    def _unload_if_idle(self, name: str) -> None:
        with self.run_lock:
            if time.monotonic() - self.last_used.get(name, 0) >= self.idle_seconds - 1:
                if self.loaded.pop(name, None) is not None:
                    logger.info("Speech model %s unloaded after %d idle minutes.", name, self.idle_seconds // 60)
                self.timers.pop(name, None)

    def shutdown(self) -> None:
        for timer in self.timers.values():
            timer.cancel()
        self.timers.clear()
        self.loaded.clear()
