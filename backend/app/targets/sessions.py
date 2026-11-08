"""Shell sessions on a target.

A session is not a shell that keeps running. Each command runs in a fresh shell that restores the session's state (its
working folder and exported variables, so an activated virtual environment too), runs the command and saves the state
again when it exits. Shell functions and unexported variables do not carry over. Secret variables (`YAAP_SECRET_*`)
are never saved: each command gets the ones it names.

Where the state lives depends on the target: in the container a folder under `data/shells/`; over SSH a file in the
target's temp folder named after the session's token (`yaap-shell-<token>`). Files there that nobody used for a day
belong to ended sessions and are removed by the next session command on that computer.
"""

from dataclasses import dataclass
from pathlib import Path

STATE_PREFIX = "yaap-shell-"
# State files on a target untouched this long belong to ended sessions (they end after two idle hours).
PRUNE_MINUTES = 24 * 60


@dataclass(frozen=True)
class ShellState:
    """The session a command runs in."""

    # Names the state on a target: yaap-shell-<token> in its temp folder.
    token: str
    # The state folder in the container (under data/shells/); unused on a target.
    folder: Path | None = None
    # run_command saves the state again after the command; start_process only restores it.
    save: bool = True


# `$__yaap_state` is a path prefix: <prefix>.env holds `export -p`, <prefix>.cwd the working folder.
BASH_RESTORE = """__yaap_state={state}
if [ -f "$__yaap_state.env" ]; then . "$__yaap_state.env" 2>/dev/null; fi
if [ -f "$__yaap_state.cwd" ]; then cd "$(cat "$__yaap_state.cwd")" 2>/dev/null; fi
"""
BASH_SAVE = """__yaap_save() {
  __yaap_status=$?
  for __yaap_name in $(compgen -e); do
    case $__yaap_name in YAAP_SECRET_*) unset "$__yaap_name" ;; esac
  done
  (umask 077; export -p > "$__yaap_state.env"; pwd > "$__yaap_state.cwd")
  exit $__yaap_status
}
trap __yaap_save EXIT
"""


def bash_session(state: str, command: str, save: bool, before_command: str = "") -> str:
    """A bash script that restores the session, runs the command and (when saving) stores the state on exit.

    `state` is a bash word for the state prefix (quoted, or with variables to expand); `before_command` comes after
    the restore, so what it sets (the command's secret variables) wins over the saved state.
    """
    script = BASH_RESTORE.format(state=state) + before_command
    if save:
        script += BASH_SAVE
    return script + command + "\n"
