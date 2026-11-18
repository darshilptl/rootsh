"""Claude Code hook probe.

Usage: hook_probe.py <mode> <events_file>

Appends the hook's stdin event to <events_file> as one JSON line, then
responds according to <mode>:

  record     exit 0, no output (no decision)
  allow      PreToolUse JSON decision "allow"
  deny       PreToolUse JSON decision "deny"
  ask        PreToolUse JSON decision "ask"
  exit2      exit code 2 with a reason on stderr
  exit1      exit code 1 (a crashing hook)
  sleep      sleep 30 s (longer than the configured hook timeout)
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path


def _decision(value: str) -> str:
    return json.dumps(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": value,
                "permissionDecisionReason": f"rootsh p0 probe: {value}",
            }
        }
    )


def main(mode: str, events_file: Path) -> int:
    event = json.loads(sys.stdin.read())
    with events_file.open("a", encoding="utf-8") as sink:
        sink.write(json.dumps({"mode": mode, "event": event}) + "\n")

    if mode in {"allow", "deny", "ask"}:
        print(_decision(mode))
        return 0
    if mode == "exit2":
        print("rootsh p0 probe: blocked by exit 2", file=sys.stderr)
        return 2
    if mode == "exit1":
        return 1
    if mode == "sleep":
        time.sleep(30)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], Path(sys.argv[2])))
