"""Fail-closed client for hook commands (design 07 §6).

`decide()` never raises and never returns anything but a well-formed
decision: daemon down, timeout, or a malformed reply all become a block.
"""

from __future__ import annotations

import json
import socket
from pathlib import Path
from typing import Any

from rootsh.notify import ASK_TIMEOUT_S

TIMEOUT_S = 5.0
MAX_REPLY_BYTES = 1024 * 1024
DECISIONS = frozenset({"allow", "ask", "block"})


def request(path: Path, body: dict[str, Any], timeout_s: float = TIMEOUT_S) -> dict[str, Any]:
    """Send one request; raise OSError or ValueError on any transport problem."""
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout_s)
        sock.connect(str(path))
        sock.sendall(json.dumps(body).encode() + b"\n")
        data = b""
        while not data.endswith(b"\n"):
            chunk = sock.recv(65536)
            if not chunk:
                break
            data += chunk
            if len(data) > MAX_REPLY_BYTES:
                raise ValueError("reply too large")
    reply = json.loads(data)
    if not isinstance(reply, dict):
        raise ValueError("reply is not an object")
    return reply


def _blocked(reason: str) -> dict[str, Any]:
    return {"decision": "block", "layer": "failure", "reasons": [f"rootsh failed closed: {reason}"]}


def decide(
    path: Path, agent: str, hook: str, payload: object, ask: str = "native"
) -> dict[str, Any]:
    """The daemon's decision for a tool call, or a block if it cannot be had."""
    body = {"op": "event", "agent": agent, "hook": hook, "payload": payload, "ask": ask}
    timeout = ASK_TIMEOUT_S + 10 if ask == "desktop" else TIMEOUT_S
    try:
        reply = request(path, body, timeout)
    except (OSError, ValueError) as exc:
        return _blocked(f"daemon unreachable ({type(exc).__name__}: {exc})")
    if reply.get("decision") not in DECISIONS or not isinstance(reply.get("reasons"), list):
        return _blocked(f"malformed reply {str(reply)[:200]}")
    return reply
