"""The local Whisper models and how they are fetched, decoded and run.

`faster-whisper` runs OpenAI's Whisper with CTranslate2 on the CPU, so audio never leaves the computer. The model is
not in the image: it is downloaded once, on first use, into the data folder, from a pinned revision of Systran's
CTranslate2 conversion on Hugging Face, and every file is checked against its size and SHA-256, as the embedding
model is. faster-whisper, CTranslate2 and PyAV are imported only when speech is used, so YAAP starts
and its tests run without them.
"""

import hashlib
import importlib.util
import io
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np

SAMPLE_RATE = 16_000


@dataclass(frozen=True)
class ModelFile:
    name: str
    size: int
    sha256: str


@dataclass(frozen=True)
class WhisperModelSpec:
    name: str
    repository: str
    revision: str
    files: tuple[ModelFile, ...]

    @property
    def size(self) -> int:
        return sum(item.size for item in self.files)

    def url(self, item: ModelFile) -> str:
        return f"https://huggingface.co/{self.repository}/resolve/{self.revision}/{item.name}"


# The small model (244M parameters): multilingual, and an English-only one that is more accurate in English.
# Revisions and hashes checked 2026-11-07.
MODELS: dict[str, WhisperModelSpec] = {
    "small": WhisperModelSpec(
        "small",
        "Systran/faster-whisper-small",
        "536b0662742c02347bc0e980a01041f333bce120",
        (
            ModelFile("config.json", 2_370, "b55496ac7940a7ae47d2c01eab40edfd8701feec1229d9cce3b40014383fb828"),
            ModelFile("model.bin", 483_546_902, "3e305921506d8872816023e4c273e75d2419fb89b24da97b4fe7bce14170d671"),
            ModelFile("tokenizer.json", 2_203_239, "fb7b63191e9bb045082c79fd742a3106a12c99513ab30df4a0d47fa6cb6fd0ab"),
            ModelFile("vocabulary.txt", 459_861, "34ce3fe1c5041027b3f8d42912270993f986dbc4bb34cf27f951e34a1e453913"),
        ),
    ),
    "small.en": WhisperModelSpec(
        "small.en",
        "Systran/faster-whisper-small.en",
        "d1d751a5f8271d482d14ca55d9e2deeebbae577f",
        (
            ModelFile("config.json", 2_657, "666a9605530ac1f61fa8177f3702b4dacec9966749e42610839fcc32661d5fae"),
            ModelFile("model.bin", 483_545_366, "62b2a45b05ee59acb4a5341b33ee35e041395d378d418a18acfe4c9e768ee37a"),
            ModelFile("tokenizer.json", 2_128_466, "929c5252409436dce1b38a75d1abbcb5e132d170d8e324e4e04ed915fa2d22df"),
            ModelFile("vocabulary.txt", 422_309, "ff77588746d3a2595d32ab5b69ffd7b95ce2441ac57533cb66fc3eb575a115cf"),
        ),
    ),
}

MULTILINGUAL = "small"
ENGLISH_ONLY = "small.en"


def model_for(language: str | None) -> str:
    """The English-only model when replies are in English; otherwise the multilingual one."""
    return ENGLISH_ONLY if language == "en" else MULTILINGUAL


class Transcriber(Protocol):
    """A loaded model: 16 kHz mono samples in, text and the language it heard out."""

    def transcribe(self, samples: np.ndarray, language: str | None) -> tuple[str, str | None]: ...


class WhisperTranscriber:
    def __init__(self, folder: Path) -> None:
        from faster_whisper import WhisperModel

        threads = max(1, min(4, os.cpu_count() or 1))
        self.model = WhisperModel(str(folder), device="cpu", compute_type="int8", cpu_threads=threads)

    def transcribe(self, samples: np.ndarray, language: str | None) -> tuple[str, str | None]:
        # The voice activity filter skips silence, which otherwise makes Whisper invent text.
        segments, info = self.model.transcribe(samples, language=language, beam_size=5, vad_filter=True)
        text = " ".join(segment.text.strip() for segment in segments).strip()
        return text, info.language


def load_whisper(folder: Path) -> Transcriber:
    return WhisperTranscriber(folder)


def package_available() -> bool:
    return all(importlib.util.find_spec(name) is not None for name in ("faster_whisper", "ctranslate2", "av"))


class AudioError(ValueError):
    """The upload is not audio YAAP can read, or it is too short or too long."""


def decode_audio(data: bytes) -> np.ndarray:
    """Any audio the browser records (WebM or Ogg with Opus, MP4 with AAC, WAV) as 16 kHz mono float samples.

    PyAV brings its own FFmpeg libraries, so the image needs no ffmpeg package. faster-whisper's own decode_audio is
    not used: it passes an option that newer PyAV versions no longer accept.
    """
    import av

    chunks: list[np.ndarray] = []
    try:
        with av.open(io.BytesIO(data), mode="r") as container:
            if not container.streams.audio:
                raise AudioError("The recording has no sound track.")
            resampler = av.AudioResampler(format="s16", layout="mono", rate=SAMPLE_RATE)
            for frame in container.decode(audio=0):
                chunks.extend(part.to_ndarray().reshape(-1) for part in resampler.resample(frame))
            chunks.extend(part.to_ndarray().reshape(-1) for part in resampler.resample(None))
    except AudioError:
        raise
    except Exception as error:  # noqa: BLE001 - PyAV raises many kinds of errors for files it cannot read
        raise AudioError("The recording could not be read as audio.") from error
    if not chunks:
        return np.zeros(0, dtype=np.float32)
    return np.concatenate(chunks).astype(np.float32) / 32768.0


Progress = Callable[[int], None]


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
