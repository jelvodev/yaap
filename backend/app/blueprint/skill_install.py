"""Skills installed from GitHub links.

A link is resolved to a fixed commit; only the linked folder is read, through the GitHub API (folder listing) and
raw.githubusercontent.com (file contents). Nothing that is downloaded is executed. Every `SKILL.md` in the folder is a
candidate; installing writes the candidate's folder unchanged to `skills/<name>/` with a `source.json` next to it, in
one commit.
"""

import hashlib
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from difflib import unified_diff
from pathlib import Path, PurePosixPath
from typing import Any

from app.blueprint.layers import (
    BlueprintError,
    Layer,
    ensure_repository,
    forget_merged,
    layer_has_directory,
    layer_has_file,
    layer_tree_files,
    parse_document,
    read_layer_bytes,
)
from app.blueprint.mutations import MutationResult, replace_folder
from app.core.hidden_text import find_hidden, mark_hidden, refusal_reason, warning_lines

API = "https://api.github.com"
RAW = "https://raw.githubusercontent.com"
MAX_SKILL_BYTES = 5 * 1024 * 1024
MAX_SKILL_FILES = 200
MAX_CANDIDATES = 30
SOURCE_FILE = "source.json"
SKILL_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,59}$")
LINK = re.compile(
    r"^https://github\.com/(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+?)(?:\.git)?"
    r"(?:/(?P<kind>tree|blob)/(?P<rest>.+?))?/?$"
)
SCRIPT_SUFFIXES = {".sh", ".bash", ".zsh", ".ps1", ".bat", ".cmd", ".py", ".js", ".mjs", ".cjs", ".ts", ".rb", ".pl"}
TEXT_SUFFIXES = SCRIPT_SUFFIXES | {
    ".md", ".markdown", ".txt", ".json", ".yaml", ".yml", ".toml", ".csv", ".html", ".xml", ".ini", ".cfg",
}
BINARY_SUFFIXES = {".exe", ".dll", ".so", ".dylib", ".bin", ".jar", ".class", ".pyc", ".zip", ".tar", ".gz", ".whl"}
RUN_TEXT = re.compile(
    r"(curl|wget|Invoke-WebRequest|iwr|irm)\b[^\n]*(\||;|&&)\s*(sh|bash|python|iex|powershell)\b"
    r"|\b(pip|npm|pnpm|yarn|uv) (install|add|i)\b|\bnpx \S|\bchmod \+x\b|\bsudo\b",
    re.IGNORECASE,
)


