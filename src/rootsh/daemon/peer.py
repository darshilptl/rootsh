"""Who is on the other end of the socket (docs/design/07-daemon.md §4)."""

from __future__ import annotations

import os
import re
import select
import socket
import struct
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

AGENT_SCOPE_PREFIX = "rootsh-agent-"
SO_PEERPIDFD = 77  # Linux >= 6.5; not in Python 3.12's socket module
_LAUNCH_UNIT = re.compile(r"rootsh-agent-([0-9a-f]{32})\.scope")
_UCRED = struct.Struct("3i")


@dataclass(frozen=True, slots=True)
class Peer:
    pid: int
    uid: int
    gid: int
    # The server fills these from a pidfd-pinned read (peer_cgroup); when
    # both are None (tests, callers without a socket) cgroup_of(pid) is used.
    cgroup: str | None = None
    cgroup_error: str | None = None


def peer_of(conn: socket.socket) -> Peer:
    """The kernel's record of the connecting process (SO_PEERCRED)."""
    raw = conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, _UCRED.size)
    pid, uid, gid = _UCRED.unpack(raw)
    return Peer(pid, uid, gid)


def read_cgroup(pid: int) -> str:
    return Path(f"/proc/{pid}/cgroup").read_text(encoding="utf-8")


def in_agent_scope(cgroup_file: str) -> bool:
    """True when any cgroup path component is an agent scope unit."""
    for line in cgroup_file.splitlines():
        path = line.split(":", 2)[-1]
        if any(part.startswith(AGENT_SCOPE_PREFIX) for part in path.split("/")):
            return True
    return False


def cgroup_text(peer: Peer, cgroup_of: Callable[[int], str] = read_cgroup) -> str:
    """The caller's cgroup file; raises OSError if it could not be pinned."""
    if peer.cgroup_error is not None:
        raise OSError(peer.cgroup_error)
    return peer.cgroup if peer.cgroup is not None else cgroup_of(peer.pid)


def approval_refusal(
    peer: Peer, own_uid: int, cgroup_of: Callable[[int], str] = read_cgroup
) -> str | None:
    """Why `peer` may not approve an ask, or None if it may."""
    if peer.uid != own_uid:
        return f"approver uid {peer.uid} is not the daemon's"
    try:
        cgroup = cgroup_text(peer, cgroup_of)
    except OSError as exc:
        return f"approver cgroup unreadable ({type(exc).__name__})"
    if not cgroup.strip():
        return "approver cgroup unreadable (empty)"
    if in_agent_scope(cgroup):
        return "approvals from inside an agent's scope are refused"
    return None


def launch_ids(cgroup_file: str) -> set[str]:
    """Launch ids named by `rootsh-agent-<id>.scope` components."""
    found: set[str] = set()
    for line in cgroup_file.splitlines():
        for part in line.split(":", 2)[-1].split("/"):
            match = _LAUNCH_UNIT.fullmatch(part)
            if match:
                found.add(match.group(1))
    return found


def peer_cgroup(
    conn: socket.socket, pid: int, cgroup_of: Callable[[int], str] = read_cgroup
) -> str:
    """The peer's cgroup file, read while its pid is pinned by a pidfd so a
    recycled pid cannot stand in for an exited caller (design 09 §3).
    Raises OSError when the caller is gone or cannot be pinned."""
    raw = conn.getsockopt(socket.SOL_SOCKET, SO_PEERPIDFD, 4)
    pidfd = struct.unpack("i", raw)[0]
    try:
        text = cgroup_of(pid)
        readable, _, _ = select.select([pidfd], [], [], 0)
        if readable:  # a pidfd polls readable once its process has exited
            raise ProcessLookupError(f"caller pid {pid} exited before it could be identified")
    finally:
        os.close(pidfd)
    return text
