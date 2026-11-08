"""File URIs between the project folder as the language server sees it and paths relative to that folder.

The server runs where the project's commands run, so it sees the folder at another path than YAAP does: in the runner
`/workspace/<project>`, in a sandbox `/workspace`, and only without a runner at YAAP's own path (which on Windows, in
development, is a drive path). The tools speak in paths relative to the project folder; this map turns those into the
URIs the server knows and back.
"""

import re
from dataclasses import dataclass
from pathlib import PurePath, PurePosixPath, PureWindowsPath
from urllib.parse import quote, unquote, urlsplit

DRIVE = re.compile(r"^/[A-Za-z]:")


@dataclass(frozen=True)
class PathMap:
    # The project folder as the server sees it.
    root: str
    windows: bool = False

    def pure(self, value: str) -> PurePath:
        return PureWindowsPath(value) if self.windows else PurePosixPath(value)

    def uri(self, relative: str = ".") -> str:
        path = self.pure(self.root)
        if relative not in ("", "."):
            path = path.joinpath(*relative.split("/"))
        # Written out rather than PurePath.as_uri(), which is deprecated; the same form: file:///C:/... on Windows.
        if self.windows:
            return "file:///" + quote(path.as_posix(), safe="/:")
        return "file://" + quote(path.as_posix(), safe="/")

    def server_path(self, uri: str) -> str | None:
        """The path the URI names where the server runs, or None for a URI that is not a file."""
        parts = urlsplit(uri)
        if parts.scheme != "file":
            return None
        path = unquote(parts.path)
        if self.windows and DRIVE.match(path):
            path = path[1:]
        return path

    def relative(self, uri: str) -> str | None:
        """The path relative to the project folder, or None for a file outside it (a library, the standard library)."""
        path = self.server_path(uri)
        if path is None:
            return None
        try:
            # PureWindowsPath compares without case, as Windows does; servers may write the drive letter in lower case.
            relative = self.pure(path).relative_to(self.pure(self.root))
        except ValueError:
            return None
        text = relative.as_posix()
        return None if text in ("", ".") or text.startswith("../") else text

    def label(self, uri: str) -> str:
        """How a result names the file: relative inside the project, the server's path outside it."""
        relative = self.relative(uri)
        if relative is not None:
            return relative
        return self.server_path(uri) or uri
