"""A machine over SSH as a target: files over SFTP, commands over SSH, with asyncssh.

The same interface as the container target (base.py), so the tools work the same. Files stay inside the working
folder (`check`), never into `.git`. Commands run with their script on standard input, so nothing needs quoting and
no value appears on a command line: bash -s on Linux and macOS; PowerShell 7 (pwsh), else Windows PowerShell, with
-NoProfile -NonInteractive on Windows, which reads the whole script from standard input. The host key is pinned once
the user confirmed it; a changed host key blocks the target.

Shell sessions keep their state in the target's temp folder: for bash the same files as in the
container, for PowerShell a script that sets the changed `$env:` variables and the folder (session_script_for).

Paths: the working folder is given as the target shows it (C:\\Users\\alex\\site on Windows); SFTP gets the
OpenSSH form (/C:/Users/alex/site).
"""

import asyncio
import base64
import codecs
import posixpath
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any, Literal

import asyncssh

from app.targets.base import CapturedResult, CommandResult, FileEntry, OutputTooLargeError, TargetError, read_outputs
from app.targets.container import ANSI, MAX_SEARCH_FILE_BYTES, Collected
from app.targets.sessions import PRUNE_MINUTES, STATE_PREFIX, ShellState, bash_session

CONNECT_SECONDS = 15
WINDOWS = "windows"
# PowerShell reads the whole script from standard input and runs it as one, so blocks over several lines work.
BOOTSTRAP = "[Console]::InputEncoding = [Text.Encoding]::UTF8; Invoke-Expression ([Console]::In.ReadToEnd())"


def shell_command(shell: str) -> str:
    if shell in ("pwsh", "powershell"):
        encoded = base64.b64encode(BOOTSTRAP.encode("utf-16-le")).decode("ascii")
        return f"{shell} -NoProfile -NonInteractive -EncodedCommand {encoded}"
    return "bash -s"


@dataclass
class SshSettings:
    """What a target needs to connect; built from the stored target and its key in the vault."""

    host: str
    port: int
    user: str
    private_key: str
    # The confirmed host key (OpenSSH format); None before the user confirmed one.
    host_key: str | None
    system: str = "linux"
    shell: str = "bash"
    label: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


class HostKeyUnconfirmed(TargetError):
    """The host answered with a key the user has not confirmed yet."""

    def __init__(self, key: str, fingerprint: str) -> None:
        super().__init__(f"Confirm the host key of this computer first ({fingerprint}).")
        self.key = key
        self.fingerprint = fingerprint


def fingerprint(public_key: str) -> str:
    return asyncssh.import_public_key(public_key).get_fingerprint("sha256")


def new_key_pair(comment: str) -> tuple[str, str]:
    """A new ed25519 key: the private key (OpenSSH format) and the public key line."""
    key = asyncssh.generate_private_key("ssh-ed25519", comment=comment)
    return key.export_private_key().decode(), key.export_public_key().decode().strip()


async def connect(settings: SshSettings, accept_unknown: bool = False) -> asyncssh.SSHClientConnection:
    """Connect with the target's key. Without a confirmed host key this raises HostKeyUnconfirmed with the key."""
    options: dict[str, Any] = {
        "host": settings.host,
        "port": settings.port,
        "username": settings.user,
        "client_keys": [asyncssh.import_private_key(settings.private_key)],
        "connect_timeout": CONNECT_SECONDS,
        "agent_path": None,
        "config": [],
    }
    if settings.host_key:
        options["known_hosts"] = ([asyncssh.import_public_key(settings.host_key)], [], [])
    else:
        options["known_hosts"] = None
    try:
        # A sandbox is reached through the helper's relay: a socket it already opened.
        if "open_socket" in settings.extra:
            options["sock"] = await settings.extra["open_socket"]()
        connection = await asyncssh.connect(**options)
    except asyncssh.HostKeyNotVerifiable as error:
        raise TargetError(
            "The host key of this computer changed since it was confirmed. YAAP does not connect until you check "
            "why and confirm the new key in Settings › Targets."
        ) from error
    except (OSError, asyncssh.Error) as error:
        raise TargetError(f"Cannot connect to {settings.host}:{settings.port}: {error}") from error
    if not settings.host_key and not accept_unknown:
        server_key = connection.get_server_host_key()
        connection.close()
        assert server_key is not None
        line = server_key.export_public_key().decode().strip()
        raise HostKeyUnconfirmed(line, server_key.get_fingerprint("sha256"))
    return connection


