"""YAAP's starter Blueprint: packages in YAAP's own repository, read at the tag of the running version.

Installing and updating go through the ordinary package flow (`packages`, `package_updates`) with the links made
here. A development build, whose version has no tag yet, reads `main` instead and says so.
"""

from dataclasses import dataclass

from app.blueprint.layers import Layer
from app.blueprint.packages import read_record
from app.blueprint.skill_install import GitHubSkills

STARTER_REPOSITORY = "jelvodev/yaap"
STARTER_FOLDER = "blueprint/starter"
# The branch a build without its own tag reads.
DEVELOPMENT_REF = "main"


@dataclass(frozen=True)
class StarterPack:
    key: str
    # The package name in its manifest, which is also the name of its record in a layer.
    name: str
    folder: str
    title: str
    description: str
    # Chosen when the starter is first offered.
    chosen: bool = False


STARTER_PACKS: tuple[StarterPack, ...] = (
    StarterPack("core", "yaap-core", "core", "Core", "How to work, test, debug, review and finish a change", True),
    StarterPack("web", "yaap-web", "web", "Web", "Front ends in TypeScript and React"),
    StarterPack("python", "yaap-python", "python", "Python", "Python back ends and APIs"),
    StarterPack("data", "yaap-data", "data", "Data", "SQL, pipelines, dbt and Airflow"),
    StarterPack("devops", "yaap-devops", "devops", "DevOps", "Containers, CI, infrastructure and operations"),
    StarterPack("writing", "yaap-writing", "writing", "Writing", "Documents, status updates, product work and research"),
)
CORE_PACK = STARTER_PACKS[0]


@dataclass(frozen=True)
class StarterRef:
    ref: str
    # False when the running version has no tag, so the starter comes from the development branch.
    pinned_to_version: bool


@dataclass(frozen=True)
class PackState:
    pack: StarterPack
    # The pack's folder on GitHub at the current ref: the link to preview and install it with.
    url: str
    installed: bool
    # The ref the layer installed the pack from, when installed.
    installed_ref: str | None
    # Installed from another ref than the current one, so moving to the current ref is an update.
    outdated: bool


def version_tag(version: str) -> str:
    return f"v{version}"


def starter_ref(github: GitHubSkills, version: str) -> StarterRef:
    """The tag of the running version when GitHub has it, else the development branch."""
    tag = version_tag(version)
    if github.commit_of(STARTER_REPOSITORY, tag) is not None:
        return StarterRef(tag, True)
    return StarterRef(DEVELOPMENT_REF, False)


def pack_url(pack: StarterPack, ref: str) -> str:
    return f"https://github.com/{STARTER_REPOSITORY}/tree/{ref}/{STARTER_FOLDER}/{pack.folder}"


def credits_url(ref: str) -> str:
    return f"https://github.com/{STARTER_REPOSITORY}/blob/{ref}/docs/credits.md"


def installed_ref(layer: Layer, pack: StarterPack) -> str | None:
    """The ref this layer installed the pack from, or None when it has none of its items.

    A record of the same name from another repository is not the starter's.
    """
    record = read_record(layer, pack.name)
    if record is None or not record["items"] or record["repository"].lower() != STARTER_REPOSITORY:
        return None
    return str(record["ref"])


def pack_state(layer: Layer, pack: StarterPack, ref: str) -> PackState:
    installed = installed_ref(layer, pack)
    return PackState(pack, pack_url(pack, ref), installed is not None, installed, installed not in (None, ref))


def pack_states(layer: Layer, ref: str) -> list[PackState]:
    return [pack_state(layer, pack, ref) for pack in STARTER_PACKS]
