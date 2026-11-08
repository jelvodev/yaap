"""Import existing documents into a Blueprint layer.

A Markdown file, or a zip of them, is read; for each document the utility model proposes what kind of item it is,
a name, a summary and (for standards) the rules that must always hold. The user reviews the proposals before
anything is written. The document's own text becomes the body, unchanged.
"""

import asyncio
import json
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO

import yaml

from app.attachments.files import AttachmentError, safe_filename, unpack_archive
from app.core.utility import UtilityContext, run_utility
from app.blueprint.layers import BlueprintError, ItemKind, parse_document

MAX_DOCUMENTS = 60
MAX_IMPORT_BYTES = 20 * 1024 * 1024
MAX_DOCUMENT_CHARS = 200_000
PROPOSAL_EXCERPT = 12_000
CONCURRENT_PROPOSALS = 4
KINDS: tuple[ItemKind, ...] = ("instructions", "standard", "engineering", "skill", "command", "agent")
RULE_KINDS = ("standard", "engineering")
KIND_FOLDERS = {"standard": "standards", "engineering": "engineering", "command": "commands", "agent": "agents"}


class BlueprintImportError(Exception):
    """The upload cannot be imported; the message says why."""


@dataclass
class ImportDocument:
    source: str
    body: str
    front_matter: dict[str, Any] = field(default_factory=dict)


@dataclass
class Proposal:
    source: str
    kind: ItemKind
    name: str
    summary: str
    rules: list[str]
    body: str
    extra: dict[str, Any] = field(default_factory=dict)
    proposed_by_model: bool = False


def slug(text: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60].strip("-")
    return value or "document"


def read_document(source: str, text: str) -> ImportDocument | None:
    text = text[:MAX_DOCUMENT_CHARS]
    try:
        parsed = parse_document(text)
    except BlueprintError:
        return ImportDocument(source, text.strip())
    if not parsed.body.strip():
        return None
    return ImportDocument(source, parsed.body.strip(), dict(parsed.front_matter))


def collect_documents(upload: BinaryIO, filename: str) -> list[ImportDocument]:
    """The Markdown documents in the upload: the file itself, or every .md file in a zip."""
    name = safe_filename(filename)
    with tempfile.TemporaryDirectory(prefix="yaap-import-") as folder:
        target = Path(folder) / name
        size = 0
        with target.open("wb") as output:
            while chunk := upload.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_IMPORT_BYTES:
                    raise BlueprintImportError("The upload is larger than 20 MB.")
                output.write(chunk)
        if name.lower().endswith(".zip"):
            unpacked = Path(folder) / "unpacked"
            try:
                unpack_archive(target, unpacked)
            except AttachmentError as error:
                raise BlueprintImportError(str(error)) from error
            files = sorted(
                path for path in unpacked.rglob("*") if path.is_file() and path.suffix.lower() in (".md", ".markdown")
            )
            documents = [
                read_document(path.relative_to(unpacked).as_posix(), path.read_text(encoding="utf-8", errors="replace"))
                for path in files[:MAX_DOCUMENTS]
            ]
        elif name.lower().endswith((".md", ".markdown", ".txt")):
            documents = [read_document(name, target.read_text(encoding="utf-8", errors="replace"))]
        else:
            raise BlueprintImportError("Import a Markdown file (.md) or a zip of Markdown files.")
        shutil.rmtree(Path(folder) / "unpacked", ignore_errors=True)
    found = [document for document in documents if document is not None]
    if not found:
        raise BlueprintImportError("No Markdown documents with content were found.")
    return found


def guess_kind(document: ImportDocument) -> ItemKind:
    path = document.source.lower()
    name = PurePosixPath(path).name
    if name == "skill.md" or "/skills/" in f"/{path}":
        return "skill"
    for kind, words in (
        ("command", ("command",)),
        ("agent", ("agent", "role")),
        ("engineering", ("engineering",)),
        ("standard", ("standard", "rule", "convention", "guideline")),
    ):
        if any(word in path for word in words):
            return kind  # type: ignore[return-value]
    if name in ("agents.md", "claude.md", "instructions.md"):
        return "instructions"
    return "standard" if len(document.body) < 6000 else "engineering"


