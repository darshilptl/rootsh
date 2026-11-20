"""`rootsh daemon` and `rootsh approve` (design 07 §1, §4)."""

from __future__ import annotations

import signal
import sys
from pathlib import Path
from types import FrameType

from rootsh.daemon import client, load_model
from rootsh.daemon.server import Server
from rootsh.daemon.service import Service
from rootsh.storage.db import DatabaseKind, open_database
from rootsh.storage.migrations import migrate
from rootsh.storage.schema import MIGRATIONS


def _stop(_signum: int, _frame: FrameType | None) -> None:
    raise KeyboardInterrupt  # systemd stops with SIGTERM; leave through the cleanup below


def serve(socket_path: Path, db: Path, bundle: Path) -> int:
    signal.signal(signal.SIGTERM, _stop)
    model, error = load_model(bundle)
    if error is not None:  # still serve: every tool call is then blocked, with this reason
        print(f"rootsh daemon: {error}; blocking every tool call", file=sys.stderr)
    conn = open_database(db, DatabaseKind.RUNTIME, threads=True)
    migrate(conn, MIGRATIONS[DatabaseKind.RUNTIME])
    service = Service(conn, model, model_error=error, home=str(Path.home()))
    with Server(socket_path, service) as server:
        print(f"rootsh daemon: listening on {socket_path}", file=sys.stderr)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            socket_path.unlink(missing_ok=True)
            conn.close()
    return 0


def approve(socket_path: Path, decision_id: str, answer: str) -> int:
    try:
        reply = client.request(socket_path, {"op": "approve", "id": decision_id, "answer": answer})
    except (OSError, ValueError) as exc:
        print(f"rootsh approve: daemon unreachable ({exc})", file=sys.stderr)
        return 1
    if reply.get("ok") is not True:
        print(f"rootsh approve: refused: {reply.get('error')}", file=sys.stderr)
        return 1
    return 0
