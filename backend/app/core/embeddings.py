"""Embedding models for semantic search.

An `Embedder` turns text into unit vectors. YAAP runs one embedding model for everything (setting `embedding_model`),
because a change of model means rebuilding the index. The model is `BAAI/bge-m3` (chosen 2026-11-06 by measuring on
YAAP's own data, see app/scripts/measure_embeddings.py), run locally with ONNX Runtime from files baked into the
image, so nothing is downloaded at runtime and no text leaves the machine.
"""

import logging
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

import numpy as np

from app.config import environment_value

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "bge-m3"
OFF = "off"
DIMENSIONS = 384
MAX_TOKENS = 512
BATCH_SIZE = 16
MAX_DISTANCE = 0.155
# Where the Dockerfile puts the model; YAAP_EMBEDDING_MODEL_DIR overrides it (local development).
DEFAULT_MODEL_DIR = Path("/opt/models/bge-m3")

Purpose = Literal["query", "passage"]


@dataclass(frozen=True)
class Embedding:
    vectors: list[np.ndarray]
    tokens: int


class Embedder(Protocol):
    name: str
    dimensions: int
    # The cosine distance up to which a chunk counts as matching a query; it depends on the model.
    max_distance: float

    def embed(self, texts: list[str], purpose: Purpose) -> Embedding: ...


@dataclass(frozen=True)
class ModelSpec:
    """How a local ONNX embedding model is run."""

    name: str
    dimensions: int
    # mean: average over the tokens (E5); cls: the first token (BGE).
    pooling: Literal["mean", "cls"]
    # E5 expects "query: " or "passage: " before every text; BGE-M3 needs nothing.
    prefixes: bool
    max_distance: float


MODELS = {
    # E5 similarities are compressed into a narrow, high band. Measured on YAAP data (2026-11-05): related chunks at
    # distance 0.12-0.15, unrelated queries from 0.16 up.
    "multilingual-e5-small": ModelSpec("multilingual-e5-small", 384, "mean", True, MAX_DISTANCE),
    "multilingual-e5-base": ModelSpec("multilingual-e5-base", 768, "mean", True, MAX_DISTANCE),
    # Measured on YAAP data (2026-11-06): right hits at 0.02-0.50, also from Dutch questions to English standards;
    # unrelated queries from 0.54 up.
    "bge-m3": ModelSpec("bge-m3", 1024, "cls", False, 0.52),
}


class OnnxEmbedder:
    """A local model run with ONNX Runtime: pooled over the last hidden state and normalized."""

    def __init__(self, model_dir: Path, spec: ModelSpec) -> None:
        import onnxruntime
        from tokenizers import Tokenizer

        self.spec = spec
        self.name = spec.name
        self.dimensions = spec.dimensions
        self.max_distance = spec.max_distance
        options = onnxruntime.SessionOptions()
        options.intra_op_num_threads = max(1, min(4, os.cpu_count() or 1))
        self.session = onnxruntime.InferenceSession(
            str(model_dir / "model.onnx"), options, providers=["CPUExecutionProvider"]
        )
        self.input_names = {item.name for item in self.session.get_inputs()}
        self.tokenizer = Tokenizer.from_file(str(model_dir / "tokenizer.json"))
        self.tokenizer.enable_truncation(MAX_TOKENS)
        self.tokenizer.enable_padding()
        # One inference at a time: the session uses several threads already.
        self.lock = threading.Lock()

    def embed(self, texts: list[str], purpose: Purpose) -> Embedding:
        vectors: list[np.ndarray] = []
        tokens = 0
        for start in range(0, len(texts), BATCH_SIZE):
            batch = texts[start : start + BATCH_SIZE]
            if self.spec.prefixes:
                batch = [f"{purpose}: {text}" for text in batch]
            encodings = self.tokenizer.encode_batch(batch)
            ids = np.array([encoding.ids for encoding in encodings], dtype=np.int64)
            mask = np.array([encoding.attention_mask for encoding in encodings], dtype=np.int64)
            tokens += int(mask.sum())
            inputs = {"input_ids": ids, "attention_mask": mask}
            if "token_type_ids" in self.input_names:
                inputs["token_type_ids"] = np.zeros_like(ids)
            with self.lock:
                hidden = self.session.run(None, inputs)[0]
            if self.spec.pooling == "cls":
                pooled = hidden[:, 0, :]
            else:
                summed = (hidden * mask[:, :, None]).sum(axis=1)
                pooled = summed / np.clip(mask.sum(axis=1, keepdims=True), 1, None)
            pooled = pooled / np.clip(np.linalg.norm(pooled, axis=1, keepdims=True), 1e-12, None)
            vectors.extend(row.astype(np.float32) for row in pooled)
        return Embedding(vectors, tokens)


def model_dir() -> Path:
    configured = environment_value("YAAP_EMBEDDING_MODEL_DIR")
    return Path(configured) if configured else DEFAULT_MODEL_DIR


def load_default_embedder() -> Embedder | None:
    """The local model, or None when its files are missing or it cannot run (search is then full-text only)."""
    folder = model_dir()
    if not (folder / "model.onnx").is_file() or not (folder / "tokenizer.json").is_file():
        logger.info("No embedding model in %s; search is full-text only.", folder)
        return None
    try:
        return OnnxEmbedder(folder, MODELS[DEFAULT_MODEL])
    except Exception:  # noqa: BLE001 - any failure to load means: no semantic search, YAAP keeps running
        logger.exception("The embedding model in %s could not be loaded; search is full-text only.", folder)
        return None


def to_blob(vector: np.ndarray) -> bytes:
    return np.asarray(vector, dtype=np.float32).tobytes()
