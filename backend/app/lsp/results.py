"""Reading a language server's answers: locations, document symbols as an outline, diagnostics as text rows."""

from dataclasses import dataclass
from typing import Any

from app.outline.symbols import Outline, Symbol, SymbolKind, short_detail

# LSP SymbolKind numbers -> the outline's kinds; kinds not listed (variables, fields, ...) are left out of an outline,
# but their children (a module's classes) are kept.
SYMBOL_KINDS: dict[int, SymbolKind] = {
    5: "class",  # Class
    6: "method",  # Method
    9: "method",  # Constructor
    10: "enum",  # Enum
    11: "interface",  # Interface
    12: "function",  # Function
    23: "class",  # Struct
    26: "type",  # TypeParameter
}
SEVERITIES = {1: "error", 2: "warning", 3: "info", 4: "hint"}


@dataclass(frozen=True)
class Location:
    uri: str
    # 0-based, as the protocol counts.
    line: int
    character: int


def locations(result: Any) -> list[Location]:
    """A definition or references answer: a Location, a list of Locations or of LocationLinks, or null."""
    items = result if isinstance(result, list) else [result] if isinstance(result, dict) else []
    found: list[Location] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        uri = item.get("uri") or item.get("targetUri")
        place = item.get("range") or item.get("targetSelectionRange") or item.get("targetRange")
        if isinstance(uri, str) and isinstance(place, dict):
            start = place.get("start") or {}
            found.append(Location(uri, int(start.get("line", 0)), int(start.get("character", 0))))
    unique = dict.fromkeys(found)
    return sorted(unique, key=lambda location: (location.uri, location.line, location.character))


def outline_from_symbols(result: Any) -> Outline:
    """A documentSymbol answer as an outline: hierarchical DocumentSymbols, or flat SymbolInformation."""
    items = [item for item in result or [] if isinstance(item, dict)] if isinstance(result, list) else []
    if items and "location" in items[0]:
        return Outline(tuple(flat_symbols(items)))
    return Outline(tuple(tree_symbols(items)))


def tree_symbols(items: list[dict[str, Any]]) -> list[Symbol]:
    symbols: list[Symbol] = []
    for item in sorted(items, key=lambda value: value.get("range", {}).get("start", {}).get("line", 0)):
        children = tree_symbols([child for child in item.get("children") or [] if isinstance(child, dict)])
        kind = SYMBOL_KINDS.get(int(item.get("kind") or 0))
        if kind is None:
            symbols.extend(children)
            continue
        span = item.get("range") or {}
        line = int(span.get("start", {}).get("line", 0)) + 1
        end_line = int(span.get("end", {}).get("line", line - 1)) + 1
        detail = short_detail(str(item.get("detail") or ""))
        symbols.append(Symbol(kind, str(item.get("name") or "?"), line, max(end_line, line), detail, tuple(children)))
    return symbols


def flat_symbols(items: list[dict[str, Any]]) -> list[Symbol]:
    symbols: list[Symbol] = []
    for item in items:
        kind = SYMBOL_KINDS.get(int(item.get("kind") or 0))
        span = (item.get("location") or {}).get("range") or {}
        if kind is None:
            continue
        line = int(span.get("start", {}).get("line", 0)) + 1
        end_line = int(span.get("end", {}).get("line", line - 1)) + 1
        container = str(item.get("containerName") or "")
        detail = f" (in {container})" if container else ""
        symbols.append(Symbol(kind, str(item.get("name") or "?"), line, max(end_line, line), detail))
    return sorted(symbols, key=lambda symbol: symbol.line)


def sorted_diagnostics(diagnostics: tuple[dict[str, Any], ...]) -> list[dict[str, Any]]:
    """Errors first, then warnings and the rest, each in line order."""

    def order(item: dict[str, Any]) -> tuple[int, int, int]:
        start = (item.get("range") or {}).get("start") or {}
        return (int(item.get("severity") or 1), int(start.get("line", 0)), int(start.get("character", 0)))

    return sorted(diagnostics, key=order)


def diagnostic_row(item: dict[str, Any]) -> str:
    """One diagnostic as `line:column severity: message (source code)`, 1-based like read_file."""
    start = (item.get("range") or {}).get("start") or {}
    severity = SEVERITIES.get(int(item.get("severity") or 1), "error")
    message = " ".join(str(item.get("message") or "").split())[:400]
    source = str(item.get("source") or "")
    code = item.get("code")
    origin = " ".join(part for part in (source, str(code) if code not in (None, "") else "") if part)
    suffix = f" ({origin})" if origin else ""
    return f"{int(start.get('line', 0)) + 1}:{int(start.get('character', 0)) + 1} {severity}: {message}{suffix}"


def diagnostic_rows(diagnostics: tuple[dict[str, Any], ...], limit: int) -> list[str]:
    ordered = sorted_diagnostics(diagnostics)
    rows = [diagnostic_row(item) for item in ordered[:limit]]
    if len(ordered) > limit:
        rows.append(f"[{len(ordered) - limit} more not shown.]")
    return rows
