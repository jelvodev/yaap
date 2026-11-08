"""The outline of a TypeScript or JavaScript file from a small parser.

Comments, strings, template literals and regular expressions are blanked first (newlines kept), so braces inside them
do not count. Then top-level declarations are matched at brace depth zero: functions, classes with their methods,
interfaces, type aliases, enums and `const` arrow functions. A declaration ends at its closing brace, at a semicolon,
or at a line end that does not continue the statement. This is a heuristic, not a TypeScript parser: an unusual
construct can give a wrong range, never an error.
"""

import re
from collections.abc import Iterator
from typing import Literal

from app.outline.symbols import Symbol, SymbolKind, short_detail

SCRIPT_SUFFIXES = frozenset({".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs"})

# A `/` after one of these characters or words starts a regular expression; after anything else it divides.
REGEX_AFTER = frozenset("(,=:[!&|?{};+-*%~^")
REGEX_AFTER_WORDS = frozenset({"return", "typeof", "case", "do", "else", "in", "of", "void", "yield", "await", "throw"})
NAME = r"[\w$]+"
PREFIX = r"(?:export\s+(?:default\s+)?)?(?:declare\s+)?"
# In order; "variable" is a function only when its value is one (an arrow function or a function expression).
DECLARATIONS: tuple[tuple[SymbolKind | Literal["variable"], re.Pattern[str]], ...] = (
    ("class", re.compile(rf"{PREFIX}(?:abstract\s+)?class\b(?!\s+(?:extends|implements)\b)\s*(?P<name>{NAME})?")),
    ("function", re.compile(rf"{PREFIX}(?:async\s+)?function\b\s*\*?\s*(?P<name>{NAME})?\s*[<(]")),
    ("interface", re.compile(rf"{PREFIX}interface\s+(?P<name>{NAME})")),
    ("type", re.compile(rf"{PREFIX}type\s+(?P<name>{NAME})")),
    ("enum", re.compile(rf"{PREFIX}(?:const\s+)?enum\s+(?P<name>{NAME})")),
    ("variable", re.compile(rf"{PREFIX}(?:const|let|var)\s+(?P<name>{NAME})\s*(?::[^=;]+)?=(?![=>])\s*")),
)
NON_SPACE = re.compile(r"\S")
MODIFIERS = r"(?:(?:public|private|protected|static|readonly|abstract|override|declare|async|accessor)\s+)*"
METHOD = re.compile(rf"{MODIFIERS}(?:(?P<accessor>get|set)\s+)?\*?\s*(?P<name>#?{NAME})\s*\??\s*(?:<[^>(]*>)?\s*\(")
PROPERTY = re.compile(rf"{MODIFIERS}(?P<name>#?{NAME})\s*[?!]?\s*(?::[^=;]*)?=(?![=>])\s*")
NOT_METHODS = frozenset({"if", "for", "while", "switch", "catch", "return", "function", "with"})
ARROW_AFTER_PARAMETERS = re.compile(r"\s*(?::[^=;{]*?)?=>")
# A line ending in one of these, or a next line starting with one, continues the statement.
CONTINUES_AT_END = ("=", "|", "&", ",", "(", "[", "{", "=>", "?", ":", "+", "-", "*", ".", "<", "extends")
CONTINUES_AT_START = ("|", "&", ".", "?", ":", "=", ")", "]", ">", "+", "-", "extends", "implements")
BODY_KINDS = frozenset({"class", "function", "interface", "enum"})


def script_symbols(text: str) -> tuple[Symbol, ...]:
    return tuple(_Scanner(mask(text), text).top_level())


def mask(text: str) -> str:
    """The code with comments, string contents, template literals and regular expressions replaced by spaces."""
    out = list(text)
    length = len(text)
    index = 0
    last = -1  # the last significant code character, to tell a regular expression from a division

    def blank(start: int, end: int) -> None:
        for position in range(start, min(end, length)):
            if out[position] != "\n":
                out[position] = " "

    while index < length:
        char = text[index]
        following = text[index + 1] if index + 1 < length else ""
        if char == "/" and following == "/":
            end = text.find("\n", index)
            end = length if end < 0 else end
        elif char == "/" and following == "*":
            end = text.find("*/", index + 2)
            end = length if end < 0 else end + 2
        elif char in "'\"":
            end = _string_end(text, index, char)
            blank(index + 1, end - 1)
            index, last = end, end - 1
            continue
        elif char == "`":
            end = _template_end(text, index)
            blank(index + 1, end - 1)
            index, last = end, end - 1
            continue
        elif char == "/" and _starts_regex(text, last) and (end := _regex_end(text, index)) > 0:
            blank(index + 1, end - 1)
            index, last = end, end - 1
            continue
        else:
            if not char.isspace():
                last = index
            index += 1
            continue
        blank(index, end)
        index = end
    return "".join(out)


