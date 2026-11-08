"""Hardening of YAAP's own server process.

Everything YAAP starts runs as the same user as YAAP. Without more, any of those processes could read YAAP's
`/proc/<pid>/environ` (the master key it started with), its memory, or attach a debugger to it. A process that is not
dumpable (`prctl(PR_SET_DUMPABLE, 0)`) has its `/proc` files owned by root and refuses ptrace from other processes of
the same user. The flag is reset by `execve`, so the commands, CLIs and servers YAAP starts are ordinary processes.

Linux only; elsewhere (development on Windows or macOS) there is nothing to set.
"""

import ctypes
import logging
import os
import sys
from typing import Protocol

logger = logging.getLogger(__name__)

PR_GET_DUMPABLE = 3
PR_SET_DUMPABLE = 4


class Prctl(Protocol):
    def __call__(self, option: int, arg2: int, arg3: int, arg4: int, arg5: int, /) -> int: ...


def linux_prctl(platform: str = sys.platform) -> Prctl | None:
    """libc's prctl on Linux, or None on another system."""
    if not platform.startswith("linux"):
        return None
    libc = ctypes.CDLL(None, use_errno=True)
    prctl: Prctl = libc.prctl
    return prctl


def mark_not_dumpable(prctl: Prctl | None) -> bool:
    """Mark the process not dumpable; whether that happened. A failure is logged, and YAAP starts anyway: the flag is
    a second lock behind the container."""
    if prctl is None:
        return False
    if prctl(PR_SET_DUMPABLE, 0, 0, 0, 0) != 0:
        error = ctypes.get_errno()
        logger.warning("Could not mark YAAP's process not dumpable: %s", os.strerror(error) if error else "unknown")
        return False
    return True


def harden_process() -> bool:
    return mark_not_dumpable(linux_prctl())
