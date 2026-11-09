"""YAAP's starter Blueprint (blueprint/starter/): every pack is a valid, text-only package whose items are complete
and name their sources."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from app.agent.tools import TOOLS_BY_NAME
from app.agents.definitions import text_list
from app.blueprint.layers import Document, parse_document
from app.blueprint.packages import MANIFEST, MAX_ITEMS, PackageError, parse_manifest
from app.blueprint.skill_install import BINARY_SUFFIXES, RUN_TEXT, SCRIPT_SUFFIXES
from app.presets.service import DEFAULT_PRESETS

STARTER = Path(__file__).resolve().parents[2] / "blueprint" / "starter"
PACKS = sorted(manifest.parent for manifest in STARTER.glob(f"*/{MANIFEST}"))
# Files in a pack's own folder that are about the pack, not items of it.
PACK_FILES = {MANIFEST, "README.md", "NOTICE.md"}
# The keys of the source repositories (docs/credits.md); `own` is YAAP's own work.
SOURCE_PREFIXES = ("superpowers/", "superpowers-skills/", "agency-agents/", "wshobson/", "anthropic-skills/")
LICENSES = {"MIT", "Apache-2.0"}
PRESET_NAMES = {name for name, *_ in DEFAULT_PRESETS}
MAX_DESCRIPTION = 1024
MAX_SKILL_LINES = 500
MAX_RULES = 5


@dataclass(frozen=True)
class StarterItem:
    pack: Path
    kind: str
    # As in the manifest: the file of a single-file item, the folder of a skill.
    path: str

    @property
    def file(self) -> Path:
        return self.pack / self.path / "SKILL.md" if self.kind == "skill" else self.pack / self.path

    @property
    def label(self) -> str:
        return f"{self.pack.name}/{self.path}"


def listed_items(pack: Path) -> list[StarterItem]:
    """The items a pack's manifest lists; none when it does not parse, which its own test reports."""
    try:
        manifest = parse_manifest((pack / MANIFEST).read_bytes())
    except PackageError:
        return []
    return [StarterItem(pack, item.kind, item.path) for item in manifest.items]


ITEMS = [item for pack in PACKS for item in listed_items(pack)]


def items_of(*kinds: str) -> list[Any]:
    return [pytest.param(item, id=item.label) for item in ITEMS if item.kind in kinds]


def packs() -> list[Any]:
    return [pytest.param(pack, id=pack.name) for pack in PACKS]


def pack_files(pack: Path) -> list[Path]:
    return sorted(path for path in pack.rglob("*") if path.is_file())


def written(item: StarterItem) -> Document:
    """The item's front matter and body; an item not written yet is skipped here and fails its existence test."""
    if not item.file.is_file():
        pytest.skip(f"{item.label} is not written yet")
    return parse_document(item.file.read_text(encoding="utf-8"))


def sources_of(item: StarterItem, document: Document) -> list[object]:
    """`sources` (a list) or a skill's `metadata.sources` (one comma-separated text), as YAML read them: an entry
    with an unquoted ": " comes back as a mapping, not as text."""
    front = document.front_matter
    if item.kind == "skill":
        metadata = front.get("metadata")
        value = metadata.get("sources") if isinstance(metadata, dict) else None
        return [part.strip() for part in value.split(",") if part.strip()] if isinstance(value, str) else []
    value = front.get("sources")
    return list(value) if isinstance(value, list) else []


def test_the_starter_has_packs() -> None:
    assert PACKS, f"No {MANIFEST} under {STARTER}"


@pytest.mark.parametrize("pack", packs())
def test_manifest_parses_and_is_named_after_its_folder(pack: Path) -> None:
    manifest = parse_manifest((pack / MANIFEST).read_bytes())

    assert manifest.name == f"yaap-{pack.name}"
    assert manifest.description
    assert len(manifest.items) <= MAX_ITEMS


@pytest.mark.parametrize("item", items_of("standard", "engineering", "skill", "command", "agent"))
def test_every_listed_item_exists(item: StarterItem) -> None:
    assert item.file.is_file(), f"{item.label} is listed but {item.file.relative_to(item.pack)} does not exist"


@pytest.mark.parametrize("pack", packs())
def test_every_item_file_in_a_pack_is_listed(pack: Path) -> None:
    items = listed_items(pack)
    single_files = {pack / item.path for item in items if item.kind != "skill"}
    skill_folders = [pack / item.path for item in items if item.kind == "skill"]
    unlisted = [
        path.relative_to(pack).as_posix()
        for path in pack_files(pack)
        if path.relative_to(pack).as_posix() not in PACK_FILES
        and path not in single_files
        and not any(folder in path.parents for folder in skill_folders)
    ]

    assert not unlisted, f"Files in {pack.name} that no item of its manifest holds: {unlisted}"