def _string_end(text: str, start: int, quote: str) -> int:
    """Just past the closing quote; a quote-delimited string cannot span lines, so a stray quote (in JSX text, say)
    stops at the line end."""
    index = start + 1
    while index < len(text):
        char = text[index]
        if char == "\\":
            index += 2
        elif char == quote:
            return index + 1
        elif char == "\n":
            return index
        else:
            index += 1
    return len(text)


def _template_end(text: str, start: int) -> int:
    index = start + 1
    while index < len(text):
        char = text[index]
        if char == "\\":
            index += 2
        elif char == "`":
            return index + 1
        elif char == "$" and text.startswith("{", index + 1):
            index = _expression_end(text, index + 2)
        else:
            index += 1
    return len(text)


def _expression_end(text: str, start: int) -> int:
    """Just past the `}` that closes a template literal's `${`, skipping nested strings and templates."""
    depth = 1
    index = start
    while index < len(text):
        char = text[index]
        if char in "'\"":
            index = _string_end(text, index, char)
            continue
        if char == "`":
            index = _template_end(text, index)
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index + 1
        index += 1
    return len(text)


def _starts_regex(text: str, last: int) -> bool:
    if last < 0 or text[last] in REGEX_AFTER:
        return True
    start = last
    while start > 0 and (text[start - 1].isalnum() or text[start - 1] in "_$"):
        start -= 1
    return text[start : last + 1] in REGEX_AFTER_WORDS


def _regex_end(text: str, start: int) -> int:
    """Just past a regular expression's flags, or 0 when no closing `/` follows on the line (then it divides)."""
    index = start + 1
    in_class = False
    while index < len(text) and text[index] != "\n":
        char = text[index]
        if char == "\\":
            index += 2
            continue
        if char == "[":
            in_class = True
        elif char == "]":
            in_class = False
        elif char == "/" and not in_class:
            index += 1
            while index < len(text) and text[index].isalpha():
                index += 1
            return index
        index += 1
    return 0


