"""Set YAAP's version in every place it lives and roll the changelog, for scripts/release.ps1 and release.sh.

Usage (from backend/):
    python -m app.scripts.set_version 0.6.0 [--date 2026-12-01]
    python -m app.scripts.set_version --section 0.6.0     # print that version's changelog section
"""

import argparse
import re
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from app.updates import changelog, versions

VERSION_PATTERN = versions.VERSION_PATTERN
UNRELEASED_HEADING = "## Unreleased"
EMPTY_UNRELEASED = "## Unreleased\n\n### Action needed\n\nNone.\n"


class ReleaseError(Exception):
    """The version cannot be set; the message says why."""


@dataclass(frozen=True)
class Spot:
    """Where a version is written in a file: group 1 is the text before it, group 2 the version, group 3 the text after.

    `count` is how many times the pattern must match, so a place that appears or disappears is noticed."""

    path: str
    pattern: re.Pattern[str]
    count: int = 1


SPOTS = (
    Spot("backend/app/version.py", re.compile(rf'(^VERSION = ")({VERSION_PATTERN})(")', re.MULTILINE)),
    Spot("backend/pyproject.toml", re.compile(rf'(^version = ")({VERSION_PATTERN})(")', re.MULTILINE)),
    Spot("frontend/package.json", re.compile(rf'(^  "version": ")({VERSION_PATTERN})(")', re.MULTILINE)),
    # The package itself and its entry under "packages"; dependencies are named differently.
    Spot(
        "frontend/package-lock.json",
        re.compile(rf'("name": "yaap-frontend",\r?\n\s*"version": ")({VERSION_PATTERN})(")'),
        count=2,
    ),
    Spot(
        "frontend/openapi.json",
        re.compile(rf'("title": "YAAP",\r?\n\s*"version": ")({VERSION_PATTERN})(")'),
    ),
    Spot("docs/roadmap.md", re.compile(rf"(The current version is \*\*)({VERSION_PATTERN})(\*\*\.)")),
)
VERSION_FILE = "backend/app/version.py"
CHANGELOG = "CHANGELOG.md"


def parse_version(text: str) -> tuple[int, int, int]:
    """A version as three numbers, so 0.10.0 is newer than 0.9.0."""
    parsed = versions.parse_version(text)
    if parsed is None:
        raise ReleaseError(f"'{text}' is not a version like 1.2.3.")
    return parsed


def is_newer(new: str, old: str) -> bool:
    return parse_version(new) > parse_version(old)


def newline_of(text: str) -> str:
    return "\r\n" if "\r\n" in text else "\n"


def replace_version(text: str, wanted: Spot, version: str) -> str:
    """The text with the version written at the spot; the number of places must be the one the spot names."""
    found = len(wanted.pattern.findall(text))
    if found != wanted.count:
        raise ReleaseError(f"{wanted.path}: found {found} places for the version, expected {wanted.count}.")
    return wanted.pattern.sub(lambda match: f"{match.group(1)}{version}{match.group(3)}", text)


def versions_at(text: str, wanted: Spot) -> list[str]:
    return [match.group(2) for match in wanted.pattern.finditer(text)]


def changelog_section(text: str, version: str) -> str:
    """The body of `## <version> (<date>)`, without its heading and without surrounding blank lines."""
    section = changelog.changelog_section(text, version)
    if section is None:
        raise ReleaseError(f"CHANGELOG.md has no section for {version}.")
    return section


def roll_changelog(text: str, version: str, day: str) -> str:
    """`## Unreleased` becomes `## <version> (<day>)` and a fresh, empty Unreleased goes above it."""
    newline = newline_of(text)
    lines = text.split(newline)
    if UNRELEASED_HEADING not in lines:
        raise ReleaseError("CHANGELOG.md has no '## Unreleased' section.")
    index = lines.index(UNRELEASED_HEADING)
    end = next((i for i in range(index + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    placeholders = ("### Action needed", "None.")
    body = [line for line in lines[index + 1 : end] if line.strip() and line.strip() not in placeholders]
    if not body:
        raise ReleaseError("The Unreleased section of CHANGELOG.md lists no changes.")
    fresh = EMPTY_UNRELEASED.split("\n")
    rolled = fresh + [f"## {version} ({day})"] + lines[index + 1 :]
    return newline.join(lines[:index] + rolled)


def current_version(root: Path) -> str:
    text = (root / VERSION_FILE).read_text(encoding="utf-8")
    found = versions_at(text, SPOTS[0])
    if len(found) != 1:
        raise ReleaseError(f"{VERSION_FILE} does not hold exactly one version.")
    return found[0]


def set_version(root: Path, version: str, day: str) -> str:
    """Write the version everywhere and roll the changelog; returns the version it replaced.

    Everything is computed and checked before the first file is written."""
    old = current_version(root)
    if not is_newer(version, old):
        raise ReleaseError(f"{version} is not newer than the current version {old}.")
    contents: dict[str, str] = {}
    for wanted in SPOTS:
        text = (root / wanted.path).read_bytes().decode("utf-8")
        contents[wanted.path] = replace_version(text, wanted, version)
    for wanted in SPOTS:
        left = [found for found in versions_at(contents[wanted.path], wanted) if found != version]
        if left:
            raise ReleaseError(f"{wanted.path} still names version {left[0]} after the change.")
    contents[CHANGELOG] = roll_changelog((root / CHANGELOG).read_bytes().decode("utf-8"), version, day)
    for path, text in contents.items():
        (root / path).write_bytes(text.encode("utf-8"))
    return old


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Set YAAP's version everywhere and roll the changelog.")
    parser.add_argument("version", help="the version, for example 0.6.0")
    parser.add_argument("--section", action="store_true", help="print the changelog section of the version and stop")
    parser.add_argument("--date", default=date.today().isoformat(), help="the release date, default today")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[3], help="the repository folder")
    args = parser.parse_args(argv)
    try:
        parse_version(args.version)
        if args.section:
            sys.stdout.write(changelog_section((args.root / CHANGELOG).read_text(encoding="utf-8"), args.version) + "\n")
            return 0
        old = set_version(args.root, args.version, args.date)
    except ReleaseError as error:
        sys.stderr.write(f"{error}\n")
        return 1
    sys.stdout.write(f"Version {old} -> {args.version}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
