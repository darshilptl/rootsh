"""Claude Code hook for recording P3 transcripts.

Usage (as a hook command): claude_recorder.py <events_file>

Appends every hook event to <events_file>. For PreToolUse it lets read-only
tools and read-only shell commands run (rootsh's effect extractor decides),
and denies everything else, so destructive proposals are recorded but never
executed. Run with the repository's Python so `rootsh` is importable.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from readonly import is_read_only

READ_ONLY = frozenset({"Read", "Glob", "Grep", "LS"})


def _decision(value: str, reason: str) -> str:
    output = {"hookEventName": "PreToolUse", "permissionDecision": value}
    return json.dumps({"hookSpecificOutput": {**output, "permissionDecisionReason": reason}})


def main(events_file: Path) -> int:
    event = json.loads(sys.stdin.read())
    with events_file.open("a", encoding="utf-8") as sink:
        sink.write(json.dumps({"hook": event["hook_event_name"], "payload": event}) + "\n")
    if event["hook_event_name"] != "PreToolUse" or event.get("tool_name") in READ_ONLY:
        return 0
    command = (event.get("tool_input") or {}).get("command")
    is_shell = event.get("tool_name") == "Bash" and isinstance(command, str)
    if is_shell and is_read_only(command, event.get("cwd") or "/"):
        print(_decision("allow", "rootsh P3 recording: read-only command"))
        return 0
    print(_decision("deny", "rootsh P3 recording: only read-only actions may run"))
    return 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1])))
