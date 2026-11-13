"""Request and response shapes of the Blueprint API."""

from typing import Literal

from pydantic import BaseModel, Field

from app.blueprint import packages


class MutationStatusOut(BaseModel):
    path: str
    committed: bool
    error: str | None = None


class MutationStatusesOut(BaseModel):
    statuses: list[MutationStatusOut]


class SkillOriginOut(BaseModel):
    """Where an installed skill comes from."""

    url: str
    repository: str
    ref: str
    commit: str
    path: str
    # The skill's folder on GitHub at the installed commit.
    commit_url: str
    installed_at: str
    # The files differ from what was installed.
    changed_locally: bool


class PackageOriginOut(BaseModel):
    """The package an item was installed from."""

    name: str
    url: str
    repository: str
    ref: str
    commit: str
    # The item's file or folder on GitHub at the installed commit.
    commit_url: str
    installed_at: str
    # The files differ from what was installed.
    changed_locally: bool


class BlueprintItemOut(BaseModel):
    path: str
    kind: Literal["instructions", "standard", "engineering", "skill", "command", "agent"]
    name: str
    summary: str
    rules: list[str]
    disabled: bool
    # Where the effective file lives: global, environment or project, with that level's name.
    source: Literal["global", "environment", "project"]
    source_label: str
    # The level being viewed has its own file for this path.
    defined_here: bool
    # A level above also defines this path, so removing this level's file brings that one back.
    inherited_from: str | None
    origin: SkillOriginOut | None = None
    package: PackageOriginOut | None = None
    # Levels that add to this skill instead of replacing it, top first.
    extended_by: list[str] = Field(default_factory=list)
    # The level being viewed adds to the inherited skill with its own file.
    extended_here: bool = False
    # This level's file extends a skill that no level above has; it is not used.
    orphan_extension: bool = False
    mutation: MutationStatusOut | None = None


class BlueprintFileOut(BlueprintItemOut):
    # This level's own file when it has one (an override or an extension), else the effective file.
    content: str
    # For an extension: the skill as the levels above give it, shown read-only above the additions.
    inherited_content: str | None = None


class BlueprintFileWrite(BaseModel):
    content: str = Field(max_length=512 * 1024)


class GitHubSourceOut(BaseModel):
    repository: str
    ref: str
    commit: str
    path: str
    url: str


class SkillFileOut(BaseModel):
    path: str
    size: int


class SkillCandidateOut(BaseModel):
    # The skill's folder in the repository; what install refers to.
    path: str
    name: str
    description: str
    text: str
    # The text with each hidden character shown as a mark such as ⟦U+200B ZERO WIDTH SPACE⟧.
    text_marked: str
    files: list[SkillFileOut]
    size: int
    warnings: list[str]
    # Why it cannot be installed (too large, too many files, hidden characters), if so.
    error: str | None
    # The level being viewed already has a skill with this name; installing replaces it.
    exists: bool


class GitHubPreviewIn(BaseModel):
    url: str = Field(min_length=1, max_length=500)


class GitHubPreviewOut(BaseModel):
    source: GitHubSourceOut
    candidates: list[SkillCandidateOut]


class SkillChoiceIn(BaseModel):
    path: str = Field(max_length=500)
    name: str = Field(min_length=1, max_length=60)


class GitHubInstallIn(BaseModel):
    url: str = Field(min_length=1, max_length=500)
    ref: str = Field(min_length=1, max_length=200)
    # The commit that was reviewed; the install reads exactly this commit.
    commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    skills: list[SkillChoiceIn] = Field(min_length=1, max_length=30)


class InstalledSkillOut(BaseModel):
    name: str
    path: str


class InstalledSkillsOut(BaseModel):
    skills: list[InstalledSkillOut]
    commits: list[MutationStatusOut]


class SkillUpdateOut(BaseModel):
    up_to_date: bool
    installed_commit: str
    latest_commit: str
    ref: str
    # Upstream changes between the installed and the newest commit.
    upstream_diff: str
    # Changes made in YAAP since the install.
    local_diff: str
    changed_locally: bool
    candidate: SkillCandidateOut | None
    source: GitHubSourceOut
    url: str


PackageKind = Literal["standard", "engineering", "skill", "command", "agent"]


class PackageFileOut(BaseModel):
    path: str
    size: int


class PackageItemOut(BaseModel):
    kind: PackageKind
    # The item's file or folder in the package; what install refers to.
    path: str
    name: str
    description: str
    text: str
    # The text with each hidden character shown as a mark such as ⟦U+200B ZERO WIDTH SPACE⟧.
    text_marked: str
    files: list[PackageFileOut]
    size: int
    warnings: list[str]
    # Why it cannot be installed (scripts, binary files, hidden characters, too large, missing), if so.
    error: str | None
    # The Blueprint path it gets, and whether the chosen level already has a file there.
    target: str
    exists: bool


class PackageInfoOut(BaseModel):
    name: str
    description: str


class PackagePreviewOut(BaseModel):
    source: GitHubSourceOut
    package: PackageInfoOut
    items: list[PackageItemOut]


class PackageChoiceIn(BaseModel):
    kind: PackageKind
    path: str = Field(min_length=1, max_length=500)
    name: str = Field(min_length=1, max_length=60)


class PackageInstallIn(BaseModel):
    url: str = Field(min_length=1, max_length=500)
    ref: str = Field(min_length=1, max_length=200)
    # The commit that was reviewed; the install reads exactly this commit.
    commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    items: list[PackageChoiceIn] = Field(min_length=1, max_length=packages.MAX_ITEMS)


class PackageInstallOut(BaseModel):
    items: list[str]
    commit: MutationStatusOut


class PackageInstalledItemOut(BaseModel):
    path: str
    kind: PackageKind
    name: str
    commit: str
    changed_locally: bool


class InstalledPackageOut(BaseModel):
    name: str
    description: str
    url: str
    repository: str
    ref: str
    items: list[PackageInstalledItemOut]


class PackageItemUpdateOut(BaseModel):
    # The item's Blueprint path at this level; what accepting refers to.
    path: str
    kind: PackageKind
    name: str
    installed_commit: str
    # changed: an update to review and accept; removed: the newest version no longer lists it.
    status: Literal["up_to_date", "changed", "removed"]
    upstream_diff: str
    # Changes made in YAAP since the install.
    local_diff: str
    changed_locally: bool
    # The newest version for review, when it changed.
    item: PackageItemOut | None


class PackageUpdateOut(BaseModel):
    name: str
    ref: str
    latest_commit: str
    source: GitHubSourceOut
    items: list[PackageItemUpdateOut]
    # Items the newest version lists that this level has not installed; add them with "Add from a package".
    new_items: list[PackageItemOut]


class PackageAcceptIn(BaseModel):
    # The commit that was reviewed; accepting writes exactly this commit.
    commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    paths: list[str] = Field(min_length=1, max_length=packages.MAX_ITEMS)
    # The ref the reviewed commit was read from, when the check named one (the starter moving to a newer tag); it is
    # recorded instead of the old one. Without it the recorded ref stays.
    ref: str | None = Field(default=None, min_length=1, max_length=200)


class RuleIn(BaseModel):
    rule: str = Field(min_length=1, max_length=500)
    # A standard, engineering standard or instructions.md this level owns; otherwise the level's rules standard.
    path: str | None = None
    # False only previews the change.
    apply: bool = False


class RuleOut(BaseModel):
    path: str
    diff: str
    applied: bool
    mutation: MutationStatusOut | None = None
