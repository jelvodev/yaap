"""The project files an answer names, read for its check.

Reads go through the project's confined `TargetFiles`, so a name cannot leave the project folder; only text files up
to MAX_FILE_BYTES are read. A name that is no file of the project is skipped (an answer also names examples and
products); a file that exists but cannot be read is named in the notes.
"""

from app.reviews.answer import MAX_FILE_BYTES, FileMaterial
from app.targets.base import TargetError, TargetFiles


async def read_named_files(files: TargetFiles, paths: list[str]) -> tuple[list[FileMaterial], list[str]]:
    """The readable text files among `paths`, and a note for each one that exists but was left out."""
    found: list[FileMaterial] = []
    notes: list[str] = []
    for path in paths:
        try:
            checked = files.check(path)
            entry = await files.stat(checked)
            if entry is None or entry.is_dir:
                continue
            if entry.size > MAX_FILE_BYTES:
                notes.append(f"{path} is larger than {MAX_FILE_BYTES // 1000} KB and was not read.")
                continue
            data = await files.read_bytes(checked)
        except TargetError as error:
            notes.append(f"{path} could not be read: {error}")
            continue
        try:
            if b"\0" in data:
                raise UnicodeDecodeError("utf-8", b"", 0, 1, "binary")
            found.append(FileMaterial(path, data.decode("utf-8")))
        except UnicodeDecodeError:
            notes.append(f"{path} is not a text file and was not read.")
    return found, notes