class SkillInstallError(BlueprintError):
    """A GitHub skill action that cannot be done; the message is safe to show to the user."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class SkillInstallResult:
    names: list[str]
    commits: list[MutationResult]


# Added to "not found" when no token was sent: GitHub answers 404 for a private repository it may not show.
PRIVATE_HINT = " A private repository needs this environment's GitHub token."

# (url, token) -> response body. Replaced in tests.
HttpGet = Callable[[str, str | None], bytes]


def http_get(url: str, token: str | None) -> bytes:
    headers = {"User-Agent": "YAAP", "Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.read(MAX_SKILL_BYTES + 1)
    except urllib.error.HTTPError as error:
        if error.code == 404:
            hint = "" if token else PRIVATE_HINT
            raise SkillInstallError(f"GitHub has nothing at this link.{hint}", 404) from error
        if error.code in (401, 403, 429):
            raise SkillInstallError(
                "GitHub refused the request (no access, or the rate limit was reached). "
                "A GitHub token in this environment raises the limit."
            ) from error
        raise SkillInstallError(f"GitHub answered with HTTP {error.code}.", error.code) from error
    except (urllib.error.URLError, TimeoutError) as error:
        raise SkillInstallError(f"GitHub could not be reached: {error}") from error


@dataclass(frozen=True)
class GitHubLink:
    owner: str
    repo: str
    # The ref and path as written in the link; the ref is not yet split from the path for tree/blob links.
    rest: str | None = None
    is_file: bool = False

    @property
    def repository(self) -> str:
        return f"{self.owner}/{self.repo}"


def parse_link(url: str) -> GitHubLink:
    match = LINK.match(url.strip().split("#", 1)[0].split("?", 1)[0])
    if not match:
        raise SkillInstallError(
            "Use a GitHub link: https://github.com/<owner>/<repo>, a folder (…/tree/<ref>/<path>) "
            "or a SKILL.md file (…/blob/<ref>/<path>/SKILL.md)."
        )
    rest = urllib.parse.unquote(match.group("rest")) if match.group("rest") else None
    is_file = match.group("kind") == "blob"
    if is_file and not (rest or "").endswith("SKILL.md"):
        raise SkillInstallError("A file link must point to a SKILL.md.")
    return GitHubLink(match.group("owner"), match.group("repo"), rest, is_file)


@dataclass
class Source:
    repository: str
    ref: str
    commit: str
    # The linked folder in the repository ('' for the root).
    path: str

    def tree_url(self, path: str) -> str:
        suffix = f"/{path}" if path else ""
        return f"https://github.com/{self.repository}/tree/{self.commit}{suffix}"


@dataclass
class RemoteFile:
    path: str  # relative to the skill folder
    size: int
    mode: str


@dataclass
class Candidate:
    # Folder of the skill in the repository.
    path: str
    folder_name: str
    files: list[RemoteFile]
    skipped: list[str]
    name: str = ""
    description: str = ""
    text: str = ""
    # The text with hidden characters made visible, for review.
    text_marked: str = ""
    warnings: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def size(self) -> int:
        return sum(file.size for file in self.files)


class GitHubSkills:
    def __init__(self, token: str | None, get: HttpGet | None = None) -> None:
        self.token = token
        self.get = get or http_get

    def json(self, path: str) -> Any:
        try:
            return json.loads(self.get(f"{API}{path}", self.token))
        except ValueError as error:
            raise SkillInstallError("GitHub sent an answer YAAP cannot read.") from error

    @property
    def private_hint(self) -> str:
        return "" if self.token else PRIVATE_HINT

    def commit_of(self, repository: str, ref: str) -> tuple[str, str] | None:
        """(commit sha, root tree sha) of a ref, or None when the ref does not exist."""
        try:
            data = self.json(f"/repos/{repository}/commits/{urllib.parse.quote(ref, safe='')}")
        except SkillInstallError as error:
            if error.status in (404, 422):
                return None
            raise
        return data["sha"], data["commit"]["tree"]["sha"]

    def resolve(self, link: GitHubLink, ref: str | None = None, path: str | None = None) -> tuple[Source, str]:
        """The source (fixed commit) and the root tree sha. `ref` and `path` override what the link says."""
        if ref is not None:
            found = self.commit_of(link.repository, ref)
            if found is None:
                raise SkillInstallError(f"{link.repository} has no branch, tag or commit '{ref}'.{self.private_hint}")
            return Source(link.repository, ref, found[0], path or ""), found[1]
        if link.rest is None:
            branch = self.json(f"/repos/{link.repository}")["default_branch"]
            found = self.commit_of(link.repository, branch)
            if found is None:
                raise SkillInstallError(f"The default branch of {link.repository} has no commits.")
            return Source(link.repository, branch, found[0], ""), found[1]
        # A branch name can contain slashes, so try the shortest ref first.
        parts = link.rest.split("/")
        for count in range(1, min(len(parts), 6) + 1):
            candidate_ref = "/".join(parts[:count])
            found = self.commit_of(link.repository, candidate_ref)
            if found is not None:
                rest = "/".join(parts[count:])
                folder = str(PurePosixPath(rest).parent) if link.is_file else rest
                return Source(link.repository, candidate_ref, found[0], "" if folder == "." else folder), found[1]
        raise SkillInstallError(
            f"The branch, tag or commit in this link was not found in {link.repository}.{self.private_hint}"
        )

    def folder_tree(self, source: Source, root_tree: str) -> list[dict[str, Any]]:
        """Every entry below the linked folder, with paths relative to that folder."""
        tree = root_tree
        for part in [part for part in source.path.split("/") if part]:
            entries = self.json(f"/repos/{source.repository}/git/trees/{tree}")["tree"]
            match = next((entry for entry in entries if entry["path"] == part and entry["type"] == "tree"), None)
            if match is None:
                raise SkillInstallError(f"{source.repository} has no folder '{source.path}' at {source.commit[:7]}.")
            tree = match["sha"]
        data = self.json(f"/repos/{source.repository}/git/trees/{tree}?recursive=1")
        if data.get("truncated"):
            raise SkillInstallError("This folder is too large to read; link to the skill's own folder instead.")
        return data["tree"]

    def raw(self, source: Source, path: str) -> bytes:
        full = "/".join(part for part in (source.path, path) if part)
        url = f"{RAW}/{source.repository}/{source.commit}/{urllib.parse.quote(full)}"
        return self.get(url, self.token)

    def candidates(self, source: Source, root_tree: str) -> list[Candidate]:
        entries = self.folder_tree(source, root_tree)
        skill_folders = sorted(
            str(PurePosixPath(entry["path"]).parent)
            for entry in entries
            if entry["type"] == "blob" and PurePosixPath(entry["path"]).name == "SKILL.md"
        )
        if not skill_folders:
            raise SkillInstallError("No SKILL.md was found at this link.")
        if len(skill_folders) > MAX_CANDIDATES:
            raise SkillInstallError(
                f"This link holds {len(skill_folders)} skills; link to a folder with at most {MAX_CANDIDATES}."
            )
        found: list[Candidate] = []
        for folder in skill_folders:
            prefix = "" if folder == "." else f"{folder}/"
            files: list[RemoteFile] = []
            skipped: list[str] = []
            for entry in entries:
                if not entry["path"].startswith(prefix) or entry["type"] == "tree":
                    continue
                relative = entry["path"][len(prefix) :]
                if entry["type"] != "blob" or entry["mode"] == "120000":
                    skipped.append(relative)  # Symlinks and submodules are never followed.
                elif relative == SOURCE_FILE:
                    skipped.append(relative)  # YAAP writes its own source.json.
                else:
                    files.append(RemoteFile(relative, int(entry.get("size") or 0), entry["mode"]))
            folder_path = "/".join(part for part in (source.path, "" if folder == "." else folder) if part)
            folder_name = PurePosixPath(folder_path).name if folder_path else source.repository.split("/")[1]
            found.append(Candidate(folder_path, folder_name, files, skipped))
        return found

    def describe(self, source: Source, candidate: Candidate) -> Candidate:
        """Fill in name, description, text and warnings from the candidate's SKILL.md."""
        if len(candidate.files) > MAX_SKILL_FILES:
            candidate.error = f"The skill has {len(candidate.files)} files; at most {MAX_SKILL_FILES} can be installed."
        elif candidate.size > MAX_SKILL_BYTES:
            candidate.error = "The skill is larger than 5 MB."
        sub = Source(source.repository, source.ref, source.commit, candidate.path)
        text = self.raw(sub, "SKILL.md").decode("utf-8", errors="replace")
        candidate.text = text.replace("\r\n", "\n")
        try:
            front = parse_document(candidate.text).front_matter
        except BlueprintError:
            front = {}
            candidate.warnings.append("The front matter of SKILL.md is not valid YAML.")
        candidate.name = skill_name(str(front.get("name") or candidate.folder_name))
        candidate.description = str(front.get("description") or "").strip()
        candidate.warnings += content_warnings(candidate.files, candidate.skipped, candidate.text)
        findings = find_hidden(candidate.text)
        candidate.warnings += warning_lines(findings)
        candidate.text_marked = mark_hidden(candidate.text)
        candidate.error = candidate.error or refusal_reason(findings)
        return candidate

    def download(self, source: Source, candidate: Candidate) -> dict[str, bytes]:
        if candidate.error:
            raise SkillInstallError(candidate.error)
        sub = Source(source.repository, source.ref, source.commit, candidate.path)
        files: dict[str, bytes] = {}
        total = 0
        for file in candidate.files:
            content = self.raw(sub, file.path)
            total += len(content)
            if total > MAX_SKILL_BYTES:
                raise SkillInstallError("The skill is larger than 5 MB.")
            if (reason := hidden_refusal(file.path, content)) is not None:
                raise SkillInstallError(f"{file.path}: {reason}")
            files[safe_relative(file.path)] = content
        return files


