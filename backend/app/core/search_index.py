"""The search index: chunks of messages, project memory and Blueprint files.

Indexing runs in the background. A pass embeds the messages that have no chunks yet, and compares the memory and
Blueprint files with the index by content hash, so only what changed is embedded again. Passes run on start, every
minute, and right after a reply ends (`nudge`). Memory and Blueprint chunks are kept even when no embedding model is
available, so their full-text search works without one; messages have their own full-text index.

Embedding uses the local model, so it costs nothing; its tokens are recorded as usage with purpose `embedding` per
environment. Global Blueprint files belong to no environment, so their tokens are not recorded.
"""

import asyncio
import hashlib
import logging
import re
import threading
from collections import defaultdict
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import anyio
from sqlalchemy import delete, exists, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.embeddings import OFF, Embedder, to_blob
from app.core.settings import resolve_text
from app.blueprint.layers import Layer, layer_files
from app.blueprint.levels import environment_layer, global_layer, project_layer
from app.knowledge.service import MAX_INDEXED_CHARS, project_documents, read_text, sync_knowledge_folder
from app.projects.memory import has_entries, read_memory
from app.projects.service import ProjectError, project_path
from app.storage.models import Conversation, Environment, Message, Project, SearchChunk, UsageRecord

logger = logging.getLogger(__name__)

CHUNK_CHARS = 1200
MAX_SOURCE_CHARS = 64_000
MESSAGE_BATCH = 64
PASS_INTERVAL_SECONDS = 60


def chunk_text(text: str, max_chars: int = MAX_SOURCE_CHARS) -> list[str]:
    """Pieces of about CHUNK_CHARS, split at paragraphs, and inside a long paragraph at a space."""
    pieces: list[str] = []
    for paragraph in re.split(r"\n\s*\n", text[:max_chars]):
        paragraph = paragraph.strip()
        while len(paragraph) > CHUNK_CHARS:
            cut = paragraph.rfind(" ", 0, CHUNK_CHARS)
            if cut < CHUNK_CHARS // 2:
                cut = CHUNK_CHARS
            pieces.append(paragraph[:cut].strip())
            paragraph = paragraph[cut:].strip()
        if paragraph:
            pieces.append(paragraph)
    chunks: list[str] = []
    current = ""
    for piece in pieces:
        if current and len(current) + len(piece) + 2 > CHUNK_CHARS:
            chunks.append(current)
            current = piece
        else:
            current = f"{current}\n\n{piece}" if current else piece
    if current:
        chunks.append(current)
    return chunks


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def message_text(message: Message) -> str:
    return "\n\n".join(
        str(block.get("text") or "") for block in message.content if block.get("type") in ("text", "steering")
    ).strip()


def blueprint_key(environment_id: int | None, project_id: int | None, path: str) -> str:
    return f"blueprint:{environment_id or 'global'}:{project_id or '-'}:{path}"


@dataclass(frozen=True)
class Source:
    kind: str
    source_key: str
    text: str
    environment_id: int | None
    project_id: int | None = None
    conversation_id: int | None = None
    message_id: int | None = None
    path: str | None = None
    # How much of the text is indexed.
    max_chars: int = MAX_SOURCE_CHARS


