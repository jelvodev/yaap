"""Secret placeholders, stored in the vault.

The agent refers to a secret as `{{secret:NAME}}` in the command of `run_command` or `start_process`. YAAP turns the
placeholder into a reference to an environment variable (`${YAAP_SECRET_NAME}`) and passes the value in that
variable, so the value never appears in the command text, the approval card, the process list or the transcript. A
command with a placeholder always asks. Every tool output is masked: the value, and its URL-encoded and base64 forms,
become `[secret:NAME]`. Other tools refuse placeholders, so a value never lands in a file or a URL by accident.

A secret is a vault entry the agent may use (agent_use), at the global, environment or project level; the nearest
level wins by name.
"""

import base64
import re
from urllib.parse import quote, quote_plus

from sqlalchemy.orm import Session

from app.credentials.crypto import CredentialCipher, CredentialDecryptionError
from app.storage.models import VaultEntry
from app.vault.store import GENERIC, PLACEHOLDER_NAME, VaultError, find_entry, placeholder_entries, record_use, save_entry

NAME = PLACEHOLDER_NAME
PLACEHOLDER = re.compile(r"\{\{\s*secret:([A-Za-z0-9_]+)\s*\}\}")
PLACEHOLDER_TOOLS = frozenset({"run_command", "start_process"})
ENV_PREFIX = "YAAP_SECRET_"
MAX_VALUE_CHARS = 8000
# Shorter values are not masked: they would hide ordinary text.
MIN_MASK_CHARS = 4


class SecretError(Exception):
    """A secret action that cannot be done; the message is safe to show."""


def check_name(name: str) -> str:
    if not NAME.match(name):
        raise SecretError("Use capital letters, digits and underscores, starting with a letter, for example DB_PASSWORD.")
    return name


def find_secret(session: Session, environment_id: int | None, project_id: int | None, name: str) -> VaultEntry | None:
    """The secret with this name at exactly this level (no environment: global)."""
    return find_entry(session, environment_id, project_id, name, agent_use=True)


def save_secret(
    session: Session, cipher: CredentialCipher, environment_id: int | None, project_id: int | None, name: str, value: str
) -> VaultEntry:
    check_name(name)
    if not value:
        raise SecretError("The value is empty.")
    if len(value) > MAX_VALUE_CHARS:
        raise SecretError(f"Keep a secret under {MAX_VALUE_CHARS} characters.")
    try:
        entry = save_entry(
            session,
            cipher,
            environment_id=environment_id,
            project_id=project_id,
            name=name,
            kind=GENERIC,
            value=value,
            agent_use=True,
        )
    except VaultError as error:
        raise SecretError(str(error)) from error
    session.commit()
    return entry


def list_secrets(session: Session, environment_id: int, project_id: int | None) -> list[VaultEntry]:
    """The secrets a run sees: global, the environment's and, for a project, its own; the nearest wins by name."""
    return list(placeholder_entries(session, environment_id, project_id).values())


def resolve_secrets(
    session: Session, cipher: CredentialCipher, environment_id: int, project_id: int | None
) -> dict[str, str]:
    values: dict[str, str] = {}
    for name, entry in placeholder_entries(session, environment_id, project_id).items():
        try:
            values[name] = cipher.decrypt(entry.encrypted_value)
        except CredentialDecryptionError:
            continue
    return values


def record_secret_uses(
    session: Session, environment_id: int, project_id: int | None, names: list[str], used_by: str, conversation_id: int | None
) -> None:
    """Note in the vault's log that these placeholders were filled in, and commit."""
    entries = placeholder_entries(session, environment_id, project_id)
    for name in names:
        if name in entries:
            record_use(session, entries[name], used_by, conversation_id)
    session.commit()


def placeholder_names(text: str) -> list[str]:
    return sorted({match.group(1) for match in PLACEHOLDER.finditer(text)})


def has_placeholder(value: object) -> bool:
    if isinstance(value, str):
        return PLACEHOLDER.search(value) is not None
    if isinstance(value, dict):
        return any(has_placeholder(item) for item in value.values())
    if isinstance(value, list):
        return any(has_placeholder(item) for item in value)
    return False


def substitute(command: str, secrets: dict[str, str], powershell: bool = False) -> tuple[str, dict[str, str]]:
    """The command with each placeholder as a variable reference (${NAME}, or $env:NAME for PowerShell on a Windows
    target), and the variables to set."""
    missing = [name for name in placeholder_names(command) if name not in secrets]
    if missing:
        available = ", ".join(sorted(secrets)) or "none"
        raise SecretError(f"Unknown secret {', '.join(missing)}. Available: {available}.")
    variables: dict[str, str] = {}

    def replace(match: re.Match[str]) -> str:
        variable = f"{ENV_PREFIX}{match.group(1)}"
        variables[variable] = secrets[match.group(1)]
        return f"$env:{variable}" if powershell else "${" + variable + "}"

    return PLACEHOLDER.sub(replace, command), variables


def fill_in(text: str, secrets: dict[str, str]) -> str:
    """The text with each placeholder replaced by the secret's value itself, for configuration YAAP passes on
    (an MCP server's environment and headers); never for anything the agent writes."""
    missing = [name for name in placeholder_names(text) if name not in secrets]
    if missing:
        raise SecretError(f"Unknown secret {', '.join(missing)}.")
    return PLACEHOLDER.sub(lambda match: secrets[match.group(1)], text)


def masked_forms(value: str) -> set[str]:
    forms = {value, quote(value, safe=""), quote_plus(value)}
    encoded = base64.b64encode(value.encode("utf-8")).decode("ascii")
    forms.update({encoded, encoded.rstrip("=")})
    return {form for form in forms if len(form) >= MIN_MASK_CHARS}


def masked_names(text: str, secrets: dict[str, str]) -> list[str]:
    """The names, sorted, of the secrets whose value occurs in text in any form `mask` replaces."""
    if not secrets or not text:
        return []
    return sorted(name for name, value in secrets.items() if any(form in text for form in masked_forms(value)))


def mask(text: str, secrets: dict[str, str]) -> str:
    if not secrets or not text:
        return text
    replacements = sorted(
        ((form, name) for name, value in secrets.items() for form in masked_forms(value)),
        key=lambda pair: len(pair[0]),
        reverse=True,
    )
    for form, name in replacements:
        if form in text:
            text = text.replace(form, f"[secret:{name}]")
    return text