def sftp_path(system: str, path: str) -> str:
    """The SFTP form of a path: forward slashes, and /C:/... for a Windows drive."""
    path = path.replace("\\", "/")
    if system == WINDOWS and re.match(r"^[A-Za-z]:", path):
        path = "/" + path
    return path


POWERSHELL_QUOTES = re.compile("['\u2018\u2019\u201a\u201b]")


def shell_quote(system: str, value: str) -> str:
    if system == WINDOWS:
        # PowerShell also ends a single-quoted string at the typographic single quotes; doubling escapes each.
        return "'" + POWERSHELL_QUOTES.sub(lambda found: found.group(0) * 2, value) + "'"
    return "'" + value.replace("'", "'\\''") + "'"


POWERSHELL_START = [
    "$ProgressPreference = 'SilentlyContinue'",
    "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8",
]


def script_for(
    settings: SshSettings, root: str, command: str, env: dict[str, str], session: ShellState | None = None
) -> str:
    """The script sent on standard input: variables, the working folder, then the command; in a session, with its
    state restored first and saved again after it."""
    if session is not None:
        return session_script_for(settings, root, command, env, session)
    if settings.system == WINDOWS:
        lines = [*POWERSHELL_START, *powershell_variables(env)]
        lines += [f"Set-Location -LiteralPath {shell_quote(WINDOWS, root)}", command]
        lines.append("if ($LASTEXITCODE) { exit $LASTEXITCODE }")
        return "\r\n".join(lines) + "\r\n"
    lines = [*bash_variables(env), f"cd {shell_quote('linux', root)} || exit 1", command]
    return "\n".join(lines) + "\n"


def bash_variables(env: dict[str, str]) -> list[str]:
    return [f"export {name}={shell_quote('linux', value)}" for name, value in env.items()]


def powershell_variables(env: dict[str, str]) -> list[str]:
    return [f"$env:{name} = {shell_quote(WINDOWS, value)}" for name, value in env.items()]


# A PowerShell session's state is a script in the temp folder that sets the variables that differ from a fresh
# connection (or removes the ones it lacks) and the folder; restoring runs it. Secret variables are left out.
POWERSHELL_RESTORE = """$__yaapTemp = [IO.Path]::GetTempPath()
$__yaapState = Join-Path $__yaapTemp 'STATE_FILE'
Get-ChildItem -LiteralPath $__yaapTemp -Filter 'PREFIX*.ps1' -ErrorAction SilentlyContinue |
  Where-Object { $_.LastWriteTime -lt (Get-Date).AddMinutes(-PRUNE_MINUTES) } |
  Remove-Item -Force -ErrorAction SilentlyContinue
$__yaapBase = @{}
foreach ($__yaapItem in Get-ChildItem Env:) { $__yaapBase[$__yaapItem.Name] = $__yaapItem.Value }
Set-Location -LiteralPath ROOT
if (Test-Path -LiteralPath $__yaapState) { try { Invoke-Expression ([IO.File]::ReadAllText($__yaapState)) } catch { } }"""
POWERSHELL_SAVE = """function __yaapQuote([string] $value) { "'" + ($value -replace "['\\u2018\\u2019\\u201A\\u201B]", '$0$0') + "'" }
$__yaapLines = [Collections.Generic.List[string]]::new()
foreach ($__yaapItem in Get-ChildItem Env:) {
  if ($__yaapItem.Name -like 'YAAP_SECRET_*') { continue }
  if (-not $__yaapBase.ContainsKey($__yaapItem.Name) -or $__yaapBase[$__yaapItem.Name] -cne $__yaapItem.Value) {
    $__yaapLines.Add('[Environment]::SetEnvironmentVariable(' + (__yaapQuote $__yaapItem.Name) + ', ' + (__yaapQuote $__yaapItem.Value) + ')')
  }
}
foreach ($__yaapName in @($__yaapBase.Keys)) {
  if ($null -eq [Environment]::GetEnvironmentVariable($__yaapName)) {
    $__yaapLines.Add('[Environment]::SetEnvironmentVariable(' + (__yaapQuote $__yaapName) + ', $null)')
  }
}
$__yaapLines.Add('Set-Location -LiteralPath ' + (__yaapQuote (Get-Location).Path) + ' -ErrorAction SilentlyContinue')
[IO.File]::WriteAllText($__yaapState, ($__yaapLines -join "`r`n"), [Text.UTF8Encoding]::new($false))"""


