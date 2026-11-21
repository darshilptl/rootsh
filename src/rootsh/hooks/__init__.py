"""Agent hook commands (docs/design/08-hooks-install.md).

Both agents reach the daemon only through `rootsh.daemon.client`. Nothing
here reads the environment: the installer passes every path as an argument.
Import cost matters (one Python start per hook call), so this package
imports only the client and the event constants.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from rootsh.events.model import MAX_TEXT_CHARS


def socket_arg(argv: Sequence[str], prog: str) -> Path:
    parser = argparse.ArgumentParser(prog=prog)
    parser.add_argument("--socket", type=Path, required=True)
    return Path(parser.parse_args(argv).socket)


def clip_strings(value: object, limit: int = MAX_TEXT_CHARS) -> object:
    """Every string in a JSON value cut to `limit` characters.

    Only for events that are recorded, never for tool calls being decided:
    the normalizer keeps at most MAX_TEXT_CHARS of any text, so clipping
    first loses nothing it would store, and keeps a large tool output under
    the daemon's request limit."""
    if isinstance(value, str):
        return value[:limit]
    if isinstance(value, list):
        return [clip_strings(v, limit) for v in value]
    if isinstance(value, dict):
        return {k: clip_strings(v, limit) for k, v in value.items()}
    return value


def reason_text(reply: dict[str, object], verb: str = "blocked") -> str:
    reasons = reply.get("reasons")
    items = [str(r) for r in reasons] if isinstance(reasons, list) else []
    layer = reply.get("layer", "?")
    text = "; ".join(items) or "no reason given"
    return f"rootsh {verb} this ({layer}): {text}"[:2000]