def skill_name(text: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60].strip("-")
    return value or "skill"


def safe_relative(path: str) -> str:
    parts = PurePosixPath(path).parts
    if not parts or path.startswith("/") or "\\" in path or any(part in ("..", ".", ".git") for part in parts):
        raise SkillInstallError(f"The skill contains a path YAAP does not write: {path}")
    return path


def hidden_refusal(path: str, content: bytes) -> str | None:
    """Why a file is refused for the characters it hides.

    Text is scanned whatever bytes it holds; a NUL byte in a text file would only skip the scan, so it is refused.
    Other files that contain NUL bytes are binary and are not looked at.
    """
    if b"\0" in content:
        if PurePosixPath(path).suffix.lower() in TEXT_SUFFIXES:
            return "A text file contains a NUL byte, which hides the rest of the text from review."
        return None
    return refusal_reason(find_hidden(content.decode("utf-8", errors="replace")))


def content_warnings(files: list[RemoteFile], skipped: list[str], text: str) -> list[str]:
    warnings: list[str] = []
    scripts = [file.path for file in files if PurePosixPath(file.path).suffix.lower() in SCRIPT_SUFFIXES]
    binaries = [file.path for file in files if PurePosixPath(file.path).suffix.lower() in BINARY_SUFFIXES]
    if scripts:
        warnings.append(f"Contains scripts ({', '.join(scripts[:5])}). YAAP never runs them; the agent can only "
                        "run them through run_command, with approval.")
    if binaries:
        warnings.append(f"Contains binary files ({', '.join(binaries[:5])}).")
    if skipped:
        warnings.append(f"Skipped: {', '.join(skipped[:5])} (symlinks, submodules or source.json).")
    match = RUN_TEXT.search(text)
    if match:
        warnings.append(f"The text tells the agent to download or install things: \"{match.group(0)[:80]}\".")
    return warnings