def session_script_for(
    settings: SshSettings, root: str, command: str, env: dict[str, str], session: ShellState
) -> str:
    """A command in a shell session on the target: the state is restored, the command's secret
    variables are set, and the state is saved again when the command ends, also through `exit`."""
    if settings.system == WINDOWS:
        restore = (
            POWERSHELL_RESTORE.replace("STATE_FILE", f"{STATE_PREFIX}{session.token}.ps1")
            .replace("PREFIX", STATE_PREFIX)
            .replace("PRUNE_MINUTES", str(PRUNE_MINUTES))
            .replace("ROOT", shell_quote(WINDOWS, root))
        )
        lines = [*POWERSHELL_START, restore, *powershell_variables(env)]
        if session.save:
            lines += ["try {", command, "} finally {", POWERSHELL_SAVE, "}"]
        else:
            lines.append(command)
        lines.append("if ($LASTEXITCODE) { exit $LASTEXITCODE }")
        return "\r\n".join(lines) + "\r\n"
    temp = '"${TMPDIR:-/tmp}"'
    prune = f"find {temp} -maxdepth 1 -name '{STATE_PREFIX}*' -mmin +{PRUNE_MINUTES} -delete 2>/dev/null\n"
    setup = f"cd {shell_quote('linux', root)} || exit 1\n{prune}"
    variables = "".join(line + "\n" for line in bash_variables(env))
    state = f'"${{TMPDIR:-/tmp}}/{STATE_PREFIX}{session.token}"'
    return setup + bash_session(state, command, session.save, before_command=variables)


class SshConnections:
    """One open connection per target, reused while it works and opened again when it dropped."""

    def __init__(self) -> None:
        self.open: dict[str, asyncssh.SSHClientConnection] = {}
        self.lock = asyncio.Lock()

    async def get(self, key: str, settings: SshSettings) -> asyncssh.SSHClientConnection:
        async with self.lock:
            connection = self.open.get(key)
            if connection is not None and not connection.is_closed():
                return connection
            connection = await connect(settings)
            self.open[key] = connection
            return connection

    def close(self, key: str) -> None:
        connection = self.open.pop(key, None)
        if connection is not None:
            connection.close()


CONNECTIONS = SshConnections()


