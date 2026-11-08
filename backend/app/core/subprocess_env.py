"""The environment of every subprocess YAAP starts: agent commands, git, checkpoints, OCR, MCP servers and the
provider CLIs.

YAAP's own variables (`YAAP_*`) hold its master key and its configuration, and the
credential variables below may sit in the server's environment; none of them reaches a subprocess unless the caller
passes it on purpose in `extra` (an MCP server's configured value, a secret a command uses, the per-run token of a
CLI). A provider CLI takes, with `only`, just the system's basics and the names it needs.
"""

import os
from collections.abc import Collection, Mapping

OWN_PREFIXES = ("YAAP_",)
# Credentials a subprocess never inherits: keys and tokens of model providers and GitHub that might be set for the
# server or a CLI. YAAP passes a credential explicitly where one is needed.
CREDENTIALS = frozenset(
    {
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "CLAUDE_CODE_OAUTH_TOKEN",
        "OPENAI_API_KEY",
        "CODEX_API_KEY",
        "GITHUB_TOKEN",
        "GH_TOKEN",
    }
)
# What any program needs to start and to reach the network the way the server does; on Linux and in the container,
# and the Windows equivalents for development there.
SYSTEM_VARIABLES = frozenset(
    {
        "PATH",
        "HOME",
        "USER",
        "LOGNAME",
        "SHELL",
        "LANG",
        "LANGUAGE",
        "TERM",
        "TZ",
        "TMPDIR",
        "TEMP",
        "TMP",
        "XDG_CONFIG_HOME",
        "XDG_CACHE_HOME",
        "XDG_DATA_HOME",
        "XDG_STATE_HOME",
        "XDG_RUNTIME_DIR",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
        "ALL_PROXY",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "REQUESTS_CA_BUNDLE",
        "NODE_EXTRA_CA_CERTS",
        "SYSTEMROOT",
        "SYSTEMDRIVE",
        "WINDIR",
        "COMSPEC",
        "PATHEXT",
        "USERPROFILE",
        "USERNAME",
        "HOMEDRIVE",
        "HOMEPATH",
        "APPDATA",
        "LOCALAPPDATA",
        "PROGRAMDATA",
        "PROGRAMFILES",
        "PROGRAMFILES(X86)",
        "COMMONPROGRAMFILES",
        "NUMBER_OF_PROCESSORS",
        "PROCESSOR_ARCHITECTURE",
        "OS",
    }
)
SYSTEM_PREFIXES = ("LC_",)


def hidden(name: str) -> bool:
    """A variable no subprocess inherits. Names compare case-insensitively (Windows; `http_proxy` on Linux)."""
    upper = name.upper()
    return upper.startswith(OWN_PREFIXES) or upper in CREDENTIALS


def inherited(name: str, wanted: frozenset[str] | None) -> bool:
    """Whether a subprocess inherits a variable; `wanted` (upper case) limits it to those names and the basics."""
    if hidden(name):
        return False
    if wanted is None:
        return True
    upper = name.upper()
    return upper in wanted or upper.startswith(SYSTEM_PREFIXES)


def safe_subprocess_env(
    extra: Mapping[str, str] | None = None,
    only: Collection[str] | None = None,
    source: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """The environment for a subprocess: the server's environment (or `source`) without YAAP's own variables and
    credentials, then `extra`, which always wins. With `only`, just the system's basics and those names are taken
    from the server's environment."""
    environment = os.environ if source is None else source
    wanted = None if only is None else SYSTEM_VARIABLES | {name.upper() for name in only}
    kept = {name: value for name, value in environment.items() if inherited(name, wanted)}
    return {**kept, **(extra or {})}
