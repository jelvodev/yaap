"""Read another tool's setup (an upload or a project folder) and propose Blueprint items for it.

The files are untrusted data: nothing in them runs, only paths that `setup_layouts.recognise` knows are read, and
refused files (settings, hooks, MCP and auth files) are listed by path without being opened. The utility model
may fill in a summary and rules for a standard, but never changes an item's kind or name.
"""

import asyncio
import tempfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Literal

from app.attachments.files import AttachmentError, safe_filename, unpack_archive
from app.blueprint.importer import (
    CONCURRENT_PROPOSALS,
    MAX_DOCUMENTS,
    MAX_IMPORT_BYTES,
    PROPOSAL_EXCERPT,
    RULE_KINDS,
    BlueprintImportError,
    ImportDocument,
    Proposal,
    SkillFile,
    heuristic_proposal,
    model_proposal,
    parse_json_object,
    read_document,
    review_hidden,
    review_keys,
    slug,
)
from app.blueprint.setup_layouts import Layout, Recognition, recognise
from app.blueprint.setup_mapping import map_item
from app.blueprint.skill_install import (
    BINARY_SUFFIXES,
    MAX_SKILL_BYTES,
    MAX_SKILL_FILES,
    SCRIPT_SUFFIXES,
    TEXT_SUFFIXES,
)
from app.core.utility import UtilityContext, run_utility
from app.targets.base import TargetError, TargetFiles

Origin = Literal["home", "project"]
# Files that are read whole into memory; a Markdown document or a skill file larger than this is not a document.
MAX_SOURCE_BYTES = 1024 * 1024
MAX_PROJECT_ENTRIES = 5000
MAX_LISTED = 200
TOTAL_LIMIT_REASON = "Over the limit of 20 MB read for one import."
SINGLE_FILE_SUFFIXES = (".md", ".markdown", ".txt", ".mdc")


@dataclass(frozen=True)
class NotImported:
    path: str
    reason: str


@dataclass
class SetupPlan:
    """Which paths to read, decided from the paths and sizes alone."""

    wanted: list[tuple[str, Recognition]] = field(default_factory=list)
    not_imported: list[NotImported] = field(default_factory=list)
    skipped: int = 0


@dataclass
class SetupSource:
    documents: list[ImportDocument] = field(default_factory=list)
    not_imported: list[NotImported] = field(default_factory=list)
    skipped: int = 0


def skill_file_problem(inner: str, size: int, used_files: int, used_bytes: int) -> str | None:
    suffix = PurePosixPath(inner).suffix.lower()
    if suffix in BINARY_SUFFIXES:
        return "A binary file; YAAP does not import binaries."
    if suffix not in TEXT_SUFFIXES:
        return "Not a text file."
    if used_files >= MAX_SKILL_FILES or used_bytes + size > MAX_SKILL_BYTES:
        return "This skill is over the limit of 200 files and 5 MB."
    return None


def plan_setup(entries: list[tuple[str, int]], layout: Layout | None, allow_unknown: bool) -> SetupPlan:
    """Recognise every path. Refused paths are named, skipped ones counted; neither is read."""
    plan = SetupPlan()
    skill_usage: dict[str, tuple[int, int]] = {}
    documents = 0
    total = 0
    for path, size in sorted(entries):
        found = recognise(path, layout)
        if found.action == "refuse":
            plan.not_imported.append(NotImported(path, found.reason))
        elif found.action == "skill_file":
            prefix = path[: len(path) - len(found.inner) - 1]
            files, used = skill_usage.get(prefix, (0, 0))
            problem = skill_file_problem(found.inner, size, files, used)
            if problem is None and size > MAX_SOURCE_BYTES:
                problem = "Larger than 1 MB."
            elif problem is None and total + size > MAX_IMPORT_BYTES:
                problem = TOTAL_LIMIT_REASON
            if problem is not None:
                plan.not_imported.append(NotImported(path, problem))
                continue
            skill_usage[prefix] = (files + 1, used + size)
            total += size
            plan.wanted.append((path, found))
        elif found.action == "item" or (found.action == "unknown" and allow_unknown):
            if size > MAX_SOURCE_BYTES:
                plan.not_imported.append(NotImported(path, "Larger than 1 MB."))
            elif documents >= MAX_DOCUMENTS:
                plan.not_imported.append(NotImported(path, f"Over the limit of {MAX_DOCUMENTS} documents."))
            elif total + size > MAX_IMPORT_BYTES:
                plan.not_imported.append(NotImported(path, TOTAL_LIMIT_REASON))
            else:
                documents += 1
                total += size
                plan.wanted.append((path, found))
        else:
            plan.skipped += 1
    return plan


