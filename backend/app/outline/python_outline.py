"""The outline of a Python file from the standard library's `ast`: classes, functions and methods, nested."""

import ast
from collections.abc import Iterator

from app.outline.symbols import OutlineError, Symbol, short_detail

Definition = ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef


def python_symbols(text: str) -> tuple[Symbol, ...]:
    try:
        tree = ast.parse(text)
    except SyntaxError as error:
        raise OutlineError(f"Python could not parse it (line {error.lineno}: {error.msg}).") from error
    return tuple(_symbols(tree.body, in_class=False))


def _symbols(body: list[ast.stmt], in_class: bool) -> Iterator[Symbol]:
    for node in body:
        if isinstance(node, ast.ClassDef):
            bases = ", ".join(ast.unparse(base) for base in [*node.bases, *node.keywords])
            yield Symbol(
                "class",
                node.name,
                _first_line(node),
                node.end_lineno or node.lineno,
                short_detail(f"({bases})") if bases else "",
                tuple(_symbols(node.body, in_class=True)),
            )
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            # Functions inside a function are local details, so the outline does not go into function bodies.
            yield Symbol(
                "method" if in_class else "function",
                node.name,
                _first_line(node),
                node.end_lineno or node.lineno,
                _signature(node),
            )
        else:
            # Definitions under `if TYPE_CHECKING:` or `try: ... except ImportError:` belong to the same level.
            for block in _blocks(node):
                yield from _symbols(block, in_class)


def _blocks(node: ast.stmt) -> list[list[ast.stmt]]:
    if isinstance(node, ast.If):
        return [node.body, node.orelse]
    if isinstance(node, ast.Try | ast.TryStar):
        return [node.body, *(handler.body for handler in node.handlers), node.orelse, node.finalbody]
    return []


def _first_line(node: Definition) -> int:
    """Decorators belong to the definition, so its range starts at the first one."""
    return min([decorator.lineno for decorator in node.decorator_list] + [node.lineno])


def _signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    returns = f" -> {ast.unparse(node.returns)}" if node.returns is not None else ""
    return short_detail(f"({ast.unparse(node.args)}){returns}")
