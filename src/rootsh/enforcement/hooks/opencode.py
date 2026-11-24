"""OpenCode bridge: the installed plugin spawns this per hook (design 08 §3).

    python -m rootsh.enforcement.hooks.opencode --socket <path> --hook <name>  < event.json

Prints exactly one JSON line. Exit 0 means: tool call allowed
(`{"decision": "allow"}`) or event recorded (`{"ok": true}`). Any other exit
is a block or a lost record; the plugin throws on anything but exit 0 with
the expected line, so a crash here can only block.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TextIO

from rootsh.enforcement.daemon import client
from rootsh.enforcement.hooks import clip_strings, reason_text

AGENT = "opencode"
PRE = "tool.execute.before"
RECORDED = frozenset({"session.prompt", "tool.execute.after"})
EXIT_BLOCK = 3
EXIT_NOT_RECORDED = 4
EXIT_ERROR = 5


def handle(argv: Sequence[str], stdin: TextIO, stdout: TextIO) -> int:
    parser = argparse.ArgumentParser(prog="rootsh.enforcement.hooks.opencode")
    parser.add_argument("--socket", type=Path, required=True)
    parser.add_argument("--hook", required=True, choices=sorted({PRE, *RECORDED}))
    args = parser.parse_args(argv)
    payload = json.loads(stdin.read())
    if args.hook == PRE:
        # OpenCode's own `ask` runs the command under --auto (P0 §3): rootsh
        # asks the user itself, so the daemon returns only allow or block.
        reply = client.decide(args.socket, AGENT, PRE, payload, ask="desktop")
        if reply.get("decision") == "allow":
            stdout.write(json.dumps({"decision": "allow"}) + "\n")
            return 0
        stdout.write(json.dumps({"decision": "block", "reason": reason_text(reply)}) + "\n")
        return EXIT_BLOCK
    error = client.record(args.socket, AGENT, args.hook, clip_strings(payload))
    if error is not None:
        stdout.write(json.dumps({"ok": False, "error": error}) + "\n")
        return EXIT_NOT_RECORDED
    stdout.write(json.dumps({"ok": True}) + "\n")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    try:
        return handle(args, sys.stdin, sys.stdout)
    except SystemExit as exc:
        sys.stdout.write(json.dumps({"error": f"bridge misconfigured (exit {exc.code})"}) + "\n")
        return EXIT_ERROR
    except BaseException as exc:  # noqa: BLE001 - every failure must block
        text = f"rootsh failed closed: {type(exc).__name__}: {exc}"[:2000]
        sys.stdout.write(json.dumps({"error": text}) + "\n")
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