def skill_folder(path: str, found: Recognition) -> str:
    """The folder of the skill a path belongs to: the skill file's own folder, or the folder of its SKILL.md."""
    if found.action == "skill_file":
        return path[: len(path) - len(found.inner) - 1]
    return PurePosixPath(path).parent.as_posix()


def build_documents(plan: SetupPlan, contents: dict[str, bytes]) -> SetupSource:
    """The documents for the paths that were read; skill files are attached to their skill."""
    source = SetupSource(not_imported=list(plan.not_imported), skipped=plan.skipped)
    skills: dict[str, ImportDocument] = {}
    skill_files: list[tuple[str, Recognition, str]] = []
    for path, found in plan.wanted:
        data = contents.get(path)
        if data is None:
            source.not_imported.append(NotImported(path, "The file could not be read."))
        elif found.action == "skill_file":
            try:
                skill_files.append((path, found, data.decode("utf-8")))
            except UnicodeDecodeError:
                source.not_imported.append(NotImported(path, "Not UTF-8 text."))
        else:
            document = read_document(path, data.decode("utf-8", errors="replace"))
            if document is None:
                source.skipped += 1
                continue
            if found.action == "item":
                document.recognition = found
                if found.kind == "skill":
                    skills[skill_folder(path, found)] = document
            source.documents.append(document)
    for path, found, text in skill_files:
        owner = skills.get(skill_folder(path, found))
        if owner is None:
            source.skipped += 1
            continue
        script = PurePosixPath(found.inner).suffix.lower() in SCRIPT_SUFFIXES
        owner.files.append(SkillFile(found.inner, text, script=script, selected=not script))
    source.not_imported = source.not_imported[:MAX_LISTED]
    return source


def collect_upload(
    upload: BinaryIO, filename: str, layout: Layout | None, skip_folders: frozenset[str]
) -> SetupSource:
    """The setup in an uploaded Markdown file or zip."""
    name = safe_filename(filename)
    raw_name = PurePosixPath(filename.replace("\\", "/")).name
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
            source = collect_zip(target, Path(folder) / "unpacked", layout, skip_folders)
        elif name.lower().endswith(SINGLE_FILE_SUFFIXES) or recognise(raw_name, layout).action == "item":
            source = collect_single(target, raw_name, layout)
        else:
            raise BlueprintImportError("Import a Markdown file (.md) or a zip of your setup.")
    if not source.documents and not source.not_imported:
        raise BlueprintImportError("No Markdown documents with content were found.")
    return source


def collect_single(target: Path, name: str, layout: Layout | None) -> SetupSource:
    found = recognise(name, layout)
    document = read_document(name, target.read_text(encoding="utf-8", errors="replace"))
    if document is None:
        return SetupSource(skipped=1)
    if found.action == "item" and found.kind != "skill":
        document.recognition = found
    return SetupSource(documents=[document])


def collect_zip(target: Path, unpacked: Path, layout: Layout | None, skip_folders: frozenset[str]) -> SetupSource:
    try:
        details = unpack_archive(target, unpacked, skip_folders)
    except AttachmentError as error:
        raise BlueprintImportError(str(error)) from error
    files = [path for path in unpacked.rglob("*") if path.is_file()]
    entries = [(path.relative_to(unpacked).as_posix(), path.stat().st_size) for path in files]
    plan = plan_setup(entries, layout, allow_unknown=True)
    contents = {path: (unpacked / path).read_bytes() for path, _ in plan.wanted}
    source = build_documents(plan, contents)
    source.skipped += len(details.get("skipped", [])) + int(details.get("left_out", 0))
    return source


async def read_wanted(files: TargetFiles, plan: SetupPlan) -> dict[str, bytes]:
    contents: dict[str, bytes] = {}
    for path, _ in plan.wanted:
        try:
            contents[path] = await files.read_bytes(path)
        except (TargetError, OSError):
            continue  # Named as not imported by build_documents; one unreadable file does not stop the rest.
    return contents


async def plan_project(files: TargetFiles, skip: set[str], root: Path) -> SetupPlan:
    """The plan for a project folder in YAAP's container; symbolic links are left out, as the walk gives no flag."""
    walked = await files.walk(".", True, skip, MAX_PROJECT_ENTRIES)
    candidates = [entry for entry in walked if not entry.is_dir]
    entries = [(entry.path, entry.size) for entry in candidates if not (root / entry.path).is_symlink()]
    plan = plan_setup(entries, None, allow_unknown=False)
    plan.skipped += len(candidates) - len(entries)
    return plan


