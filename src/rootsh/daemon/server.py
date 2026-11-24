"""The Unix-socket front of the daemon: one JSON line in, one JSON line out."""

from __future__ import annotations

import json
import os
import socketserver
import stat
from dataclasses import replace
from pathlib import Path

from rootsh.daemon.peer import peer_cgroup, peer_of
from rootsh.daemon.service import Endpoint, Service

MAX_REQUEST_BYTES = 4 * 1024 * 1024  # hook payloads carry tool output (text cap 256k chars)
READ_TIMEOUT_S = 10


class _Handler(socketserver.StreamRequestHandler):
    server: Server
    timeout = READ_TIMEOUT_S

    def handle(self) -> None:
        peer = peer_of(self.connection)
        try:
            peer = replace(peer, cgroup=peer_cgroup(self.connection, peer.pid))
        except OSError as exc:
            peer = replace(peer, cgroup_error=f"{type(exc).__name__}: {exc}")
        line = self.rfile.readline(MAX_REQUEST_BYTES + 1)
        if len(line) > MAX_REQUEST_BYTES or not line.endswith(b"\n"):
            reply: dict[str, object] = {"error": "request too large or not one line"}
        else:
            try:
                request = json.loads(line)
            except ValueError:
                reply = {"error": "request is not JSON"}
            else:
                reply = self.server.service.handle(request, peer, self.server.endpoint)
        self.wfile.write(json.dumps(reply).encode() + b"\n")


class Server(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True

    def __init__(self, path: Path, service: Service, endpoint: Endpoint = Endpoint.ANY) -> None:
        self.service = service
        self.endpoint = endpoint
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if path.exists() or path.is_symlink():
            if not stat.S_ISSOCK(path.lstat().st_mode):
                raise FileExistsError(f"{path} exists and is not a socket")
            path.unlink()  # a stale socket from a previous run
        old = os.umask(0o177)  # owner-only socket; SO_PEERCRED checks the rest
        try:
            super().__init__(str(path), _Handler)
        finally:
            os.umask(old)
