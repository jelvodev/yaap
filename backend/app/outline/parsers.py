"""The outliner from the simple parsers: one per file type, chosen by the file's extension."""

from collections.abc import Callable
from pathlib import PurePosixPath

from app.outline.markdown_outline import markdown_symbols
from app.outline.python_outline import python_symbols
from app.outline.script_outline import SCRIPT_SUFFIXES, script_symbols
from app.outline.symbols import Outline, OutlineError, Symbol

Parser = Callable[[str], tuple[Symbol, ...]]
PARSERS: dict[str, Parser] = {
    ".py": python_symbols,
    ".pyi": python_symbols,
    ".md": markdown_symbols,
    ".markdown": markdown_symbols,
    **{suffix: script_symbols for suffix in SCRIPT_SUFFIXES},
}


class ParserOutliner:
    """Implements `Outliner` with the parsers above."""

    async def outline(self, path: str, text: str) -> Outline | None:
        parse = PARSERS.get(PurePosixPath(path).suffix.lower())
        if parse is None:
            return None
        try:
            return Outline(parse(text))
        except OutlineError as error:
            return Outline((), str(error))
