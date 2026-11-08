"""The language servers YAAP knows, and which languages a project has.

pyright and typescript-language-server are installed in the sandbox image (docker/sandbox/Dockerfile), so every runner
and sandbox has them. The others start when their program is installed through the image's extra layer
(docs/configuration.md, "Extra packages for the runner"); YAAP checks which of them a runner has the first time it
starts a server there.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath


@dataclass(frozen=True)
class ServerSpec:
    # The language the server is kept for: one server per project and language.
    language: str
    # Shown in results ("pyright found ...").
    name: str
    command: tuple[str, ...]
    # File suffix -> the languageId the server expects for it.
    language_ids: dict[str, str]
    # Installed in the sandbox image; the others need the extra layer.
    in_image: bool = False
    initialization_options: dict[str, object] = field(default_factory=dict)

    @property
    def program(self) -> str:
        return self.command[0]


SERVERS: tuple[ServerSpec, ...] = (
    ServerSpec("python", "pyright", ("pyright-langserver", "--stdio"), {".py": "python", ".pyi": "python"}, True),
    ServerSpec(
        "typescript",
        "typescript-language-server",
        ("typescript-language-server", "--stdio"),
        {
            ".ts": "typescript",
            ".mts": "typescript",
            ".cts": "typescript",
            ".tsx": "typescriptreact",
            ".js": "javascript",
            ".mjs": "javascript",
            ".cjs": "javascript",
            ".jsx": "javascriptreact",
        },
        True,
    ),
    ServerSpec("go", "gopls", ("gopls",), {".go": "go"}),
    ServerSpec("rust", "rust-analyzer", ("rust-analyzer",), {".rs": "rust"}),
    ServerSpec(
        "c",
        "clangd",
        ("clangd",),
        {".c": "c", ".h": "c", ".cc": "cpp", ".cpp": "cpp", ".cxx": "cpp", ".hpp": "cpp", ".hh": "cpp"},
    ),
)
# How far detecting a project's languages looks: enough to see the languages of a repository, never the whole tree.
DETECT_MAX_ENTRIES = 3000
DETECT_MAX_DEPTH = 4


def suffix(path: str) -> str:
    return PurePosixPath(path).suffix.lower()


def spec_for(path: str, servers: tuple[ServerSpec, ...]) -> ServerSpec | None:
    found = suffix(path)
    return next((spec for spec in servers if found in spec.language_ids), None)


def project_suffixes(folder: Path, skip: frozenset[str]) -> set[str]:
    """The file suffixes in the first levels of a project folder, within DETECT_MAX_ENTRIES entries."""
    found: set[str] = set()
    seen = 0
    pending = [(folder, 0)]
    while pending and seen < DETECT_MAX_ENTRIES:
        current, depth = pending.pop(0)
        try:
            entries = list(os.scandir(current))
        except OSError:
            continue
        for entry in entries:
            seen += 1
            if entry.is_dir(follow_symlinks=False):
                if depth + 1 < DETECT_MAX_DEPTH and entry.name not in skip and not entry.name.startswith("."):
                    pending.append((Path(entry.path), depth + 1))
            elif "." in entry.name:
                found.add(Path(entry.name).suffix.lower())
    return found


def project_servers(folder: Path, skip: frozenset[str], servers: tuple[ServerSpec, ...]) -> tuple[ServerSpec, ...]:
    """The servers for the languages the project has files of."""
    suffixes = project_suffixes(folder, skip)
    return tuple(spec for spec in servers if suffixes & set(spec.language_ids))
