"""The repository map as text for the system prompt, within a budget of characters.

The folders come first, then the modules most central first, each with its public symbols; what does not fit is
counted in a last line. The budget is in characters, from the token budget with the estimate of the token footprint
(characters / 4), so the map's size is measured the same way as the rest of the context.
"""

from collections import Counter
from pathlib import PurePosixPath

from app.outline.symbols import Symbol
from app.repomap.ranking import Module

MAX_FOLDERS = 12
MAX_SUBFOLDERS = 4
MAX_ROOT_FILES = 15
# Symbols shown per module, fewer when not every module fits.
SYMBOL_LIMITS = (10, 6, 4)
MAX_METHODS = 5
# The share of the budget the folder list may take, so modules always get room.
FOLDER_SHARE = 0.35
# Room kept for the last line that counts what was left out.
TAIL_CHARS = 90


def header(revision_label: str, budget_tokens: int) -> str:
    return (
        "# Repository map\n\n"
        f"The project's folders, main modules and public symbols at {revision_label}, most central modules first, "
        f"made by YAAP within about {budget_tokens:,} tokens when the conversation started. It is not complete and "
        "can lag behind your own changes: list_files, search and read_file (outline: true for a file's symbols) "
        "show the current state."
    )


def folder_lines(files: tuple[str, ...]) -> list[str]:
    top: Counter[str] = Counter()
    second: dict[str, Counter[str]] = {}
    root_files: list[str] = []
    for path in files:
        parts = PurePosixPath(path).parts
        if len(parts) == 1:
            root_files.append(path)
            continue
        top[parts[0]] += 1
        if len(parts) > 2:
            second.setdefault(parts[0], Counter())[parts[1]] += 1
    lines = []
    for folder, count in sorted(top.items(), key=lambda item: (-item[1], item[0]))[:MAX_FOLDERS]:
        line = f"- {folder}/ ({count} file{'' if count == 1 else 's'})"
        subfolders = sorted(second.get(folder, Counter()).items(), key=lambda item: (-item[1], item[0]))
        if subfolders:
            shown = ", ".join(f"{name}/ ({number})" for name, number in subfolders[:MAX_SUBFOLDERS])
            more = f", +{len(subfolders) - MAX_SUBFOLDERS} more" if len(subfolders) > MAX_SUBFOLDERS else ""
            line += f": {shown}{more}"
        lines.append(line)
    if len(top) > MAX_FOLDERS:
        lines.append(f"- +{len(top) - MAX_FOLDERS} more folders")
    if root_files:
        more = f", +{len(root_files) - MAX_ROOT_FILES} more" if len(root_files) > MAX_ROOT_FILES else ""
        lines.append(f"Files at the root: {', '.join(sorted(root_files)[:MAX_ROOT_FILES])}{more}")
    return lines


def symbol_text(symbol: Symbol, max_methods: int) -> str:
    if symbol.kind == "function":
        return f"{symbol.name}()"
    if symbol.kind != "class" or not symbol.children:
        return f"{symbol.kind} {symbol.name}"
    methods = [child.name for child in symbol.children[:max_methods]]
    if len(symbol.children) > max_methods:
        methods.append(f"+{len(symbol.children) - max_methods}")
    return f"class {symbol.name}({', '.join(methods)})"


def module_line(module: Module, max_symbols: int = SYMBOL_LIMITS[0]) -> str:
    if not module.symbols:
        return f"- {module.path}"
    max_methods = min(MAX_METHODS, max_symbols)
    shown = [symbol_text(symbol, max_methods) for symbol in module.symbols[:max_symbols]]
    if len(module.symbols) > max_symbols:
        shown.append(f"+{len(module.symbols) - max_symbols} more")
    return f"- {module.path}: {', '.join(shown)}"


def module_lines(modules: list[Module], room: int, max_symbols: int) -> tuple[list[str], int]:
    """The lines of the modules that fit in room characters, most central first, and how many were left out."""
    lines: list[str] = []
    used, left_out = 0, 0
    for module in modules:
        line = module_line(module, max_symbols)
        if used + len(line) + 1 > room:
            left_out += 1
            continue
        lines.append(line)
        used += len(line) + 1
    return lines, left_out


def render_map(
    revision_label: str, files: tuple[str, ...], modules: list[Module], budget_tokens: int, budget_chars: int
) -> str:
    """The map, at most budget_chars long; empty when the folder has no files. When not every module fits with its
    symbols, fewer symbols per module are shown so the map covers more modules."""
    if not files:
        return ""
    text = header(revision_label, budget_tokens)
    folders = ["", "", "## Folders"]
    for line in folder_lines(files):
        if len("\n".join(folders)) + len(line) + 1 > budget_chars * FOLDER_SHARE:
            break
        folders.append(line)
    text += "\n".join(folders)
    heading = "\n\n## Modules"
    room = budget_chars - len(text) - len(heading) - TAIL_CHARS
    lines: list[str] = []
    left_out = 0
    for max_symbols in SYMBOL_LIMITS:
        lines, left_out = module_lines(modules, room, max_symbols)
        if not left_out:
            break
    if not lines:
        return text
    if left_out:
        lines.append(f"(+{left_out} more source files not shown; search or list_files finds them.)")
    return text + heading + "".join(f"\n{line}" for line in lines)