def guess_name(document: ImportDocument) -> str:
    path = PurePosixPath(document.source)
    if path.name.lower() == "skill.md" and path.parent.name:
        return slug(path.parent.name)
    heading = re.search(r"^#\s+(.+)$", document.body, re.MULTILINE)
    return slug(heading.group(1) if heading else path.stem)


def heuristic_proposal(document: ImportDocument) -> Proposal:
    front = document.front_matter
    rules = front.get("rules") if isinstance(front.get("rules"), list) else []
    return Proposal(
        source=document.source,
        kind=guess_kind(document),
        name=slug(str(front.get("name") or guess_name(document))),
        summary=str(front.get("summary") or front.get("description") or ""),
        rules=[str(rule) for rule in rules],
        body=document.body,
        extra={key: value for key, value in front.items() if key not in ("name", "summary", "description", "rules")},
    )


def parse_json_object(text: str) -> dict[str, Any] | None:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match is None:
        return None
    try:
        value = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


async def model_proposal(context: UtilityContext, environment_id: int, document: ImportDocument) -> Proposal:
    proposal = heuristic_proposal(document)
    if proposal.summary and (proposal.rules or proposal.kind not in ("standard", "engineering")):
        return proposal  # The document already says what it is.
    prompt = (
        "Classify this document for a personal development knowledge base and describe it. Kinds:\n"
        "- instructions: general instructions that always apply\n"
        "- standard: a short standard whose rules must always be followed\n"
        "- engineering: a longer engineering standard, loaded when needed\n"
        "- skill: a reusable way of working for a kind of task\n"
        "- command: a prompt template the user invokes\n"
        "- agent: a role definition for an assistant\n\n"
        'Reply with JSON only: {"kind": "...", "name": "lowercase-with-hyphens", "summary": "one sentence in '
        'English", "rules": ["up to 7 short rules in English that must always hold; only for standard and '
        'engineering, otherwise []"]}\n\n'
        f"File: {document.source}\n\n{document.body[:PROPOSAL_EXCERPT]}"
    )
    answer = await run_utility(context, environment_id, prompt, max_tokens=800, task="import")
    data = parse_json_object(answer or "")
    if data is None:
        return proposal
    kind = data.get("kind") if data.get("kind") in KINDS else proposal.kind
    rules = data.get("rules") if isinstance(data.get("rules"), list) else []
    return Proposal(
        source=document.source,
        kind=kind,
        name=slug(str(data.get("name") or proposal.name)),
        summary=str(data.get("summary") or proposal.summary).strip(),
        rules=[str(rule).strip() for rule in rules if str(rule).strip()][:10] if kind in RULE_KINDS else [],
        body=document.body,
        extra=proposal.extra,
        proposed_by_model=True,
    )


async def propose(
    documents: list[ImportDocument], context: UtilityContext | None, environment_id: int | None
) -> list[Proposal]:
    if context is None or environment_id is None:
        return [heuristic_proposal(document) for document in documents]
    limit = asyncio.Semaphore(CONCURRENT_PROPOSALS)

    async def one(document: ImportDocument) -> Proposal:
        async with limit:
            return await model_proposal(context, environment_id, document)

    return list(await asyncio.gather(*(one(document) for document in documents)))


def item_path(kind: ItemKind, name: str) -> str:
    if kind == "instructions":
        return "instructions.md"
    if kind == "skill":
        return f"skills/{name}/SKILL.md"
    return f"{KIND_FOLDERS[kind]}/{name}.md"


def render(kind: ItemKind, summary: str, rules: list[str], body: str, extra: dict[str, Any] | None = None) -> str:
    """The item file: front matter as the kind expects it, then the document's own text."""
    front: dict[str, Any] = dict(extra or {})
    if kind in ("standard", "engineering"):
        if summary:
            front["summary"] = summary
        if rules:
            front["rules"] = rules
    elif kind != "instructions" and summary:
        front["description"] = summary
    if not front:
        return body.strip() + "\n"
    header = yaml.safe_dump(front, allow_unicode=True, sort_keys=False, width=110).strip()
    return f"---\n{header}\n---\n\n{body.strip()}\n"
