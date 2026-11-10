"""Release versions as YAAP names them (`v1.2.3` tags, `1.2.3` inside): strict parsing and ordering. Pure."""

import re
from collections.abc import Iterable

Version = tuple[int, int, int]

VERSION_PATTERN = r"\d+\.\d+\.\d+"
_VERSION = re.compile(VERSION_PATTERN)
_TAG = re.compile(rf"v({VERSION_PATTERN})")


def parse_version(text: str) -> Version | None:
    """Three numbers from `1.2.3`, so 0.10.0 is newer than 0.9.0; None for anything else."""
    if not _VERSION.fullmatch(text):
        return None
    major, minor, patch = (int(part) for part in text.split("."))
    return major, minor, patch


def parse_tag(tag: str) -> Version | None:
    """The version of a tag named `v1.2.3`; pre-releases (`v1.2.3-rc1`) and other names are not releases."""
    match = _TAG.fullmatch(tag)
    return parse_version(match.group(1)) if match else None


def format_version(version: Version) -> str:
    return ".".join(str(part) for part in version)


def newest_tag(tags: Iterable[str]) -> str | None:
    """The version (without the `v`) of the newest release tag, skipping pre-releases and malformed names."""
    found = [version for version in (parse_tag(tag) for tag in tags) if version is not None]
    return format_version(max(found)) if found else None


def is_newer(new: str, old: str) -> bool:
    """Whether version `new` is newer than `old`; a text that is not a version is never newer."""
    parsed_new, parsed_old = parse_version(new), parse_version(old)
    return parsed_new is not None and parsed_old is not None and parsed_new > parsed_old
