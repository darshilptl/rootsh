"""Who is on the other end of the socket (docs/design/07-daemon.md §4)."""

from __future__ import annotations

import socket
import struct
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

AGENT_SCOPE_PREFIX = "rootsh-agent-"
_UCRED = struct.Struct("3i")


@dataclass(frozen=True, slots=True)
class Peer:
    pid: int
    uid: int
    gid: int


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


def approval_refusal(
    peer: Peer, own_uid: int, cgroup_of: Callable[[int], str] = read_cgroup
) -> str | None:
    """Why `peer` may not approve an ask, or None if it may."""
    if peer.uid != own_uid:
        return f"approver uid {peer.uid} is not the daemon's"
    try:
        cgroup = cgroup_of(peer.pid)
    except OSError as exc:
        return f"approver cgroup unreadable ({type(exc).__name__})"
    if not cgroup.strip():
        return "approver cgroup unreadable (empty)"
    if in_agent_scope(cgroup):
        return "approvals from inside an agent's scope are refused"
    return None