# --- Writing into a layer ------------------------------------------------------------------------------------------


def skill_folder(layer: Layer, name: str) -> Path:
    if not SKILL_NAME.match(name):
        raise SkillInstallError("A skill name uses lowercase letters, digits and hyphens (at most 60).")
    return layer.root / "skills" / name


def files_hash(files: dict[str, bytes]) -> str:
    digest = hashlib.sha256()
    for path in sorted(files):
        digest.update(f"{path}\0{hashlib.sha256(files[path]).hexdigest()}\n".encode())
    return digest.hexdigest()


def local_files(folder: Path, layer: Layer | None = None) -> dict[str, bytes]:
    if layer is not None and layer.kind == "project":
        return layer_tree_files(layer, folder.relative_to(layer.root).as_posix(), frozenset({SOURCE_FILE}))
    return {
        file.relative_to(folder).as_posix(): file.read_bytes()
        for file in sorted(folder.rglob("*"))
        if file.is_file() and not file.is_symlink() and file.relative_to(folder).as_posix() != SOURCE_FILE
    }


def install(
    layer: Layer, name: str, source: Source, candidate_path: str, files: dict[str, bytes], link: str, action: str
) -> MutationResult:
    """Replace `skills/<name>/` with these files and its source.json, as one commit."""
    skill_folder(layer, name)
    if "SKILL.md" not in files:
        raise SkillInstallError("The skill has no SKILL.md.")
    record = {
        "url": link,
        "repository": source.repository,
        "ref": source.ref,
        "commit": source.commit,
        "path": candidate_path,
        "installed_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "hash": files_hash(files),
    }
    ensure_repository(layer)
    try:
        result = replace_folder(
            layer,
            f"skills/{name}",
            {
                **{safe_relative(path): content for path, content in files.items()},
                SOURCE_FILE: (json.dumps(record, indent=2) + "\n").encode(),
            },
            f"{action} skill {name} from {source.repository}@{source.commit[:7]} ({layer.label})",
        )
        forget_merged()
        return result
    except OSError as error:
        raise SkillInstallError(f"Could not replace skill '{name}': {error}") from error


@dataclass
class Origin:
    record: dict[str, Any]
    changed_locally: bool

    @property
    def source(self) -> Source:
        return Source(self.record["repository"], self.record["ref"], self.record["commit"], self.record["path"])


def read_origin(layer: Layer, name: str) -> Origin | None:
    folder = layer.root / "skills" / name
    relative = f"skills/{name}/{SOURCE_FILE}"
    if not layer_has_file(layer, relative):
        return None
    try:
        record = json.loads(read_layer_bytes(layer, relative).decode("utf-8"))
        changed = files_hash(local_files(folder, layer)) != record.get("hash")
        if not all(isinstance(record.get(key), str) for key in ("repository", "ref", "commit", "path")):
            return None
    except (OSError, ValueError, AttributeError):
        return None
    return Origin(record, changed)


