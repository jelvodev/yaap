"""Project files stay inside the directory descriptor SafeRoot pinned."""

import asyncio
import os
import socket
import stat
from pathlib import Path

import pytest

from app.targets.base import TargetError
from app.targets.container import LocalFiles
from app.targets.safe_root import RootReplacedError, SafeRoot, SafeRootError, same_root

POSIX_ONLY = pytest.mark.skipif(os.name != "posix", reason="descriptor-relative checks require POSIX")


@POSIX_ONLY
def test_failed_root_open_can_be_cleaned_up(tmp_path: Path) -> None:
    root = SafeRoot.__new__(SafeRoot)
    with pytest.raises(OSError):
        root.__init__(tmp_path / "missing")
    root.close()


@POSIX_ONLY
def test_local_files_allow_internal_links_and_refuse_external_links(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("outside-secret", encoding="utf-8")
    (root / "inside.txt").write_text("inside-secret", encoding="utf-8")
    (root / "inside-link.txt").symlink_to(root / "inside.txt")
    (root / "outside-link.txt").symlink_to(outside)
    files = LocalFiles(str(root))

    assert asyncio.run(files.read_bytes("inside-link.txt")) == b"inside-secret"
    with pytest.raises(TargetError):
        asyncio.run(files.read_bytes("outside-link.txt"))
    search = asyncio.run(files.search("secret", ".", "*.txt", 10))
    assert search == ["inside-link.txt:1: inside-secret", "inside.txt:1: inside-secret"]
    listed = asyncio.run(files.walk(".", True, set(), 20))
    assert {entry.path for entry in listed} == {"inside.txt", "inside-link.txt", "outside-link.txt"}
    assert all(entry.size == 0 for entry in listed if entry.path == "outside-link.txt")


@POSIX_ONLY
def test_directory_symlink_is_listed_without_descending(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("outside-secret", encoding="utf-8")
    (root / "external-dir").symlink_to(outside, target_is_directory=True)

    entries = asyncio.run(LocalFiles(str(root)).walk(".", True, set(), 20))
    assert [entry.path for entry in entries] == ["external-dir"]
    assert asyncio.run(LocalFiles(str(root)).search("secret", ".", "*.txt", 20)) == []


@POSIX_ONLY
def test_skipped_directory_is_pruned_from_walk_and_search(tmp_path: Path) -> None:
    root = tmp_path / "project"
    hidden = root / "ignored"
    hidden.mkdir(parents=True)
    (hidden / "secret.txt").write_text("outside-secret", encoding="utf-8")
    (root / "visible.txt").write_text("visible-secret", encoding="utf-8")
    files = LocalFiles(str(root))

    listed = asyncio.run(files.walk(".", True, {"ignored"}, 20))
    assert [entry.path for entry in listed] == ["visible.txt"]
    assert asyncio.run(files.search("secret", ".", "*.txt", 20, {"ignored"})) == [
        "visible.txt:1: visible-secret"
    ]


@pytest.mark.parametrize("operation", ["stat", "is_dir"])
def test_directory_symlink_cannot_expose_outside_metadata(tmp_path: Path, operation: str) -> None:
    root = tmp_path / "project"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("outside-secret", encoding="utf-8")
    link = root / "external-dir"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Directory symlinks are unavailable")

    safe = SafeRoot(root)
    if operation == "stat":
        with pytest.raises(OSError):
            safe.stat("external-dir/secret.txt")
    else:
        assert safe.is_dir("external-dir/secret.txt") is False


@pytest.mark.parametrize("kind", ["fifo", "socket"])
@POSIX_ONLY
def test_search_skips_special_files_without_blocking(tmp_path: Path, kind: str) -> None:
    if kind == "fifo" and not hasattr(os, "mkfifo"):
        pytest.skip("FIFO support is unavailable")
    root = tmp_path / "project"
    root.mkdir()
    path = root / "special.txt"
    listener: socket.socket | None = None
    if kind == "fifo":
        os.mkfifo(path)
    else:
        listener = socket.socket(socket.AF_UNIX)
        listener.bind(str(path))
    try:
        assert asyncio.run(LocalFiles(str(root)).search("anything", ".", "*.txt", 10)) == []
    finally:
        if listener is not None:
            listener.close()


@POSIX_ONLY
def test_proc_environ_link_is_not_read(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "environment.txt").symlink_to("/proc/self/environ")
    files = LocalFiles(str(root))

    with pytest.raises(TargetError):
        asyncio.run(files.read_bytes("environment.txt"))
    assert asyncio.run(files.search("PATH=", ".", "*.txt", 10)) == []


@POSIX_ONLY
def test_root_swap_cannot_redirect_an_open_safe_root(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "value.txt").write_text("pinned", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "value.txt").write_text("outside", encoding="utf-8")
    files = LocalFiles(str(root))
    root.rename(tmp_path / "original")
    root.symlink_to(outside, target_is_directory=True)

    with pytest.raises(TargetError, match="replaced"):
        asyncio.run(files.read_bytes("value.txt"))
    with pytest.raises(TargetError, match="replaced"):
        asyncio.run(files.write_bytes("written.txt", b"pinned-write"))
    assert (outside / "written.txt").exists() is False
    assert (tmp_path / "original" / "written.txt").exists() is False


@POSIX_ONLY
def test_parent_swap_after_resolution_refuses_operations(tmp_path: Path) -> None:
    root = tmp_path / "project"
    parent = root / "folder"
    parent.mkdir(parents=True)
    (parent / "value.txt").write_text("pinned", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "value.txt").write_text("outside", encoding="utf-8")
    safe = SafeRoot(root)
    resolve = safe._resolve

    def swap_after_resolve(parts: tuple[str, ...], **options: bool) -> tuple[int, str, tuple[str, ...]]:
        descriptor, leaf, canonical = resolve(parts, **options)
        if parts == ("folder", "value.txt"):
            parent.rename(root / "old-folder")
            parent.symlink_to(outside, target_is_directory=True)
        return descriptor, leaf, canonical

    safe._resolve = swap_after_resolve  # type: ignore[method-assign]  # force a rename after openat pins the parent
    try:
        with pytest.raises(OSError, match="parent folder was replaced"):
            safe.read_bytes("folder/value.txt")
        with pytest.raises(OSError):
            safe.write_bytes("folder/new.txt", b"pinned-write")
    finally:
        safe.close()
    assert (outside / "new.txt").exists() is False
    assert (root / "old-folder" / "new.txt").exists() is False
    assert (outside / "value.txt").read_text(encoding="utf-8") == "outside"


@pytest.mark.parametrize("move_during", ["before_write", "before_replace"])
@POSIX_ONLY
def test_write_refuses_parent_moved_outside_after_open(tmp_path: Path, move_during: str) -> None:
    root = tmp_path / "project"
    parent = root / "folder"
    parent.mkdir(parents=True)
    (parent / "existing.txt").write_text("keep", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    safe = SafeRoot(root)
    verify = safe._verify_parent_link
    calls = 0

    def move_then_verify(descriptor: int, parts: tuple[str, ...]) -> None:
        nonlocal calls
        calls += 1
        expected_call = 1 if move_during == "before_write" else 2
        if calls == expected_call:
            parent.rename(outside / "moved-folder")
            parent.symlink_to(outside / "moved-folder", target_is_directory=True)
        verify(descriptor, parts)

    safe._verify_parent_link = move_then_verify  # type: ignore[method-assign]  # force rename after dirfd open
    try:
        with pytest.raises(OSError, match="parent folder was replaced"):
            safe.write_bytes("folder/new.txt", b"must-not-escape")
    finally:
        safe.close()
    moved = outside / "moved-folder"
    assert (moved / "existing.txt").read_text(encoding="utf-8") == "keep"
    assert (moved / "new.txt").exists() is False
    assert list(moved.iterdir()) == [moved / "existing.txt"]


def test_local_files_use_the_windows_resolve_fallback_contract(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    files = LocalFiles(str(root))

    asyncio.run(files.write_bytes("nested/new.txt", b"created"))
    assert asyncio.run(files.read_bytes("nested/new.txt")) == b"created"
    asyncio.run(files.remove("nested/new.txt"))
    assert asyncio.run(files.stat("nested/new.txt")) is None


def test_write_preserves_existing_file_mode_and_uses_normal_new_file_mode(tmp_path: Path) -> None:
    if os.name != "posix":
        pytest.skip("POSIX file modes are checked on Linux")
    root = tmp_path / "project"
    root.mkdir()
    executable = root / "tool.sh"
    executable.write_text("old", encoding="utf-8")
    executable.chmod(0o751)
    reference = root / "reference.txt"
    reference.write_bytes(b"reference")
    files = LocalFiles(str(root))

    asyncio.run(files.write_bytes("tool.sh", b"new"))
    asyncio.run(files.write_bytes("created.txt", b"new"))
    assert stat.S_IMODE(executable.stat().st_mode) == 0o751
    assert stat.S_IMODE((root / "created.txt").stat().st_mode) == stat.S_IMODE(reference.stat().st_mode)


@POSIX_ONLY
def test_writes_and_removes_cannot_reach_git_through_internal_links(tmp_path: Path) -> None:
    root = tmp_path / "project"
    (root / ".git").mkdir(parents=True)
    config = root / ".git" / "config"
    config.write_text("safe", encoding="utf-8")
    (root / "config-link").symlink_to(config)
    (root / "git-dir-link").symlink_to(root / ".git", target_is_directory=True)
    files = LocalFiles(str(root))

    for path in ("config-link", "git-dir-link/config"):
        with pytest.raises(TargetError, match="into .git"):
            asyncio.run(files.write_bytes(path, b"unsafe"))
        with pytest.raises(TargetError, match="into .git"):
            asyncio.run(files.remove(path))
    assert config.read_text(encoding="utf-8") == "safe"


@pytest.mark.parametrize("operation", ["write", "remove"])
@POSIX_ONLY
def test_git_link_retargeted_after_preflight_is_still_refused(tmp_path: Path, operation: str) -> None:
    root = tmp_path / "project"
    root.mkdir()
    git_dir = root / ".git"
    git_dir.mkdir()
    config = git_dir / "config"
    config.write_text("safe", encoding="utf-8")
    ordinary = root / "ordinary"
    ordinary.mkdir()
    (ordinary / "config").write_text("ordinary", encoding="utf-8")
    alias = root / "alias"
    alias.symlink_to(ordinary, target_is_directory=True)
    files = LocalFiles(str(root))
    canonical_parts = files.safe.canonical_parts

    def retarget_after_preflight(path: str) -> tuple[str, ...]:
        canonical = canonical_parts(path)
        alias.unlink()
        alias.symlink_to(git_dir, target_is_directory=True)
        return canonical

    files.safe.canonical_parts = retarget_after_preflight  # type: ignore[method-assign]  # simulate a link swap after preflight
    if operation == "write":
        with pytest.raises(TargetError, match="into .git"):
            asyncio.run(files.write_bytes("alias/config", b"unsafe"))
    else:
        with pytest.raises(TargetError, match="into .git"):
            asyncio.run(files.remove("alias/config"))
    assert config.read_text(encoding="utf-8") == "safe"


@pytest.mark.parametrize(
    ("raised", "message"),
    [
        (RootReplacedError("unrelated wording"), "The project folder was replaced."),
        (RootReplacedError("The parent folder was replaced."), "The project folder was replaced."),
        (SafeRootError("The project folder was replaced."), "The path value.txt cannot be written safely."),
        (SafeRootError("The symbolic link leaves the project folder."), "The path value.txt cannot be written safely."),
    ],
)
def test_write_check_recognises_a_replaced_root_by_error_type_not_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, raised: SafeRootError, message: str
) -> None:
    files = LocalFiles(str(tmp_path))

    def refuse(_path: str) -> tuple[str, ...]:
        raise raised

    monkeypatch.setattr(files.safe, "canonical_parts", refuse)

    with pytest.raises(TargetError) as caught:
        files.check("value.txt", for_write=True)
    assert str(caught.value) == message


def test_root_replaced_error_is_a_safe_root_error() -> None:
    assert issubclass(RootReplacedError, SafeRootError)


@pytest.mark.parametrize(
    ("pinned", "opened", "same"),
    [
        (None, (1, 2), True),
        ((1, 2), (1, 2), True),
        ((1, 2), (1, 3), False),
        ((1, 2), (2, 2), False),
    ],
)
def test_same_root_accepts_only_the_pinned_folder(
    pinned: tuple[int, int] | None, opened: tuple[int, int], same: bool
) -> None:
    assert same_root(pinned, opened) is same


def identity(path: Path) -> tuple[int, int]:
    info = path.lstat()
    return info.st_dev, info.st_ino


@POSIX_ONLY
@pytest.mark.parametrize("swapped", [False, True])
def test_opened_root_must_be_the_pinned_folder(tmp_path: Path, swapped: bool) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "file.txt").write_text("pinned", encoding="utf-8")
    pin = identity(root)
    if swapped:
        root.rename(tmp_path / "moved")
        root.mkdir()
        (root / "file.txt").write_text("swapped", encoding="utf-8")
        with pytest.raises(RootReplacedError):
            SafeRoot(root, pin)
        with pytest.raises(TargetError, match="replaced"):
            LocalFiles(str(root), pin)
    else:
        with SafeRoot(root, pin) as safe:
            assert safe.read_bytes("file.txt") == b"pinned"
        assert LocalFiles(str(root), pin).safe.read_bytes("file.txt") == b"pinned"
