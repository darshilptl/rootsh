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

Usage: python -m rootsh.research.collect.confine [--local-port N]
       [--connect-port N]... -- <command...>
Fails closed: if any step fails, the command is not run (exit 126). The
Landlock calls are `rootsh.enforcement.kernel.landlock` with filesystem rules
off (merged in v0.12.0; this module had its own copy before).
"""

from __future__ import annotations

import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass

from rootsh.enforcement.kernel.landlock import LandlockError, Rules, abi_version, apply

ALLOWED_CONNECT_PORTS = (443,)
EXIT_REFUSED = 126
__all__ = ["abi_version", "confine", "main", "parse"]


def confine(
    connect_ports: Sequence[int] = ALLOWED_CONNECT_PORTS, local_port: int | None = None
) -> None:
    """Restrict this process and every future child. Irreversible."""
    bind = () if local_port is None else (local_port,)
    apply(Rules(connect_ports=tuple(connect_ports), bind_ports=bind, filesystem=False))


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
            "usage: python -m rootsh.research.collect.confine [--local-port N] "
            "[--connect-port N]... -- <command...>",
            file=sys.stderr,
        )
        return EXIT_REFUSED
    grant, command = parsed
    try:
        confine((*ALLOWED_CONNECT_PORTS, *grant.extra_connect), grant.local_port)
    except LandlockError as exc:
        print(f"rootsh confine: refusing to start: {exc}", file=sys.stderr)
        return EXIT_REFUSED
    try:
        os.execvp(command[0], command)  # noqa: S606 - the sandboxed agent command
    except OSError as exc:
        print(f"rootsh confine: cannot start {command[0]}: {exc}", file=sys.stderr)
    return EXIT_REFUSED


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
