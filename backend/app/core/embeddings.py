"""Embedding models for semantic search.

An `Embedder` turns text into unit vectors. YAAP runs one embedding model for everything (setting `embedding_model`),
because a change of model means rebuilding the index. The model is `BAAI/bge-m3` (chosen by measuring on
YAAP's own data, see app/scripts/measure_embeddings.py), run locally with ONNX Runtime from files downloaded once
into the data folder (app/core/local_models), so no text leaves the machine.
"""

import logging
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

import numpy as np

from app.config import environment_value
from app.core.model_files import ModelFile, RemoteModel, is_complete

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "bge-m3"
OFF = "off"
DIMENSIONS = 384
MAX_TOKENS = 512
BATCH_SIZE = 16
MAX_DISTANCE = 0.155

# The pinned files of BAAI/bge-m3 (ONNX): the revision and the SHA-256 of every file are what YAAP runs, nothing else.
# They sit in the repository's onnx/ folder and are kept flat on disk.
BGE_M3 = RemoteModel(
    "bge-m3",
    "BAAI/bge-m3",
    "5617a9f61b028005a4858fdac845db406aefb181",
    (
        ModelFile(
            "model.onnx", 724_923, "f84251230831afb359ab26d9fd37d5936d4d9bb5d1d5410e66442f630f24435b", "onnx/model.onnx"
        ),
        ModelFile(
            "model.onnx_data",
            2_266_820_608,
            "1eebfb28493f67bba03ce0ef64bfdc7fc5a3bd9d7493f818bb1d78cd798416b4",
            "onnx/model.onnx_data",
        ),
        ModelFile(
            "Constant_7_attr__value",
            65_552,
            "cdf16f72c5d07b36484056e601ed9687f78477e5d85cee85a34f2406b7fb5906",
            "onnx/Constant_7_attr__value",
        ),
        ModelFile(
            "tokenizer.json", 17_082_821, "6710678b12670bc442b99edc952c4d996ae309a7020c1fa0096dd245c2faf790", "onnx/tokenizer.json"
        ),
    ),
)

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
    # E5 similarities are compressed into a narrow, high band. Measured on YAAP data: related chunks at
    # distance 0.12-0.15, unrelated queries from 0.16 up.
    "multilingual-e5-small": ModelSpec("multilingual-e5-small", 384, "mean", True, MAX_DISTANCE),
    "multilingual-e5-base": ModelSpec("multilingual-e5-base", 768, "mean", True, MAX_DISTANCE),
    # Measured on YAAP data: right hits at 0.02-0.50, also from Dutch questions to English standards;
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


def model_folder(models_dir: Path) -> Path:
    """Where the model lives: `<models>/bge-m3/<revision>`; YAAP_EMBEDDING_MODEL_DIR overrides it (development)."""
    configured = environment_value("YAAP_EMBEDDING_MODEL_DIR")
    return Path(configured) if configured else models_dir / BGE_M3.name / BGE_M3.revision


def model_present(folder: Path) -> bool:
    return is_complete(BGE_M3, folder)


def load_default_embedder(folder: Path) -> Embedder | None:
    """The local model, or None when its files are missing or it cannot run (search is then full-text only)."""
    if not model_present(folder):
        logger.info("No embedding model in %s yet; search is full-text only.", folder)
        return None
    try:
        return OnnxEmbedder(folder, MODELS[DEFAULT_MODEL])
    except Exception:  # noqa: BLE001 - any failure to load means: no semantic search, YAAP keeps running
        logger.exception("The embedding model in %s could not be loaded; search is full-text only.", folder)
        return None


def to_blob(vector: np.ndarray) -> bytes:
    return np.asarray(vector, dtype=np.float32).tobytes()
