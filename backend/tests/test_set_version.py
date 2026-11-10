"""Setting the version everywhere and rolling the changelog."""

from pathlib import Path

import pytest

from app.scripts.set_version import (
    SPOTS,
    ReleaseError,
    changelog_section,
    is_newer,
    parse_version,
    roll_changelog,
    set_version,
)

CHANGELOG = """# Changelog

## Unreleased

- Something new.

### Action needed

Run the thing.

## 0.5.7 (2026-11-09)

- Older.

### Action needed

None.
"""
EMPTY_UNRELEASED = "## Unreleased\n\n### Action needed\n\nNone.\n\n## 0.5.7 (2026-11-09)\n"


@pytest.mark.parametrize(
    ("text", "expected"),
    [("0.5.7", (0, 5, 7)), ("1.0.0", (1, 0, 0)), ("0.10.2", (0, 10, 2))],
)
def test_parse_version_reads_three_numbers(text: str, expected: tuple[int, int, int]) -> None:
    assert parse_version(text) == expected


@pytest.mark.parametrize("text", ["", "1", "1.2", "1.2.3.4", "v1.2.3", "1.2.x", "1.2.3-rc1"])
def test_parse_version_refuses_what_is_not_a_version(text: str) -> None:
    with pytest.raises(ReleaseError):
        parse_version(text)


@pytest.mark.parametrize(
    ("new", "old", "expected"),
    [
        ("0.5.8", "0.5.7", True),
        ("0.6.0", "0.5.7", True),
        ("0.10.0", "0.9.0", True),
        ("0.5.7", "0.5.7", False),
        ("0.5.6", "0.5.7", False),
        ("0.9.0", "0.10.0", False),
    ],
)
def test_is_newer_compares_numbers_not_text(new: str, old: str, expected: bool) -> None:
    assert is_newer(new, old) is expected


def test_changelog_section_returns_the_body_of_a_version() -> None:
    assert changelog_section(CHANGELOG, "0.5.7") == "- Older.\n\n### Action needed\n\nNone."


def test_changelog_section_stops_at_the_next_heading() -> None:
    text = CHANGELOG.replace("## Unreleased", "## 0.6.0 (2026-12-01)")
    assert changelog_section(text, "0.6.0").endswith("Run the thing.")


def test_changelog_section_refuses_an_unknown_version() -> None:
    with pytest.raises(ReleaseError):
        changelog_section(CHANGELOG, "0.9.9")


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_roll_changelog_names_the_release_and_leaves_a_fresh_unreleased(newline: str) -> None:
    text = CHANGELOG.replace("\n", newline)
    rolled = roll_changelog(text, "0.6.0", "2026-12-01")
    assert rolled.startswith(f"# Changelog{newline}{newline}## Unreleased{newline}{newline}### Action needed")
    assert f"None.{newline}{newline}## 0.6.0 (2026-12-01){newline}{newline}- Something new." in rolled
    assert rolled.count("## Unreleased") == 1
    assert rolled.endswith(text.split("## 0.5.7")[1])
    assert rolled.count("\n") == rolled.count(newline)


@pytest.mark.parametrize("empty", [EMPTY_UNRELEASED, "# Changelog\n"])
def test_roll_changelog_refuses_an_empty_or_missing_unreleased(empty: str) -> None:
    with pytest.raises(ReleaseError):
        roll_changelog(empty, "0.6.0", "2026-12-01")


def make_repository(root: Path, newline: str) -> None:
    files = {
        "backend/app/version.py": 'VERSION = "0.5.7"\n',
        "backend/pyproject.toml": '[project]\nname = "yaap"\nversion = "0.5.7"\ndependencies = ["x==0.5.7"]\n',
        "frontend/package.json": '{\n  "name": "yaap-frontend",\n  "version": "0.5.7",\n  "dependencies": {}\n}\n',
        "frontend/package-lock.json": '{\n  "name": "yaap-frontend",\n  "version": "0.5.7",\n  "packages": {\n'
        '    "": {\n      "name": "yaap-frontend",\n      "version": "0.5.7"\n    },\n'
        '    "node_modules/x": {\n      "version": "0.5.7"\n    }\n  }\n}\n',
        "frontend/openapi.json": '{\n  "info": {\n    "title": "YAAP",\n    "version": "0.5.7"\n  }\n}\n',
        "docs/roadmap.md": "The current version is **0.5.7**.\n",
        "CHANGELOG.md": CHANGELOG,
    }
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.replace("\n", newline).encode())


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_set_version_changes_every_place_and_keeps_line_endings(tmp_path: Path, newline: str) -> None:
    make_repository(tmp_path, newline)
    assert set_version(tmp_path, "0.6.0", "2026-12-01") == "0.5.7"
    for wanted in SPOTS:
        data = (tmp_path / wanted.path).read_bytes().decode()
        assert data.count("0.6.0") == wanted.count
        assert ("\r\n" in data) == (newline == "\r\n")
    # Only the places that name the project's own version changed.
    assert '"x==0.5.7"' in (tmp_path / "backend/pyproject.toml").read_bytes().decode()
    lock = (tmp_path / "frontend/package-lock.json").read_bytes().decode().replace("\r\n", "\n")
    assert '"node_modules/x": {\n      "version": "0.5.7"' in lock
    assert "## 0.6.0 (2026-12-01)" in (tmp_path / "CHANGELOG.md").read_bytes().decode()


def test_set_version_refuses_a_version_that_is_not_newer_and_writes_nothing(tmp_path: Path) -> None:
    make_repository(tmp_path, "\n")
    with pytest.raises(ReleaseError):
        set_version(tmp_path, "0.5.7", "2026-12-01")
    assert (tmp_path / "backend/app/version.py").read_text() == 'VERSION = "0.5.7"\n'


def test_set_version_stops_before_writing_when_a_place_is_missing(tmp_path: Path) -> None:
    make_repository(tmp_path, "\n")
    (tmp_path / "docs/roadmap.md").write_text("No version here.\n")
    with pytest.raises(ReleaseError):
        set_version(tmp_path, "0.6.0", "2026-12-01")
    assert (tmp_path / "backend/app/version.py").read_text() == 'VERSION = "0.5.7"\n'
