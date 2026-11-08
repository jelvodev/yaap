"""Runtime configuration read from environment variables."""

import logging
import os
import stat
from dataclasses import dataclass
from pathlib import Path

from cryptography.fernet import Fernet

logger = logging.getLogger(__name__)


def environment_value(name: str, default: str = "") -> str:
    """The value of a YAAP_* variable, or the default when it is not set."""
    return os.environ.get(name, default)

MASTER_KEY_HELP = (
    "YAAP_MASTER_KEY is missing or invalid. Generate one with:\n"
    '  python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"\n'
    "and put it in your .env file, or in a file named by YAAP_MASTER_KEY_FILE (for a Docker secret). Keep it outside "
    "the data folder and back it up separately."
)
PREVIOUS_KEY_HELP = "YAAP_MASTER_KEY_PREVIOUS is not a valid master key. Use the exact key YAAP ran with before."
# The variables that hold a master key; removed from the environment once read.
SECRET_VARIABLES = ("YAAP_MASTER_KEY", "YAAP_MASTER_KEY_PREVIOUS")


class ConfigurationError(RuntimeError):
    """Raised when the application cannot start because of missing or invalid configuration."""


@dataclass(frozen=True)
class Settings:
    master_key: bytes
    data_dir: Path
    static_dir: Path | None
    # During a key rotation: the key YAAP ran with before; values are re-encrypted with master_key on start.
    previous_master_key: bytes | None = None
    # Where the master key came from: "environment" or "file".
    master_key_source: str = "environment"

    @property
    def database_url(self) -> str:
        return f"sqlite:///{(self.data_dir / 'yaap.db').as_posix()}"

    @property
    def workspace_dir(self) -> Path:
        """Project working folders: one folder per environment, one per project inside it."""
        return self.data_dir / "workspace"

    @property
    def blueprint_dir(self) -> Path:
        """Blueprint layers: global/ and one folder per environment."""
        return self.data_dir / "blueprint"

    @property
    def files_dir(self) -> Path:
        """Attachments: one folder per environment, one per attachment inside it."""
        return self.data_dir / "files"


def open_to_others(mode: int) -> bool:
    """Whether a file's permission bits give its group or other users any access."""
    return bool(mode & (stat.S_IRWXG | stat.S_IRWXO))


def read_key_file(path: str) -> bytes:
    try:
        key_file = Path(path)
        # Windows has no such bits (every file shows as readable by all); a container and Linux do.
        if os.name == "posix" and open_to_others(key_file.stat().st_mode):
            logger.warning(
                "YAAP_MASTER_KEY_FILE %s can be read by other users; make it readable by its owner only (chmod 600).",
                path,
            )
        return key_file.read_text(encoding="utf-8").strip().encode()
    except OSError as error:
        raise ConfigurationError(f"YAAP_MASTER_KEY_FILE names {path}, which cannot be read: {error}") from error


def forget_secret_variables() -> None:
    """Take the master keys out of the process environment once read, so nothing that copies the environment later
    (a subprocess, a library, a crash report) can pass them on. The kernel keeps the environment YAAP started with in
    /proc/<pid>/environ; that is guarded by marking the process not dumpable (app/core/process_hardening.py)."""
    for name in SECRET_VARIABLES:
        os.environ.pop(name, None)


def valid_key(key: bytes, help_text: str) -> bytes:
    try:
        Fernet(key)
    except (ValueError, TypeError) as error:
        raise ConfigurationError(help_text) from error
    return key


def load_settings() -> Settings:
    """The master key comes from YAAP_MASTER_KEY, or from the file YAAP_MASTER_KEY_FILE names when that is set."""
    key_file = os.environ.get("YAAP_MASTER_KEY_FILE", "").strip()
    master_key = read_key_file(key_file) if key_file else environment_value("YAAP_MASTER_KEY").strip().encode()
    previous_value = os.environ.get("YAAP_MASTER_KEY_PREVIOUS", "").strip().encode()
    forget_secret_variables()
    valid_key(master_key, MASTER_KEY_HELP)
    previous = valid_key(previous_value, PREVIOUS_KEY_HELP) if previous_value else None

    data_dir = Path(environment_value("YAAP_DATA_DIR", "data")).resolve()
    static_value = environment_value("YAAP_STATIC_DIR").strip()
    static_dir = Path(static_value).resolve() if static_value else None
    return Settings(
        master_key=master_key,
        data_dir=data_dir,
        static_dir=static_dir,
        previous_master_key=previous if previous != master_key else None,
        master_key_source="file" if key_file else "environment",
    )
