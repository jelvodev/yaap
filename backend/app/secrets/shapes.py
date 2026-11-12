"""Spot text that looks like a key or token, without ever returning the value.

A document that is imported into the Blueprint is read by the model and committed to git, so a pasted key must not
travel with it. The check is shaped on well-known prefixes and on assignments, so prose about keys is not flagged.
"""

import re
from collections.abc import Iterator
from typing import Any

# Well-known credential shapes. The prefix is anchored so that a word such as "task-planning-and-execution" is
# not taken for an `sk-` key: a real key has digits.
TOKEN_SHAPES = (
    re.compile(r"(?<![A-Za-z0-9_-])sk-(?=[A-Za-z0-9_-]*\d)[A-Za-z0-9_-]{20,}"),
    re.compile(r"(?<![A-Za-z0-9_])gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"(?<![A-Za-z0-9_])github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"(?<![A-Za-z0-9])AKIA[0-9A-Z]{16}(?![A-Za-z0-9])"),
    re.compile(r"(?<![A-Za-z0-9])xox[abpr]-[A-Za-z0-9-]{10,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?<![A-Za-z0-9_-])AIza[0-9A-Za-z_-]{35}"),
    re.compile(r"(?<![A-Za-z0-9_])[sr]k_live_[0-9A-Za-z]{10,}"),
    re.compile(r"eyJ[A-Za-z0-9_-]{5,}\.eyJ[A-Za-z0-9_-]{5,}\."),
    re.compile(r"(?<![A-Za-z0-9_-])glpat-[A-Za-z0-9_-]{20,}"),
    re.compile(r"(?<![A-Za-z0-9_])npm_[A-Za-z0-9]{36}"),
    re.compile(r"(?<![A-Za-z0-9_])hf_[A-Za-z0-9]{30,}"),
    # Credentials inside a URL; a placeholder or variable instead of the password is fine.
    re.compile(r"://[^/\s:@]+:(?![{$<])[^/\s@]{3,}@"),
)
ASSIGNMENT = re.compile(
    r"(?:api[_-]?key|token|secret|password)\w*[\"']?\s*[=:]\s*[\"']?(?P<value>[^\s\"']{16,})", re.IGNORECASE
)
# A reference to a secret, an environment variable or a template is not a secret.
REFERENCE_START = ("{", "$", "<", "%", "[", "(")


def looks_like_secret_value(value: str) -> bool:
    if value.startswith(REFERENCE_START):
        return False
    # Prose and file names have no digits; a generated key almost always has letters and digits.
    return any(char.isdigit() for char in value) and any(char.isalpha() for char in value)


def key_shaped_lines(text: str) -> list[int]:
    """The 1-based numbers of the lines that look like they hold a key, token or private key."""
    lines: list[int] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if any(shape.search(line) for shape in TOKEN_SHAPES):
            lines.append(number)
        elif any(looks_like_secret_value(match.group("value")) for match in ASSIGNMENT.finditer(line)):
            lines.append(number)
    return lines


def strings_in(value: Any) -> Iterator[str]:
    """Every string in a value made of strings, lists and dictionaries (keys included)."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield str(key)
            yield from strings_in(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from strings_in(item)


def holds_key_shape(value: Any) -> bool:
    """Whether any string in the value looks like a key or token."""
    return any(key_shaped_lines(text) for text in strings_in(value))


def key_refusal(lines: list[int]) -> str | None:
    """The sentence shown to the user for text with key-shaped lines; None when there are none."""
    if not lines:
        return None
    if len(lines) == 1:
        return f"Line {lines[0]} looks like a key or token; remove it from the source and import again."
    shown = ", ".join(str(line) for line in lines[:5])
    return f"Lines {shown} look like a key or token; remove them from the source and import again."
