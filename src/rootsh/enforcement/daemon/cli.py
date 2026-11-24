"""`rootsh daemon` and `rootsh approve` (design 07 §1, §4)."""

from __future__ import annotations

import signal
import sys
import threading
from pathlib import Path
from types import FrameType

from rootsh.enforcement.daemon import client, load_model
from rootsh.enforcement.daemon.server import Server
from rootsh.enforcement.daemon.service import Endpoint, Service
from rootsh.storage.db import DatabaseKind, open_database
from rootsh.storage.migrations import migrate
from rootsh.storage.schema import MIGRATIONS


def _stop(_signum: int, _frame: FrameType | None) -> None:
    raise KeyboardInterrupt  # systemd stops with SIGTERM; leave through the cleanup below


def serve(socket_path: Path, control_path: Path, db: Path, bundle: Path) -> int:
    """Serve the event socket (hooks) and the control socket (approvals,
    launch registration) from one process (design 09 §2)."""
    if socket_path.parent == control_path.parent:
        print("rootsh daemon: event and control sockets need separate directories", file=sys.stderr)
        return 2
    signal.signal(signal.SIGTERM, _stop)
    model, error = load_model(bundle)
    if error is not None:  # still serve: every tool call is then blocked, with this reason
        print(f"rootsh daemon: {error}; blocking every tool call", file=sys.stderr)
    conn = open_database(db, DatabaseKind.RUNTIME, threads=True)
    migrate(conn, MIGRATIONS[DatabaseKind.RUNTIME])
    service = Service(conn, model, model_error=error, home=str(Path.home()))
    with (
        Server(socket_path, service, Endpoint.EVENT) as events,
        Server(control_path, service, Endpoint.CONTROL) as control,
    ):
        print(f"rootsh daemon: events {socket_path}, control {control_path}", file=sys.stderr)
        threading.Thread(target=control.serve_forever, daemon=True).start()
        try:
            events.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            control.shutdown()
            socket_path.unlink(missing_ok=True)
            control_path.unlink(missing_ok=True)
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