@pytest.mark.parametrize("pack", packs())
def test_no_file_in_a_pack_is_a_script_or_binary(pack: Path) -> None:
    code = [path.name for path in pack_files(pack) if path.suffix.lower() in SCRIPT_SUFFIXES | BINARY_SUFFIXES]

    assert not code, f"A package is text only; {pack.name} holds {code}"


@pytest.mark.parametrize("pack", packs())
def test_no_text_in_a_pack_tells_the_agent_to_install_or_pipe_downloads(pack: Path) -> None:
    found = {
        path.relative_to(pack).as_posix(): match.group(0)
        for path in pack_files(pack)
        if (match := RUN_TEXT.search(path.read_text(encoding="utf-8")))
    }

    assert not found, f"Install or run phrases that the package review flags: {found}"


@pytest.mark.parametrize("item", items_of("standard", "engineering", "skill", "command", "agent"))
def test_item_parses_as_a_blueprint_document(item: StarterItem) -> None:
    document = written(item)

    assert document.front_matter, f"{item.label} has no front matter"
    assert document.body.strip(), f"{item.label} has no body"


@pytest.mark.parametrize("item", items_of("standard", "engineering"))
def test_standard_has_a_summary_and_one_to_five_rules(item: StarterItem) -> None:
    front = written(item).front_matter
    rules = front.get("rules")

    # A ": " inside an unquoted rule makes YAML read it as a mapping instead of text.
    not_text = [rule for rule in rules or () if not isinstance(rule, str) or not rule.strip()]

    assert isinstance(front.get("summary"), str) and front["summary"].strip(), f"{item.label}: summary is not text"
    assert isinstance(rules, list) and 1 <= len(rules) <= MAX_RULES, f"{item.label} has {rules!r} as rules"
    assert not not_text, f"{item.label}: rules that YAML does not read as text: {not_text}"


@pytest.mark.parametrize("item", items_of("skill", "command", "agent"))
def test_item_description_is_text(item: StarterItem) -> None:
    description = written(item).front_matter.get("description")

    assert isinstance(description, str) and description.strip(), f"{item.label}: description is {description!r}"


@pytest.mark.parametrize("item", items_of("skill"))
def test_skill_front_matter_is_complete_and_its_body_is_short(item: StarterItem) -> None:
    document = written(item)
    front = document.front_matter
    description = front.get("description")
    metadata = front.get("metadata")

    assert front.get("name") == Path(item.path).name
    assert isinstance(description, str) and 1 <= len(description.strip()) <= MAX_DESCRIPTION
    assert front.get("license") in LICENSES
    assert isinstance(metadata, dict) and metadata.get("sources")
    assert len(document.body.splitlines()) < MAX_SKILL_LINES


@pytest.mark.parametrize("item", items_of("skill"))
def test_skill_folder_holds_only_its_skill_file(item: StarterItem) -> None:
    written(item)
    # `load_skill` returns only SKILL.md, so other files would be out of the agent's reach.
    extra = [path.relative_to(item.pack).as_posix() for path in pack_files(item.file.parent) if path != item.file]

    assert not extra, f"{item.label} holds files besides SKILL.md: {extra}"


@pytest.mark.parametrize("item", items_of("agent"))
def test_agent_names_only_known_tools_presets_and_roles(item: StarterItem) -> None:
    front = written(item).front_matter
    tools = text_list(front.get("tools")) or ()
    unknown = sorted(set(tools) - set(TOOLS_BY_NAME))

    assert not unknown, f"{item.label} names tools YAAP does not have: {unknown}"
    assert front.get("preset") is None or front["preset"] in PRESET_NAMES
    assert front.get("role") is None or front["role"] in ("main", "worker")


@pytest.mark.parametrize("item", items_of("standard", "engineering", "skill", "command", "agent"))
def test_item_names_only_known_sources(item: StarterItem) -> None:
    sources = sources_of(item, written(item))
    unknown = [
        source
        for source in sources
        if not isinstance(source, str) or (source != "own" and not source.startswith(SOURCE_PREFIXES))
    ]

    assert sources, f"{item.label} names no sources"
    assert not unknown, f"{item.label} names sources outside the credited repositories: {unknown}"


@pytest.mark.parametrize("item", items_of("skill"))
def test_skill_adapted_from_anthropic_skills_keeps_the_apache_license(item: StarterItem) -> None:
    document = written(item)
    adapted = any(str(source).startswith("anthropic-skills/") for source in sources_of(item, document))

    if adapted:
        assert document.front_matter.get("license") == "Apache-2.0"
