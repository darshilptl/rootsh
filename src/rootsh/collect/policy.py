"""The collection policy: deny visible network effects, let the rest run
inside the sandbox (design 04 §6).

As a program it serves the OpenCode recorder plugin:
    python -m rootsh.collect.policy < tool.execute.before payload
exit 0 = no decision, exit 3 = deny (reason on stdout). Any other exit is a
policy failure, which the plugin treats as a deny.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from rootsh.effects import Context, EffectKind, analyze_event
from rootsh.events import Agent
from rootsh.normalize import normalize

EXIT_ALLOW, EXIT_DENY = 0, 3

# Web tools by name, case-folded: OpenCode's `webfetch`/`websearch` are not
# mapped by the normalizer (input shapes unverified), so the effect extractor
# cannot see their network access. Collection denies them outright.
WEB_TOOLS = frozenset({"webfetch", "websearch"})
PRE_HOOKS = {Agent.CLAUDE_CODE: "PreToolUse", Agent.OPENCODE: "tool.execute.before"}


def deny_reason(agent: Agent, payload: object, home: str) -> str | None:
    """Why a proposed tool call is denied, or None to let it run.

    Raises EventParseError for a payload that cannot be normalized; callers
    deny in that case.
    """
    event = normalize(agent, PRE_HOOKS[agent], payload, datetime.now(UTC))
    if (event.tool_raw or "").lower() in WEB_TOOLS:
        return f"rootsh collection: web tools are not allowed here ({event.tool_raw})"
    analysis = analyze_event(event, Context(event.cwd, home))
    targets = sorted({e.target for e in analysis.effects if e.kind is EffectKind.NETWORK})
    if not targets:
        return None
    return "rootsh collection: network access is not allowed here (" + ", ".join(targets) + ")"


def main() -> int:
    reason = deny_reason(Agent.OPENCODE, json.loads(sys.stdin.read()), str(Path.home()))
    if reason is None:
        return EXIT_ALLOW
    print(reason)
    return EXIT_DENY


if __name__ == "__main__":
    sys.exit(main())
