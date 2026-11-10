"""Reading sections out of CHANGELOG.md. Pure."""

import re
from dataclasses import dataclass

from app.updates.versions import VERSION_PATTERN, parse_version

_HEADING = re.compile(rf"^## (?:({VERSION_PATTERN}) \(.*\)|Unreleased)\s*$")
_ACTION_HEADING = "### Action needed"


def changelog_section(text: str, version: str) -> str | None:
    """The body of `## <version> (<date>)`, without its heading and surrounding blank lines; None when absent."""
    lines = text.splitlines()
    heading = re.compile(rf"^## {re.escape(version)} \(")
    start = next((index for index, line in enumerate(lines) if heading.match(line)), None)
    if start is None:
        return None
    end = next((index for index in range(start + 1, len(lines)) if lines[index].startswith("## ")), len(lines))
    return "\n".join(lines[start + 1 : end]).strip()


@dataclass(frozen=True)
class Release:
    """One changelog section: its version (None for Unreleased), heading, notes and what the user must do."""

    version: str | None
    heading: str
    notes: str
    action: str


def split_action(body: str) -> tuple[str, str]:
    """The release notes and the text under *Action needed* (empty when there is none or it says "None.")."""
    notes, _, action = body.partition(_ACTION_HEADING)
    action = action.strip()
    return notes.strip(), "" if action.rstrip(".").lower() == "none" else action


def releases(text: str) -> list[Release]:
    """Every section of the changelog, in the order written (newest first)."""
    lines = text.splitlines()
    starts = [index for index, line in enumerate(lines) if _HEADING.match(line)]
    found: list[Release] = []
    for position, start in enumerate(starts):
        end = starts[position + 1] if position + 1 < len(starts) else len(lines)
        match = _HEADING.match(lines[start])
        notes, action = split_action("\n".join(lines[start + 1 : end]))
        found.append(
            Release(match.group(1) if match else None, lines[start].removeprefix("## ").strip(), notes, action)
        )
    return found


def sections_between(text: str, old: str, new: str) -> list[Release]:
    """The sections of the versions after `old` up to and including `new`, newest first. A `new` that is not a version
    (a commit of the main channel) also takes Unreleased."""
    low, high = parse_version(old), parse_version(new)
    chosen: list[Release] = []
    for release in releases(text):
        if release.version is None:
            if high is None:
                chosen.append(release)
            continue
        version = parse_version(release.version)
        if version is not None and (low is None or version > low) and (high is None or version <= high):
            chosen.append(release)
    return chosen


def render_between(text: str, old: str, new: str) -> str:
    """What the update script shows: every *Action needed* first, then the notes of each version in between."""
    chosen = sections_between(text, old, new)
    if not chosen:
        return "No changelog entries between these versions.\n"
    lines = ["Action needed:" if any(release.action for release in chosen) else "Action needed: none."]
    for release in chosen:
        for index, line in enumerate(release.action.splitlines()):
            lines.append(f"  {release.heading}: {line}" if index == 0 else f"    {line}")
    lines += ["", "What changed:"]
    for release in chosen:
        lines += [f"## {release.heading}", release.notes, ""]
    return "\n".join(lines).rstrip() + "\n"
