"""Choosing what the repository map shows: which files are candidates, how central each looks, and its public symbols.

Pure functions over paths and outlines, so the ranking is tested without a project. Source folders, entry points
and files with many public symbols rank first; tests, documentation and examples last.
"""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import PurePosixPath

from app.outline.symbols import Outline, Symbol

# Files whose outline the map asks for. An outliner without support for one returns None; the file can still be
# listed by name as an entry point.
SOURCE_SUFFIXES = frozenset(
    {
        *(".py", ".pyi", ".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs"),
        *(".go", ".rs", ".java", ".kt", ".cs", ".rb", ".php", ".swift", ".c", ".h", ".cpp", ".hpp"),
    }
)
SOURCE_FOLDERS = frozenset(
    {"src", "lib", "app", "apps", "pkg", "cmd", "internal", "packages", "core"}
    | {"backend", "frontend", "server", "client"}
)
SIDE_FOLDERS = frozenset(
    {"docs", "doc", "examples", "example", "samples", "scripts", "tools", "fixtures", "benchmarks"}
)
TEST_FOLDERS = frozenset({"test", "tests", "__tests__", "spec", "specs", "testing", "e2e"})
ENTRY_STEMS = frozenset({"main", "__main__", "cli", "app", "server", "index", "manage", "wsgi", "asgi", "mod", "lib"})
SYMBOL_KINDS = frozenset({"class", "function", "interface", "type", "enum"})
MAX_SYMBOL_BONUS = 20
FOLDER_DECAY = 0.75


@dataclass(frozen=True)
class Module:
    path: str
    symbols: tuple[Symbol, ...]
    score: float


def is_source(path: str) -> bool:
    name = PurePosixPath(path).name
    return PurePosixPath(path).suffix.lower() in SOURCE_SUFFIXES and ".min." not in name and not name.endswith(".d.ts")


def is_test(path: str) -> bool:
    parts = PurePosixPath(path).parts
    name = parts[-1].lower()
    stem = name.split(".", 1)[0]
    return (
        any(part.lower() in TEST_FOLDERS for part in parts[:-1])
        or stem.startswith("test_")
        or stem.endswith(("_test", "_spec"))
        or ".test." in name
        or ".spec." in name
        or stem == "conftest"
    )


def is_entry_point(path: str) -> bool:
    return PurePosixPath(path).name.split(".", 1)[0].lower() in ENTRY_STEMS


def package_folders(paths: tuple[str, ...]) -> frozenset[str]:
    """Folders that are Python packages or Node packages: their files are the project's own source."""
    markers = ("__init__.py", "package.json", "Cargo.toml", "go.mod", "pyproject.toml")
    return frozenset(str(PurePosixPath(path).parent) for path in paths if PurePosixPath(path).name in markers)


def path_score(path: str, packages: frozenset[str]) -> float:
    """How central a file looks from its path alone; used to choose which files to outline."""
    folders = [part.lower() for part in PurePosixPath(path).parts[:-1]]
    score = 1.0
    if any(folder in SOURCE_FOLDERS for folder in folders) or str(PurePosixPath(path).parent) in packages:
        score += 2.0
    if is_entry_point(path):
        score += 2.5
    if is_test(path):
        score -= 3.0
    elif any(folder in SIDE_FOLDERS for folder in folders):
        score -= 1.5
    return score - 0.3 * len(folders)


def public_symbols(outline: Outline | None, with_methods: bool = True) -> tuple[Symbol, ...]:
    """The top-level classes, functions, interfaces, types and enums whose names are not private, each class with
    its public methods (not its getters and setters). A test file's methods are its test names, which say little
    in a map, so with_methods is off for tests."""
    if outline is None or outline.problem:
        return ()
    shown = []
    for symbol in outline.symbols:
        if symbol.kind not in SYMBOL_KINDS or not is_public(symbol.name):
            continue
        methods = tuple(
            child
            for child in symbol.children
            if with_methods and child.kind == "method" and is_public(child.name) and " " not in child.name
        )
        shown.append(Symbol(symbol.kind, symbol.name, symbol.line, symbol.end_line, children=methods))
    return tuple(shown)


def is_public(name: str) -> bool:
    return bool(name) and not name.startswith(("_", "#")) and name != "constructor"


def module_score(path: str, packages: frozenset[str], symbols: tuple[Symbol, ...]) -> float:
    weight = len(symbols) + sum(len(symbol.children) for symbol in symbols) / 4
    return path_score(path, packages) + min(weight, MAX_SYMBOL_BONUS) * 0.25


def spread(paths: list[str], score: Callable[[str], float]) -> list[str]:
    """The paths best first, taking turns between folders, so a large folder does not crowd out the others: the
    best file of every folder comes before the second best of any."""
    folders: dict[str, list[str]] = {}
    for path in sorted(paths, key=lambda item: (-score(item), item)):
        folders.setdefault(str(PurePosixPath(path).parent), []).append(path)
    turns = sorted(
        ((rank, -score(path), path) for files in folders.values() for rank, path in enumerate(files)),
    )
    return [path for _rank, _score, path in turns]


def ranked(modules: list[Module]) -> list[Module]:
    """The modules most central first; each further module of the same folder counts a little less, so the map
    covers more of the project than its largest folder."""
    seen: dict[str, int] = {}
    weighted = []
    for module in sorted(modules, key=lambda item: (-item.score, item.path)):
        folder = str(PurePosixPath(module.path).parent)
        weighted.append((module.score - FOLDER_DECAY * seen.get(folder, 0), module))
        seen[folder] = seen.get(folder, 0) + 1
    return [module for _score, module in sorted(weighted, key=lambda item: (-item[0], item[1].path))]
