"""Search the text of conversations in an environment.

The `message_search` FTS5 table (migration 0010) holds each message's text blocks and attachment names. A query is
split into words; every word must occur, and the last one also matches as a prefix, so results appear while typing.
"""

import re
from pathlib import Path
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.core.embeddings import to_blob

# Markers around matched words in a snippet; the interface turns them into highlights.
MATCH_START = "\x02"
MATCH_END = "\x03"
MAX_RESULTS = 50
WORD = re.compile(r"[\w][\w'-]*", re.UNICODE)


@dataclass
class SearchHit:
    conversation_id: int
    conversation_title: str
    project_id: int | None
    message_id: int
    role: str
    snippet: str
    created_at: datetime


def match_expression(query: str) -> str | None:
    """The FTS5 expression for what the user typed: each word quoted, the last one as a prefix."""
    words = WORD.findall(query)[:12]
    if not words:
        return None
    quoted = [f'"{word.replace(chr(34), "")}"' for word in words]
    quoted[-1] += "*"
    return " ".join(quoted)


def search_messages(
    session: Session, environment_id: int, query: str, project_id: int | None = None, limit: int = MAX_RESULTS
) -> list[SearchHit]:
    expression = match_expression(query)
    if expression is None:
        return []
    project_filter = "AND conversations.project_id = :project_id" if project_id is not None else ""
    rows = session.execute(
        text(
            f"""
            SELECT conversations.id, conversations.title, conversations.project_id, messages.id, messages.role,
                   snippet(message_search, 0, :start, :end, '…', 16), messages.created_at
            FROM message_search
            JOIN messages ON messages.id = message_search.rowid
            JOIN conversations ON conversations.id = messages.conversation_id
            WHERE message_search MATCH :expression
              AND conversations.environment_id = :environment_id
              {project_filter}
            ORDER BY bm25(message_search), messages.created_at DESC
            LIMIT :limit
            """
        ),
        {
            "start": MATCH_START,
            "end": MATCH_END,
            "expression": expression,
            "environment_id": environment_id,
            "project_id": project_id,
            "limit": max(1, min(limit, MAX_RESULTS)),
        },
    )
    return [
        SearchHit(
            conversation_id=row[0],
            conversation_title=row[1],
            project_id=row[2],
            message_id=row[3],
            role=row[4],
            snippet=row[5] or "",
            created_at=row[6] if isinstance(row[6], datetime) else datetime.fromisoformat(str(row[6])),
        )
        for row in rows
    ]


# --- Search across conversations, project memory and the Blueprint ----------------------------------

KINDS = ("message", "memory", "knowledge", "blueprint")
RRF_K = 60
VECTOR_CANDIDATES = 200
VECTOR_RESULTS = 20
SNIPPET_CHARS = 240


@dataclass
class FoundItem:
    """One result: a message, a project's memory, a project knowledge document or a Blueprint file."""

    kind: str
    source_key: str
    snippet: str
    score: float
    environment_id: int | None = None
    project_id: int | None = None
    conversation_id: int | None = None
    conversation_title: str | None = None
    message_id: int | None = None
    role: str | None = None
    path: str | None = None
    created_at: datetime | None = None


def scope_filter(kinds: tuple[str, ...], project_id: int | None) -> str:
    """SQL on search_chunks (as c) for the kinds asked and what one environment sees: its own chunks, global
    Blueprint files and, for a project, only that project's chunks and the Blueprint levels above it."""
    kind_list = ", ".join(f"'{kind}'" for kind in kinds if kind in KINDS) or "''"
    parts = [
        f"c.kind IN ({kind_list})",
        "(c.environment_id = :environment_id OR (c.kind = 'blueprint' AND c.environment_id IS NULL))",
    ]
    if project_id is not None:
        parts.append("(c.project_id = :project_id OR (c.kind = 'blueprint' AND c.project_id IS NULL))")
    return " AND ".join(parts)


