"""Descriptor-relative access to files below one pinned directory.

`SafeRoot` is the implementation for this platform: POSIX pins the root and every parent by descriptor, Windows
resolves paths and keeps them below the root. The choice is a conditional import on `sys.platform` (Windows is
`win32`, as `os.name == "nt"`), which type checkers follow, so `SafeRoot` stays a class usable in annotations.
"""

import sys

from app.targets.safe_root_base import RootReplacedError, SafeRootBase, SafeRootError, relative_parts, same_root

if sys.platform == "win32":
    from app.targets.safe_root_windows import WindowsSafeRoot as SafeRoot
else:
    from app.targets.safe_root_posix import PosixSafeRoot as SafeRoot

__all__ = ["RootReplacedError", "SafeRoot", "SafeRootBase", "SafeRootError", "relative_parts", "same_root"]