async def collect_project(files: TargetFiles, skip: set[str], root: Path) -> SetupSource:
    """The setup files in a project's own folder: only paths that are recognised are read."""
    plan = await plan_project(files, skip, root)
    return build_documents(plan, await read_wanted(files, plan))


async def count_project_items(files: TargetFiles, skip: set[str], root: Path) -> int:
    """How many items a project's folder holds, from the paths alone."""
    plan = await plan_project(files, skip, root)
    return sum(1 for _, found in plan.wanted if found.action == "item")


def recognised_proposal(document: ImportDocument) -> Proposal:
    found = document.recognition
    if found is None or found.kind is None:
        raise ValueError("A recognised document has a kind.")
    mapped = map_item(found.kind, found.format, document.front_matter, document.body)
    front = document.front_matter
    rules = front.get("rules") if found.kind in RULE_KINDS and isinstance(front.get("rules"), list) else []
    summary = mapped.summary or (str(front.get("summary") or "").strip() if found.kind in RULE_KINDS else "")
    return Proposal(
        source=document.source,
        kind=found.kind,
        name=slug(found.name),
        summary=summary,
        rules=[str(rule) for rule in rules],
        body=document.body,
        extra=dict(mapped.extra),
        tool=found.tool,
        notes=[*found.notes, *mapped.notes],
        files=document.files,
    )


async def fill_summary_and_rules(context: UtilityContext, environment_id: int, proposal: Proposal) -> Proposal:
    """For a standard that has no summary or rules: the model writes them. Kind and name stay as recognised."""
    if proposal.kind not in RULE_KINDS or (proposal.summary and proposal.rules):
        return proposal
    prompt = (
        "Describe this standard for a personal development knowledge base. Reply with JSON only: "
        '{"summary": "one sentence in English", "rules": ["up to 7 short rules in English that must always hold"]}'
        f"\n\nFile: {proposal.source}\n\n{proposal.body[:PROPOSAL_EXCERPT]}"
    )
    data = parse_json_object(await run_utility(context, environment_id, prompt, max_tokens=800, task="import") or "")
    if data is None:
        return proposal
    if not proposal.summary:
        proposal.summary = str(data.get("summary") or "").strip()
    if not proposal.rules and isinstance(data.get("rules"), list):
        proposal.rules = [str(rule).strip() for rule in data["rules"] if str(rule).strip()][:10]
    proposal.proposed_by_model = True
    return proposal


def untick_extra_instructions(proposals: list[Proposal]) -> None:
    """A layer has one instructions file: the shallowest stays ticked, the others are unticked with a note."""
    candidates = [item for item in proposals if item.kind == "instructions" and item.blocked is None]
    candidates.sort(key=lambda item: len(PurePosixPath(item.source).parts))
    for extra in candidates[1:]:
        extra.selected = False
        extra.notes.append("A layer has one instructions file; this one is not ticked because another was found.")


def untick_duplicates(proposals: list[Proposal]) -> None:
    """Two sources that would write the same file: the first stays ticked, the later ones are unticked."""
    first: dict[tuple[str, str], str] = {}
    for proposal in proposals:
        if proposal.kind == "instructions":
            continue
        owner = first.setdefault((proposal.kind, proposal.name), proposal.source)
        if owner != proposal.source:
            proposal.selected = False
            proposal.notes.append(f"Same name as {owner}; rename one to import both.")


async def propose_setup(
    documents: list[ImportDocument], origin: Origin, context: UtilityContext | None, environment_id: int | None
) -> list[Proposal]:
    limit = asyncio.Semaphore(CONCURRENT_PROPOSALS)

    async def one(document: ImportDocument) -> Proposal:
        async with limit:
            proposal = recognised_proposal(document) if document.recognition else heuristic_proposal(document)
            # Text that looks like a key never goes to the model.
            if review_keys(proposal).blocked is None and context is not None and environment_id is not None:
                if document.recognition is not None:
                    proposal = await fill_summary_and_rules(context, environment_id, proposal)
                else:
                    proposal = await model_proposal(context, environment_id, document)
            proposal.default_layer = "global" if origin == "home" else "level"
            return review_hidden(proposal)

    proposals = list(await asyncio.gather(*(one(document) for document in documents)))
    untick_extra_instructions(proposals)
    untick_duplicates(proposals)
    return proposals