class SftpFiles:
    def __init__(self, target: "SshTarget", root: str) -> None:
        self.target = target
        self.root = root.replace("\\", "/").rstrip("/") or "/"

    def check(self, path: str, for_write: bool = False) -> str:
        joined = posixpath.normpath(posixpath.join("/__root__", (path or ".").replace("\\", "/")))
        if joined != "/__root__" and not joined.startswith("/__root__/"):
            raise TargetError(f"{path} is outside the working folder. Use a path relative to it.")
        relative = joined[len("/__root__/") :] if joined != "/__root__" else "."
        if for_write and (relative == "." or ".git" in relative.split("/")):
            raise TargetError("Writing into .git or replacing the working folder itself is not allowed.")
        return relative

    def remote(self, relative: str) -> str:
        full = self.root if relative == "." else f"{self.root}/{relative}"
        return sftp_path(self.target.settings.system, full)

    async def sftp(self) -> asyncssh.SFTPClient:
        connection = await self.target.connection()
        return await connection.start_sftp_client()

    async def stat(self, path: str) -> FileEntry | None:
        relative = self.check(path)
        async with await self.sftp() as sftp:
            try:
                attributes = await sftp.stat(self.remote(relative))
            except asyncssh.SFTPNoSuchFile:
                return None
            except asyncssh.SFTPError as error:
                raise TargetError(f"{path}: {error.reason}") from error
        return sftp_entry(relative, attributes)

    async def read_bytes(self, path: str) -> bytes:
        relative = self.check(path)
        async with await self.sftp() as sftp:
            try:
                async with sftp.open(self.remote(relative), "rb") as handle:
                    return await handle.read()
            except asyncssh.SFTPError as error:
                raise TargetError(f"{path}: {error.reason}") from error

    async def write_bytes(self, path: str, data: bytes) -> None:
        relative = self.check(path, for_write=True)
        async with await self.sftp() as sftp:
            try:
                parent = posixpath.dirname(relative)
                if parent:
                    await sftp.makedirs(self.remote(parent), exist_ok=True)
                async with sftp.open(self.remote(relative), "wb") as handle:
                    await handle.write(data)
            except asyncssh.SFTPError as error:
                raise TargetError(f"{path}: {error.reason}") from error

    async def remove(self, path: str) -> None:
        relative = self.check(path, for_write=True)
        async with await self.sftp() as sftp:
            try:
                await sftp.remove(self.remote(relative))
            except asyncssh.SFTPNoSuchFile:
                return
            except asyncssh.SFTPError as error:
                raise TargetError(f"{path}: {error.reason}") from error

    async def walk(self, path: str, recursive: bool, skip: set[str], limit: int) -> list[FileEntry]:
        start = self.check(path)
        entries: list[FileEntry] = []
        async with await self.sftp() as sftp:
            pending = [start]
            while pending and len(entries) <= limit:
                current = pending.pop(0)
                try:
                    names = sorted(await sftp.readdir(self.remote(current)), key=lambda item: item.filename)
                except asyncssh.SFTPError as error:
                    raise TargetError(f"{path}: {error.reason}") from error
                folders: list[FileEntry] = []
                files: list[FileEntry] = []
                for item in names:
                    if item.filename in (".", ".."):
                        continue
                    relative = item.filename if current == "." else f"{current}/{item.filename}"
                    if item.attrs.type == asyncssh.FILEXFER_TYPE_DIRECTORY:
                        if item.filename not in skip:
                            folders.append(sftp_entry(relative, item.attrs))
                    else:
                        files.append(sftp_entry(relative, item.attrs))
                entries += folders + files
                if not recursive:
                    break
                pending += [folder.path for folder in folders]
        return entries

    async def search(self, pattern: str, path: str, glob: str, limit: int, skip: set[str] | None = None) -> list[str]:
        compiled = re.compile(pattern)
        entries = await self.walk(path, True, skip or set(), 5000)
        matches: list[str] = []
        async with await self.sftp() as sftp:
            for entry in entries:
                if entry.is_dir or not posixpath.basename(entry.path) or not fnmatch_path(entry.path, glob):
                    continue
                try:
                    attributes = await sftp.stat(self.remote(entry.path))
                    if (attributes.size or 0) > MAX_SEARCH_FILE_BYTES:
                        continue
                    async with sftp.open(self.remote(entry.path), "rb") as handle:
                        text = (await handle.read()).decode("utf-8")
                except (asyncssh.SFTPError, UnicodeDecodeError):
                    continue
                for number, line in enumerate(text.splitlines(), 1):
                    if compiled.search(line):
                        matches.append(f"{entry.path}:{number}: {line.strip()[:300]}")
                        if len(matches) >= limit:
                            return matches
        return matches


def sftp_entry(relative: str, attributes: asyncssh.SFTPAttrs) -> FileEntry:
    is_dir = attributes.type == asyncssh.FILEXFER_TYPE_DIRECTORY
    modified = float(attributes.mtime) if attributes.mtime is not None else None
    return FileEntry(relative, is_dir, 0 if is_dir else int(attributes.size or 0), modified)


def fnmatch_path(path: str, glob: str) -> bool:
    return PurePosixPath(path).match(glob or "*")


class RemoteProcess:
    """A command over SSH that keeps running, shaped like asyncio.subprocess.Process for the process manager."""

    def __init__(self, process: asyncssh.SSHClientProcess, system: str) -> None:
        self.process = process
        self.system = system
        self.stdout = process.stdout
        self.pid = -1
        self.returncode: int | None = None

    async def wait(self) -> int:
        completed = await self.process.wait(check=False)
        self.returncode = completed.exit_status if completed.exit_status is not None else -1
        return self.returncode

    def kill(self) -> None:
        # Closing the channel ends the session; sshd ends what it started (a job object on Windows).
        self.process.close()

    terminate = kill