class SearchIndexer:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        blueprint_dir: Path,
        workspace_dir: Path,
        load_embedder: Callable[[], Embedder | None],
        data_dir: Path | None = None,
    ) -> None:
        self.data_dir = data_dir
        self.session_factory = session_factory
        self.blueprint_dir = blueprint_dir
        self.workspace_dir = workspace_dir
        self.load_embedder = load_embedder
        self._embedder: Embedder | None = None
        self._loaded = False
        self._load_lock = threading.Lock()
        self._pass_lock = threading.Lock()
        self._wake: asyncio.Event | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    # --- The model -----------------------------------------------------------------------------------------------

    def set_embedder(self, embedder: Embedder | None) -> None:
        """Use this embedder instead of loading the default one (tests)."""
        with self._load_lock:
            self._embedder, self._loaded = embedder, True

    def embedder(self, session: Session) -> Embedder | None:
        """The embedding model in use, or None when it is switched off or not available."""
        if resolve_text(session, "embedding_model", None) == OFF:
            return None
        with self._load_lock:
            if not self._loaded:
                self._embedder = self.load_embedder()
                self._loaded = True
            return self._embedder

    # --- Passes --------------------------------------------------------------------------------------------------

    def index_pass(self) -> int:
        """Bring the index up to date. Returns the number of chunks written."""
        with self._pass_lock, self.session_factory() as session:
            embedder = self.embedder(session)
            tokens: dict[int, int] = defaultdict(int)
            written = self._index_files(session, embedder, tokens)
            if embedder is not None:
                written += self._index_messages(session, embedder, tokens)
            for environment_id, count in tokens.items():
                session.add(
                    UsageRecord(
                        environment_id=environment_id,
                        provider="local",
                        model=embedder.name if embedder else "",
                        purpose="embedding",
                        input_tokens=count,
                        cost=0.0,
                    )
                )
            session.commit()
            return written

    def _index_files(self, session: Session, embedder: Embedder | None, tokens: dict[int, int]) -> int:
        model = embedder.name if embedder else None
        sources = list(self.file_sources(session))
        known = {
            key: (content_hash, chunk_model)
            for key, content_hash, chunk_model in session.execute(
                select(SearchChunk.source_key, SearchChunk.content_hash, SearchChunk.model)
                .where(SearchChunk.kind != "message")
                .distinct()
            )
        }
        current = {source.source_key for source in sources}
        gone = [key for key in known if key not in current]
        for start in range(0, len(gone), 200):
            session.execute(delete(SearchChunk).where(SearchChunk.source_key.in_(gone[start : start + 200])))
        written = 0
        for source in sources:
            if known.get(source.source_key) == (text_hash(source.text), model):
                continue
            written += self._store(session, source, embedder, tokens)
        session.commit()
        return written

    def _index_messages(self, session: Session, embedder: Embedder, tokens: dict[int, int]) -> int:
        # Chunks of another model are embedded again.
        session.execute(
            delete(SearchChunk).where(
                SearchChunk.kind == "message", (SearchChunk.model != embedder.name) | SearchChunk.model.is_(None)
            )
        )
        written = 0
        while True:
            rows = session.execute(
                select(Message, Conversation.environment_id, Conversation.project_id)
                .join(Conversation, Conversation.id == Message.conversation_id)
                .where(
                    Message.status == "complete",
                    ~exists(select(SearchChunk.id).where(SearchChunk.message_id == Message.id)),
                )
                .order_by(Message.id)
                .limit(MESSAGE_BATCH)
            ).all()
            if not rows:
                return written
            for message, environment_id, project_id in rows:
                source = Source(
                    "message",
                    f"message:{message.id}",
                    message_text(message),
                    environment_id,
                    project_id,
                    conversation_id=message.conversation_id,
                    message_id=message.id,
                )
                written += self._store(session, source, embedder, tokens)
            session.commit()

    def _store(self, session: Session, source: Source, embedder: Embedder | None, tokens: dict[int, int]) -> int:
        """Replace the chunks of one source. A source without text gets one empty chunk, so it is not tried again.

        The embedding is computed before anything is written, and the source is committed on its own: a write keeps
        SQLite locked for every other writer, and embedding a large source takes seconds."""
        session.commit()
        chunks = chunk_text(source.text, source.max_chars)
        vectors: list[bytes | None] = [None] * len(chunks)
        if embedder is not None and chunks:
            embedding = embedder.embed(chunks, "passage")
            vectors = [to_blob(vector) for vector in embedding.vectors]
            if source.environment_id is not None:
                tokens[source.environment_id] += embedding.tokens
        now = datetime.now(UTC)
        content_hash = text_hash(source.text)
        model = embedder.name if embedder else None
        session.execute(delete(SearchChunk).where(SearchChunk.source_key == source.source_key))
        for number, (text, vector) in enumerate(zip(chunks or [""], vectors or [None], strict=True)):
            session.add(
                SearchChunk(
                    kind=source.kind,
                    source_key=source.source_key,
                    chunk_no=number,
                    environment_id=source.environment_id,
                    project_id=source.project_id,
                    conversation_id=source.conversation_id,
                    message_id=source.message_id,
                    path=source.path,
                    text=text,
                    content_hash=content_hash,
                    model=model,
                    embedding=vector,
                    created_at=now,
                )
            )
        session.commit()
        return len(chunks)

    def file_sources(self, session: Session) -> Iterator[Source]:
        """The Blueprint files of every level, and the memory and knowledge documents of every project."""
        yield from self._layer_sources(global_layer(self.blueprint_dir), None, None)
        for environment in session.scalars(select(Environment).order_by(Environment.id)):
            yield from self._layer_sources(environment_layer(self.blueprint_dir, environment), environment.id, None)
        for project in session.scalars(select(Project).order_by(Project.id)):
            try:
                folder = project_path(self.workspace_dir, project)
            except ProjectError:
                continue
            yield from self._layer_sources(project_layer(self.workspace_dir, project), project.environment_id, project.id)
            memory = read_memory(folder)
            if has_entries(memory):
                yield Source("memory", f"memory:{project.id}", memory, project.environment_id, project.id)
            if self.data_dir is not None:
                sync_knowledge_folder(session, self.data_dir, project, folder)
                for document in project_documents(session, project.id):
                    yield Source(
                        "knowledge",
                        f"knowledge:{document.id}",
                        read_text(self.data_dir, document),
                        project.environment_id,
                        project.id,
                        path=document.name,
                        max_chars=MAX_INDEXED_CHARS,
                    )

    def _layer_sources(self, layer: Layer, environment_id: int | None, project_id: int | None) -> Iterator[Source]:
        for path, file in sorted(layer_files(layer).items()):
            try:
                text = file.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            yield Source(
                "blueprint", blueprint_key(environment_id, project_id, path), text, environment_id, project_id, path=path
            )

    # --- Background ----------------------------------------------------------------------------------------------

    async def run_periodically(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._wake = asyncio.Event()
        while True:
            try:
                await anyio.to_thread.run_sync(self.index_pass)
            except Exception:  # noqa: BLE001 - the next pass tries again
                logger.exception("Indexing for search failed.")
            try:
                await asyncio.wait_for(self._wake.wait(), PASS_INTERVAL_SECONDS)
            except TimeoutError:
                pass
            self._wake.clear()

    def nudge(self) -> None:
        """Run a pass soon, from any thread: a reply ended or a file changed."""
        if self._loop is not None and self._wake is not None and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self._wake.set)