class _Scanner:
    def __init__(self, masked: str, original: str) -> None:
        self.text = masked
        # Details such as parameter lists are shown as written, with their strings.
        self.original = original
        self.starts = [0] + [match.end() for match in re.finditer("\n", masked)]
        self.depths = self._line_depths()

    def _line_depths(self) -> list[tuple[int, int]]:
        """Per line, the brace depth and the parenthesis depth at its start."""
        depths: list[tuple[int, int]] = []
        braces = parens = 0
        for line in self.text.split("\n"):
            depths.append((braces, parens))
            for char in line:
                if char == "{":
                    braces += 1
                elif char == "}":
                    braces = max(braces - 1, 0)
                elif char == "(":
                    parens += 1
                elif char == ")":
                    parens = max(parens - 1, 0)
        return depths

    def line_of(self, index: int) -> int:
        """The 0-based line of a position."""
        low, high = 0, len(self.starts) - 1
        while low < high:
            middle = (low + high + 1) // 2
            if self.starts[middle] <= index:
                low = middle
            else:
                high = middle - 1
        return low

    def code_start(self, line: int) -> int | None:
        """Where the line's code starts, or None for a blank line (the patterns would otherwise match the next one)."""
        start = self.starts[line]
        while start < len(self.text) and self.text[start] in " \t":
            start += 1
        return None if start >= len(self.text) or self.text[start] in "\r\n" else start

    def top_level(self) -> Iterator[Symbol]:
        line = 0
        while line < len(self.starts):
            symbol = self.declaration(line) if self.depths[line] == (0, 0) else None
            if symbol is not None:
                yield symbol
                line = symbol.end_line  # end_line is 1-based, so this is the line after it
            else:
                line += 1

    def declaration(self, line: int) -> Symbol | None:
        start = self.code_start(line)
        if start is None:
            return None
        for found, pattern in DECLARATIONS:
            match = pattern.match(self.text, start)
            if match is not None:
                break
        else:
            return None
        if found == "variable" and not self.is_function_value(match.end()):
            return None
        kind: SymbolKind = "function" if found == "variable" else found
        end = self.statement_end(start, needs_body=kind in BODY_KINDS)
        end_line = self.line_of(end)
        children = tuple(self.members(start, end)) if kind == "class" else ()
        detail = short_detail(self.parameters(match.end() - 1)) if found == "function" else ""
        return Symbol(kind, match.group("name") or "default", line + 1, end_line + 1, detail, children)

    def is_function_value(self, index: int) -> bool:
        """Whether a variable's value, starting at index, is a function: `function`, `(…) =>`, `x =>` or async."""
        rest = self.text[index : index + 200]
        if rest.startswith("async"):
            index += len(rest) - len(rest[5:].lstrip())
            rest = self.text[index : index + 200]
        if re.match(r"function\b", rest) or re.match(rf"{NAME}\s*=>", rest):
            return True
        generic = re.match(r"<[^>(]*>\s*", rest)
        if generic:
            index += generic.end()
        if not self.text.startswith("(", index):
            return False
        closing = self.matching(index)
        return closing > 0 and ARROW_AFTER_PARAMETERS.match(self.text, closing + 1) is not None

    def matching(self, index: int) -> int:
        """The position of the bracket that closes the one at index, or -1."""
        pairs = {"(": ")", "[": "]", "{": "}"}
        stack: list[str] = []
        for position in range(index, len(self.text)):
            char = self.text[position]
            if char in pairs:
                stack.append(pairs[char])
            elif char in ")]}":
                if not stack or stack.pop() != char:
                    return -1
                if not stack:
                    return position
        return -1

    def statement_end(self, start: int, needs_body: bool) -> int:
        """Where the statement that starts at start ends: its body's closing brace, a semicolon, or a line end."""
        nesting = 0
        index = start
        while index < len(self.text):
            char = self.text[index]
            if char in "({[":
                nesting += 1
            elif char in ")}]":
                nesting = max(nesting - 1, 0)
                if char == "}" and nesting == 0 and needs_body:
                    return index
            elif char == ";" and nesting == 0:
                return index
            elif char == "\n" and nesting == 0 and not needs_body and not self.continues(index):
                return max(index - 1, start)
            index += 1
        return len(self.text) - 1

    def continues(self, newline: int) -> bool:
        line = self.line_of(newline)
        ending = self.text[self.starts[line] : newline].rstrip()
        if not ending or ending.endswith(CONTINUES_AT_END):
            return True
        following = NON_SPACE.search(self.text, newline + 1)
        return following is not None and self.text.startswith(CONTINUES_AT_START, following.start())

    def members(self, class_start: int, class_end: int) -> Iterator[Symbol]:
        body = self.text.find("{", class_start, class_end + 1)
        if body < 0:
            return
        depth = self.depths[self.line_of(class_start)][0] + 1
        line = self.line_of(body) + 1
        last = self.line_of(class_end)
        while line < last:
            symbol = self.member(line) if self.depths[line] == (depth, 0) else None
            if symbol is not None:
                yield symbol
                line = max(symbol.end_line, line + 1)
            else:
                line += 1

    def member(self, line: int) -> Symbol | None:
        start = self.code_start(line)
        if start is None:
            return None
        method = METHOD.match(self.text, start)
        if method is not None and method.group("name") not in NOT_METHODS:
            name = method.group("name")
            if method.group("accessor"):
                name = f"{method.group('accessor')} {name}"
            end = self.statement_end(start, needs_body=True)
            detail = short_detail(self.parameters(method.end() - 1))
            return Symbol("method", name, line + 1, self.line_of(end) + 1, detail)
        prop = PROPERTY.match(self.text, start)
        if prop is not None and self.is_function_value(prop.end()):
            end = self.statement_end(start, needs_body=False)
            return Symbol("method", prop.group("name"), line + 1, self.line_of(end) + 1)
        return None

    def parameters(self, opening: int) -> str:
        closing = self.matching(opening)
        return self.original[opening : closing + 1] if closing > 0 else ""
