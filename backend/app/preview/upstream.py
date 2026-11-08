"""Reaching the dev server of a process: where the process runs decides the way.

- In YAAP's own process (no runner, local development): a TCP connection to localhost.
- In the environment's runner or a project's sandbox: a direct-tcpip channel over the SSH connection YAAP already
  has to it, to localhost inside that container. The sandbox image allows local forwarding to localhost only
  (docker/sandbox/sshd.conf), so this channel cannot reach anything else.
- On a computer over SSH (a target): the same channel to localhost on that computer; when its sshd refuses
  forwarding, a TCP connection to the address the target is reached at.
"""

import asyncio
import logging
from typing import Protocol

import asyncssh

from app.targets.base import TargetError
from app.targets.ssh import SshTarget

logger = logging.getLogger(__name__)

DEV_SERVER_HOST = "localhost"
LOOPBACK = ("127.0.0.1", "::1")
CONNECT_SECONDS = 10
SSH_TARGET = "ssh"


class ByteReader(Protocol):
    async def read(self, n: int = -1) -> bytes: ...

    async def readuntil(self, separator: bytes = b"\n") -> bytes: ...


class ByteWriter(Protocol):
    def write(self, data: bytes) -> None: ...

    async def drain(self) -> None: ...

    def close(self) -> None: ...


class UpstreamError(Exception):
    """The dev server cannot be reached; the browser gets 502 with this message."""


def dev_server_authority(port: int) -> str:
    """The Host the dev server sees: its own address, as when the browser opened it directly."""
    return f"{DEV_SERVER_HOST}:{port}"


async def open_local(host: str, port: int) -> tuple[ByteReader, ByteWriter]:
    try:
        return await asyncio.wait_for(asyncio.open_connection(host, port), timeout=CONNECT_SECONDS)
    except (OSError, TimeoutError) as error:
        raise UpstreamError(f"Nothing answers on port {port}: {error}") from error


async def open_over_ssh(target: SshTarget, port: int) -> tuple[ByteReader, ByteWriter]:
    try:
        connection = await target.connection()
    except TargetError as error:
        raise UpstreamError(str(error)) from error
    try:
        reader, writer = await asyncio.wait_for(
            connection.open_connection(DEV_SERVER_HOST, port, encoding=None), timeout=CONNECT_SECONDS
        )
    except asyncssh.ChannelOpenError as error:
        if target.kind == SSH_TARGET:
            # A computer whose sshd forbids forwarding: the dev server may still listen on its address.
            logger.info("Forwarding refused by %s (%s); connecting to its address.", target.settings.host, error)
            return await open_local(target.settings.host, port)
        raise UpstreamError(f"Nothing answers on port {port} where the process runs: {error.reason}") from error
    except (OSError, asyncssh.Error, TimeoutError) as error:
        raise UpstreamError(f"The connection to port {port} failed: {error}") from error
    return reader, writer


async def open_loopback(port: int) -> tuple[ByteReader, ByteWriter]:
    """IPv4 first, then IPv6: a dev server listens on one of them, and trying a closed one first costs seconds on
    some systems."""
    try:
        return await open_local(LOOPBACK[0], port)
    except UpstreamError:
        return await open_local(LOOPBACK[1], port)


async def open_upstream(target: SshTarget | None, port: int) -> tuple[ByteReader, ByteWriter]:
    """A byte stream to the dev server on `port` where a process with this target runs."""
    if target is None:
        return await open_loopback(port)
    return await open_over_ssh(target, port)
