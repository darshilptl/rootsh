"""Claude Code hook command (design 08 §2).

Installed as: python -m rootsh.hooks.claude_code --socket <path>
for UserPromptSubmit, PreToolUse, PostToolUse and PostToolUseFailure, each with
`onFailure: "block"`.

- PreToolUse: allow -> exit 0 and no output (rootsh never answers `allow`,
  which would override Claude Code's own permission rules, P0 §1); ask ->
  `permissionDecision: "ask"`; block -> `deny` with the reasons.
- UserPromptSubmit / PostToolUse[Failure]: recorded; if the daemon did not store the
  event, exit 2 (blocks the prompt; shows the reason after a tool).
- Any error at all -> exit 2 with a one-line reason on stderr, which Claude
  Code treats as a block.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Sequence
from typing import TextIO

from rootsh.daemon import client
from rootsh.hooks import clip_strings, reason_text, socket_arg

AGENT = "claude_code"
PRE = "PreToolUse"
RECORDED = frozenset({"UserPromptSubmit", "PostToolUse", "PostToolUseFailure"})
EXIT_BLOCK = 2


def _decision(kind: str, reason: str) -> str:
    output = {
        "hookEventName": PRE,
        "permissionDecision": kind,
        "permissionDecisionReason": reason,
    }
    return json.dumps({"hookSpecificOutput": output})


def handle(argv: Sequence[str], stdin: TextIO, stdout: TextIO, stderr: TextIO) -> int:
    path = socket_arg(argv, "rootsh.hooks.claude_code")
    payload = json.loads(stdin.read())
    if not isinstance(payload, dict):
        raise ValueError("hook input is not a JSON object")
    hook = payload.get("hook_event_name")
    if hook == PRE:
        reply = client.decide(path, AGENT, PRE, payload, ask="native")
        decision = reply.get("decision")
        if decision == "allow":
            return 0  # no decision: Claude Code's own rules still apply
        if decision == "ask":
            stdout.write(_decision("ask", reason_text(reply, "asks about")))
            return 0
        stdout.write(_decision("deny", reason_text(reply)))
        return 0
    if hook in RECORDED:
        error = client.record(path, AGENT, str(hook), clip_strings(payload))
        if error is not None:
            stderr.write(f"rootsh: {hook} {error}\n")
            return EXIT_BLOCK
        return 0
    raise ValueError(f"hook {hook!r} is not one rootsh handles")


def main(argv: Sequence[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    try:
        return handle(args, sys.stdin, sys.stdout, sys.stderr)
    except SystemExit as exc:  # argparse: bad arguments are an install error
        sys.stderr.write(f"rootsh: hook misconfigured (exit {exc.code})\n")
        return EXIT_BLOCK
    except BaseException as exc:  # noqa: BLE001 - every failure must block
        sys.stderr.write(f"rootsh failed closed: {type(exc).__name__}: {exc}"[:2000] + "\n")
        return EXIT_BLOCK


if __name__ == "__main__":
    sys.exit(main())