def text_diff(before: dict[str, bytes], after: dict[str, bytes], before_label: str, after_label: str) -> str:
    """A unified diff over text files; binary files are only named."""
    chunks: list[str] = []
    for path in sorted(set(before) | set(after)):
        old, new = before.get(path), after.get(path)
        if old == new:
            continue
        if b"\0" in (old or b"") or b"\0" in (new or b""):
            chunks.append(f"Binary file {path} changed\n")
            continue
        old_lines = (old or b"").decode("utf-8", errors="replace").splitlines(keepends=True)
        new_lines = (new or b"").decode("utf-8", errors="replace").splitlines(keepends=True)
        chunks.append(
            "".join(
                unified_diff(
                    old_lines,
                    new_lines,
                    f"{before_label}/{path}" if old is not None else "/dev/null",
                    f"{after_label}/{path}" if new is not None else "/dev/null",
                )
            )
        )
    return "\n".join(chunk if chunk.endswith("\n") else chunk + "\n" for chunk in chunks)


# --- Flows shared by the API and the install_skill tool --------------------------------------------------------------


def preview(github: GitHubSkills, url: str) -> tuple[Source, list[Candidate]]:
    """The fixed commit a link resolves to and the skills found there, described for review."""
    source, tree = github.resolve(parse_link(url))
    return source, [github.describe(source, candidate) for candidate in github.candidates(source, tree)]


def install_at_commit(
    github: GitHubSkills, layer: Layer, url: str, ref: str, commit: str, choices: list[tuple[str, str]]
) -> SkillInstallResult:
    """Install skills (folder path in the repository, name) exactly as they are at the reviewed commit."""
    link = parse_link(url)
    pinned, tree = github.resolve(link, commit)
    written: list[str] = []
    commits: list[MutationResult] = []
    for path, name in choices:
        skill_folder(layer, name)  # Validates the name before anything is downloaded.
        source = Source(pinned.repository, ref, pinned.commit, path)
        candidate = next((item for item in github.candidates(source, tree) if item.path == path), None)
        if candidate is None:
            raise SkillInstallError(f"There is no skill at '{path}' in {pinned.repository}@{pinned.commit[:7]}.")
        files = github.download(source, github.describe(source, candidate))
        action = "Update" if layer_has_directory(layer, f"skills/{name}") else "Install"
        commits.append(install(layer, name, source, path, files, url, action))
        written.append(name)
    return SkillInstallResult(written, commits)


@dataclass
class UpdateCheck:
    latest: Source
    candidate: Candidate | None
    upstream_diff: str
    local_diff: str


def skill_files_at(github: GitHubSkills, source: Source, tree: str) -> tuple[Candidate | None, dict[str, bytes]]:
    candidate = next((item for item in github.candidates(source, tree) if item.path == source.path), None)
    if candidate is None:
        return None, {}
    candidate = github.describe(source, candidate)
    return candidate, github.download(source, candidate)


def check_update(github: GitHubSkills, layer: Layer, name: str, origin: Origin) -> UpdateCheck:
    installed = origin.source
    owner, repo = installed.repository.split("/", 1)
    repository = GitHubLink(owner, repo)
    latest, latest_tree = github.resolve(repository, installed.ref, installed.path)
    latest.ref = installed.ref
    if latest.commit == installed.commit and not origin.changed_locally:
        return UpdateCheck(latest, None, "", "")
    _, old_tree = github.resolve(repository, installed.commit, installed.path)
    _, old_files = skill_files_at(github, installed, old_tree)
    candidate = None
    upstream_diff = ""
    if latest.commit != installed.commit:
        candidate, new_files = skill_files_at(github, latest, latest_tree)
        if candidate is None:
            raise SkillInstallError(f"The skill's folder '{installed.path}' no longer exists on {installed.ref}.")
        upstream_diff = text_diff(old_files, new_files, installed.commit[:7], latest.commit[:7])
    local_diff = ""
    if origin.changed_locally:
        local_diff = text_diff(
            old_files, local_files(layer.root / "skills" / name, layer), installed.commit[:7], "local"
        )
    return UpdateCheck(latest, candidate, upstream_diff, local_diff)
