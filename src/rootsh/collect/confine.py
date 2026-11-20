"""Landlock confinement applied inside the sandbox, before the agent starts.

The sandbox shares the host's network namespace (the agents call their model
APIs), so without this a sandboxed process could reach every localhost
service: the author's own OpenCode server and Claude Code session ports, X11
through its abstract socket, CUPS (design 04 §5, audit). Landlock (ABI >= 6,
host has 8 [RUN]) closes that without root:

- TCP connect is allowed to port 443 only; TCP bind is not allowed at all,
  so nothing the agent starts can listen on the LAN;
- abstract unix sockets and signals are scoped to the sandbox.

Two kinds of extra local port may be granted: one for bind and connect
(OpenCode's own server, started inside the sandbox on a port the harness
picked free), and connect-only ports (the host key proxy, keyproxy.py).

Usage: python -m rootsh.collect.confine [--local-port N] [--connect-port N]... -- <command...>
Fails closed: if any step fails, the command is not run (exit 126).
"""

from __future__ import annotations

import ctypes
import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass

SYS_CREATE_RULESET, SYS_ADD_RULE, SYS_RESTRICT_SELF = 444, 445, 446
CREATE_RULESET_VERSION = 1
RULE_NET_PORT = 2
NET_BIND_TCP, NET_CONNECT_TCP = 1 << 0, 1 << 1
SCOPE_ABSTRACT_UNIX_SOCKET, SCOPE_SIGNAL = 1 << 0, 1 << 1
PR_SET_NO_NEW_PRIVS = 38
MIN_ABI = 6  # scoping arrived in ABI 6
ALLOWED_CONNECT_PORTS = (443,)
EXIT_REFUSED = 126


class _RulesetAttr(ctypes.Structure):
    _fields_ = (
        ("handled_access_fs", ctypes.c_uint64),
        ("handled_access_net", ctypes.c_uint64),
        ("scoped", ctypes.c_uint64),
    )


class _NetPortAttr(ctypes.Structure):
    _fields_ = (("allowed_access", ctypes.c_uint64), ("port", ctypes.c_uint64))


class ConfineError(OSError):
    """Landlock could not be applied; the agent must not start."""


def _libc() -> ctypes.CDLL:
    libc = ctypes.CDLL(None, use_errno=True)
    libc.syscall.restype = ctypes.c_long
    return libc


def _check(result: int, what: str) -> int:
    if result < 0:
        errno = ctypes.get_errno()
        raise ConfineError(errno, f"{what}: {os.strerror(errno)}")
    return result


def abi_version() -> int:
    libc = _libc()
    return _check(
        libc.syscall(
            SYS_CREATE_RULESET, None, ctypes.c_size_t(0), ctypes.c_uint32(CREATE_RULESET_VERSION)
        ),
        "landlock ABI query",
    )


def confine(
    connect_ports: Sequence[int] = ALLOWED_CONNECT_PORTS, local_port: int | None = None
) -> None:
    """Restrict this process and every future child. Irreversible."""
    abi = abi_version()
    if abi < MIN_ABI:
        raise ConfineError(0, f"Landlock ABI {abi} < {MIN_ABI}")
    libc = _libc()
    attr = _RulesetAttr(
        0, NET_BIND_TCP | NET_CONNECT_TCP, SCOPE_ABSTRACT_UNIX_SOCKET | SCOPE_SIGNAL
    )
    ruleset = _check(
        libc.syscall(
            SYS_CREATE_RULESET, ctypes.byref(attr), ctypes.c_size_t(ctypes.sizeof(attr)), 0
        ),
        "create ruleset",
    )
    try:
        rules = [(NET_CONNECT_TCP, port) for port in connect_ports]
        if local_port is not None:
            rules.append((NET_BIND_TCP | NET_CONNECT_TCP, local_port))
        for access, port in rules:
            rule = _NetPortAttr(access, port)
            _check(
                libc.syscall(SYS_ADD_RULE, ruleset, RULE_NET_PORT, ctypes.byref(rule), 0),
                f"allow port {port}",
            )
        _check(libc.prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0), "no_new_privs")
        _check(libc.syscall(SYS_RESTRICT_SELF, ruleset, 0), "restrict self")
    finally:
        os.close(ruleset)


@dataclass(frozen=True, slots=True)
class Grant:
    """Ports beyond 443 for one run: OpenCode's own server (bind + connect)
    and the host key proxy (connect only)."""

    local_port: int | None = None
    extra_connect: tuple[int, ...] = ()


def _port(text: str) -> int | None:
    return int(text) if text.isdigit() and 1024 <= int(text) <= 65535 else None


def parse(argv: Sequence[str]) -> tuple[Grant, list[str]] | None:
    """(grant, command), or None for a malformed command line."""
    args = list(argv)
    local: int | None = None
    extra: list[int] = []
    while args and args[0] in ("--local-port", "--connect-port"):
        port = _port(args[1]) if len(args) > 1 else None
        if port is None or (args[0] == "--local-port" and local is not None):
            return None
        if args[0] == "--local-port":
            local = port
        else:
            extra.append(port)
        args = args[2:]
    if len(args) < 2 or args[0] != "--":
        return None
    return Grant(local, tuple(extra)), args[1:]


def main(argv: Sequence[str]) -> int:
    parsed = parse(argv)
    if parsed is None:
        print(
            "usage: python -m rootsh.collect.confine [--local-port N] [--connect-port N]... "
            "-- <command...>",
            file=sys.stderr,
        )
        return EXIT_REFUSED
    grant, command = parsed
    try:
        confine((*ALLOWED_CONNECT_PORTS, *grant.extra_connect), grant.local_port)
    except ConfineError as exc:
        print(f"rootsh confine: refusing to start: {exc}", file=sys.stderr)
        return EXIT_REFUSED
    try:
        os.execvp(command[0], command)  # noqa: S606 - the sandboxed agent command
    except OSError as exc:
        print(f"rootsh confine: cannot start {command[0]}: {exc}", file=sys.stderr)
    return EXIT_REFUSED


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