def plain_snippet(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= SNIPPET_CHARS else text[: SNIPPET_CHARS - 1].rstrip() + "…"


def keyword_chunks(
    session: Session, environment_id: int, query: str, kinds: tuple[str, ...], project_id: int | None
) -> list[tuple[str, str]]:
    """(source key, snippet) of memory and Blueprint chunks matching the words, best first."""
    expression = match_expression(query)
    wanted = tuple(kind for kind in kinds if kind != "message")
    if expression is None or not wanted:
        return []
    rows = session.execute(
        text(
            f"""
            SELECT c.source_key, snippet(chunk_search, 0, :start, :end, '…', 16)
            FROM chunk_search JOIN search_chunks AS c ON c.id = chunk_search.rowid
            WHERE chunk_search MATCH :expression AND {scope_filter(wanted, project_id)}
            ORDER BY bm25(chunk_search)
            LIMIT 100
            """
        ),
        {
            "start": MATCH_START,
            "end": MATCH_END,
            "expression": expression,
            "environment_id": environment_id,
            "project_id": project_id,
        },
    )
    return [(row[0], row[1] or "") for row in rows]


@dataclass(frozen=True)
class QueryVector:
    vector: bytes
    model: str
    # Chunks further away than this (cosine distance) do not count as a match.
    max_distance: float


def vector_chunks(
    session: Session, query: QueryVector, environment_id: int, kinds: tuple[str, ...], project_id: int | None
) -> list[tuple[str, str]]:
    """(source key, chunk text) of the chunks closest in meaning, best first."""
    rows = session.execute(
        text(
            f"""
            SELECT c.source_key, c.text, vec_distance_cosine(c.embedding, :vector) AS distance
            FROM search_chunks AS c
            WHERE c.embedding IS NOT NULL AND c.model = :model AND {scope_filter(kinds, project_id)}
            ORDER BY distance
            LIMIT :limit
            """
        ),
        {
            "vector": query.vector,
            "model": query.model,
            "environment_id": environment_id,
            "project_id": project_id,
            "limit": VECTOR_CANDIDATES,
        },
    )
    return [(row[0], row[1]) for row in rows if row[2] is not None and row[2] <= query.max_distance]


def first_per_source(rows: list[tuple[str, str]], limit: int | None = None) -> list[tuple[str, str]]:
    seen: dict[str, str] = {}
    for key, snippet in rows:
        if key not in seen:
            seen[key] = snippet
            if limit is not None and len(seen) >= limit:
                break
    return list(seen.items())


def search_everything(
    session: Session,
    environment_id: int,
    query: str,
    query_vector: QueryVector | None = None,
    project_id: int | None = None,
    kinds: tuple[str, ...] = KINDS,
    limit: int = MAX_RESULTS,
) -> list[FoundItem]:
    """Full-text and semantic results fused by reciprocal rank. Without `query_vector` the search is full-text only."""
    message_hits = (
        {f"message:{hit.message_id}": hit for hit in search_messages(session, environment_id, query, project_id)}
        if "message" in kinds
        else {}
    )
    keyword = first_per_source(keyword_chunks(session, environment_id, query, kinds, project_id))
    rankings: list[list[str]] = [list(message_hits), [key for key, _ in keyword]]
    snippets: dict[str, str] = dict(keyword)
    if query_vector is not None:
        semantic = first_per_source(vector_chunks(session, query_vector, environment_id, kinds, project_id), VECTOR_RESULTS)
        rankings.append([key for key, _ in semantic])
        for key, chunk in semantic:
            snippets.setdefault(key, plain_snippet(chunk))
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, key in enumerate(ranking):
            scores[key] = scores.get(key, 0.0) + 1.0 / (RRF_K + rank + 1)
    ordered = sorted(scores, key=lambda key: scores[key], reverse=True)[: max(1, min(limit, MAX_RESULTS))]
    return [item for key in ordered if (item := found_item(session, key, scores[key], message_hits, snippets))]


def found_item(
    session: Session, key: str, score: float, message_hits: dict[str, SearchHit], snippets: dict[str, str]
) -> FoundItem | None:
    hit = message_hits.get(key)
    if hit is not None:
        return FoundItem(
            "message", key, hit.snippet, score, None, hit.project_id, hit.conversation_id, hit.conversation_title,
            hit.message_id, hit.role, None, hit.created_at,
        )
    row = session.execute(
        text(
            """
            SELECT c.kind, c.environment_id, c.project_id, c.conversation_id, c.message_id, c.path,
                   conversations.title, messages.role, messages.created_at
            FROM search_chunks AS c
            LEFT JOIN conversations ON conversations.id = c.conversation_id
            LEFT JOIN messages ON messages.id = c.message_id
            WHERE c.source_key = :key
            ORDER BY c.chunk_no
            LIMIT 1
            """
        ),
        {"key": key},
    ).first()
    if row is None:
        return None
    created_at = row[8] if isinstance(row[8], datetime) or row[8] is None else datetime.fromisoformat(str(row[8]))
    return FoundItem(
        row[0], key, snippets.get(key, ""), score, row[1], row[2], row[3], row[6], row[4], row[7], row[5], created_at
    )


def embed_query(indexer: object | None, session: Session, query: str) -> QueryVector | None:
    """The query's embedding, or None when there is no embedding model (full-text only)."""
    embedder = indexer.embedder(session) if indexer is not None and query.strip() else None  # type: ignore[attr-defined]
    if embedder is None:
        return None
    vector = to_blob(embedder.embed([query.strip()], "query").vectors[0])
    return QueryVector(vector, embedder.name, embedder.max_distance)


def recall_project_memory(
    session: Session, indexer: object | None, environment_id: int, project_id: int, memory: str, query: str
):
    """The project memory as it goes into the prompt: whole while small, else the entries that matter
    for this message, found by meaning through the memory's search chunks when there is a model."""
    from app.projects.memory import RECALL_TOKENS, estimate_tokens, recall

    chunks: list[str] = []
    if estimate_tokens(memory) > RECALL_TOKENS and query.strip():
        vector = embed_query(indexer, session, query)
        if vector is not None:
            chunks = [chunk for _, chunk in vector_chunks(session, vector, environment_id, ("memory",), project_id)]
    return recall(memory, query, chunks)


@dataclass
class RunSearch:
    """What search_project and read_knowledge need: the database, the search index and the knowledge folder."""

    session_factory: sessionmaker[Session]
    indexer: object | None
    data_dir: Path | None = None

    def knowledge(self, project_id: int, name: str) -> tuple[str, str] | None:
        """A knowledge document's name and text, found by (part of) its name."""
        from app.knowledge.service import find_document, read_text

        if self.data_dir is None:
            return None
        with self.session_factory() as session:
            document = find_document(session, project_id, name)
            return (document.name, read_text(self.data_dir, document)) if document is not None else None

    def knowledge_names(self, project_id: int) -> list[str]:
        from app.knowledge.service import project_documents

        with self.session_factory() as session:
            return [document.name for document in project_documents(session, project_id)]

    def search(
        self, environment_id: int, project_id: int, query: str, kinds: tuple[str, ...], exclude_conversation: int | None
    ) -> list[FoundItem]:
        with self.session_factory() as session:
            items = search_everything(
                session, environment_id, query, embed_query(self.indexer, session, query), project_id, kinds, limit=12
            )
        return [item for item in items if item.conversation_id is None or item.conversation_id != exclude_conversation]
