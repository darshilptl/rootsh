"""Claude Code recording hook for gold-set collection.

Usage (as a hook command): python -m rootsh.research.collect.claude_hook <events.jsonl>

Appends every delivery as {"hook", "at", "payload"}. For PreToolUse it denies
visible network effects (rootsh.research.collect.net_guard) and otherwise makes no
decision: the sandbox, not Claude Code, contains the action. Never answers
allow (P0 consequence 1). A payload it cannot parse is denied.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from rootsh.analysis.events import Agent, EventParseError
from rootsh.research.collect.net_guard import deny_reason


def record(sink: Path, hook: str, payload: object) -> None:
    line = {"hook": hook, "at": datetime.now(UTC).isoformat(), "payload": payload}
    with sink.open("a", encoding="utf-8") as out:
        out.write(json.dumps(line, ensure_ascii=False) + "\n")


def deny(reason: str) -> str:
    output = {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": reason,
    }
    return json.dumps({"hookSpecificOutput": output})


def main(sink: Path) -> int:
    payload = json.loads(sys.stdin.read())
    hook = payload.get("hook_event_name") if isinstance(payload, dict) else None
    record(sink, str(hook), payload)
    if hook != "PreToolUse":
        return 0
    try:
        reason = deny_reason(Agent.CLAUDE_CODE, payload, str(Path.home()))
    except EventParseError as exc:
        reason = f"rootsh collection: unreadable tool call ({exc})"
    if reason is not None:
        record(sink, "rootsh.deny", {"reason": reason, "tool_use_id": payload.get("tool_use_id")})
        print(deny(reason))
    return 0


HOOK_ERRORS = "hook-errors.log"


def run(sink: Path) -> int:
    """main(), but a crash is logged next to the events before it propagates:
    Claude Code blocks the call (onFailure) and discards our stderr, so the
    harness would otherwise never learn that a hook failed."""
    try:
        return main(sink)
    except BaseException as exc:
        with (sink.parent / HOOK_ERRORS).open("a", encoding="utf-8") as log:
            log.write(f"{datetime.now(UTC).isoformat()} {type(exc).__name__}: {exc}\n")
        raise


if __name__ == "__main__":
    sys.exit(run(Path(sys.argv[1])))