class SshExecutor:
    def __init__(self, target: "SshTarget", root: str) -> None:
        self.target = target
        self.root = root

    async def start(
        self,
        command: str,
        env: dict[str, str],
        session: ShellState | None = None,
        separate_stderr: bool = False,
    ) -> asyncssh.SSHClientProcess:
        settings = self.target.settings
        connection = await self.target.connection()
        process = await connection.create_process(
            shell_command(settings.shell), stderr=asyncssh.PIPE if separate_stderr else asyncssh.STDOUT, encoding=None
        )
        process.stdin.write(script_for(settings, self.root, command, env, session).encode("utf-8"))
        process.stdin.write_eof()
        return process

    async def run(
        self,
        command: str,
        env: dict[str, str],
        timeout: float,
        session: ShellState | None = None,
        silence: float | None = None,
        on_output: Callable[[str], None] | None = None,
    ) -> CommandResult:
        process = await self.start(command, remote_env(env), session)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        collected = Collected()
        stopped: Literal["total", "silence"] | None = None
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                stopped = "total"
                break
            wait = min(remaining, silence) if silence else remaining
            try:
                chunk = await asyncio.wait_for(process.stdout.read(65536), timeout=wait)
            except TimeoutError:
                stopped = "silence" if silence and silence < remaining else "total"
                break
            if not chunk:
                break
            text = decoder.decode(chunk)
            if text:
                collected.add(text)
                if on_output is not None:
                    on_output(ANSI.sub("", text))
        collected.add(decoder.decode(b"", final=True))
        if stopped is not None:
            process.close()
            return CommandResult(None, collected.text(), timed_out=True, stopped=stopped)
        completed = await process.wait(check=False)
        return CommandResult(completed.exit_status, collected.text())

    async def capture(self, command: str, timeout: float) -> CapturedResult:
        process = await self.start(command, {}, separate_stderr=True)
        try:
            async with asyncio.timeout(timeout):
                stdout, stderr = await read_outputs(process.stdout, process.stderr)
                completed = await process.wait(check=False)
        except (TimeoutError, OutputTooLargeError) as error:
            process.close()
            too_large = isinstance(error, OutputTooLargeError)
            return CapturedResult(None, b"", b"", timed_out=not too_large, too_large=too_large)
        return CapturedResult(completed.exit_status, stdout, stderr)

    async def spawn(self, command: str, env: dict[str, str], session: ShellState | None = None) -> Any:
        process = await self.start(command, remote_env(env), session)
        return RemoteProcess(process, self.target.settings.system)


def remote_env(env: dict[str, str]) -> dict[str, str]:
    """Only YAAP's own variables go to the target (secrets for placeholders); the container's stay here."""
    return {name: value for name, value in env.items() if name.startswith("YAAP_SECRET_")}


class SshTarget:
    kind = "ssh"

    def __init__(self, key: str, settings: SshSettings) -> None:
        self.key = key
        self.settings = settings

    async def connection(self) -> asyncssh.SSHClientConnection:
        return await CONNECTIONS.get(self.key, self.settings)

    def note_use(self) -> None:
        """A use without a new connection; a sandbox records it for its idle stop, a computer has nothing to do."""

    def is_open(self) -> bool:
        connection = CONNECTIONS.open.get(self.key)
        return connection is not None and not connection.is_closed()

    def files(self, root: str) -> SftpFiles:
        return SftpFiles(self, root)

    def executor(self, root: str) -> SshExecutor:
        return SshExecutor(self, root)


async def probe(settings: SshSettings) -> dict[str, str]:
    """Detect the system and, on Windows, the shell and whether the user is an administrator."""
    connection = await connect(settings)
    try:
        result = await connection.run("uname -s", check=False, timeout=CONNECT_SECONDS)
        name = str(result.stdout or "").strip().lower()
        if result.exit_status == 0 and name:
            system = "macos" if name == "darwin" else "linux"
            whoami = await connection.run("id -u", check=False, timeout=CONNECT_SECONDS)
            rights = "administrator" if str(whoami.stdout or "").strip() == "0" else "standard"
            return {"system": system, "shell": "bash", "rights": rights}
        found = await connection.run("where pwsh", check=False, timeout=CONNECT_SECONDS)
        shell = "pwsh" if found.exit_status == 0 else "powershell"
        groups = await connection.run(
            f"{shell} -NoProfile -NonInteractive -Command \"([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)\"",
            check=False,
            timeout=CONNECT_SECONDS,
        )
        rights = "administrator" if "true" in str(groups.stdout or "").lower() else "standard"
        return {"system": WINDOWS, "shell": shell, "rights": rights}
    finally:
        connection.close()
