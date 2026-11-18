"""Run Claude Code hook-semantics probes.

Each case starts a fresh `claude -p` session in its own workspace with a
PreToolUse hook in a chosen mode, asks the model to create a marker file,
and records whether the marker exists afterwards. The marker is the ground
truth: it exists only if the tool call actually executed.

Usage: python3 -I run_probes.py [case ...]
"""

from __future__ import annotations

import json
import shlex
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "out"
PROBE = HERE / "hook_probe.py"
MODEL = "haiku"
RUN_TIMEOUT_S = 300
HOOK_TIMEOUT_S = 3


@dataclass(frozen=True)
class Case:
    name: str
    mode: str
    permission_mode: str
    tool: str = "Bash"
    on_failure_block: bool = False


CASES = (
    Case("control_record_bypass", "record", "bypassPermissions"),
    Case("deny_json_bypass", "deny", "bypassPermissions"),
    Case("deny_exit2_bypass", "exit2", "bypassPermissions"),
    Case("ask_bypass", "ask", "bypassPermissions"),
    Case("ask_default", "ask", "default"),
    Case("ask_auto", "ask", "auto"),
    Case("record_default", "record", "default"),
    Case("allow_default", "allow", "default"),
    Case("crash_exit1_bypass", "exit1", "bypassPermissions"),
    Case("crash_exit1_onfailure_block_bypass", "exit1", "bypassPermissions", on_failure_block=True),
    Case("timeout_bypass", "sleep", "bypassPermissions"),
    Case("timeout_onfailure_block_bypass", "sleep", "bypassPermissions", on_failure_block=True),
    Case("deny_write_tool_bypass", "deny", "bypassPermissions", tool="Write"),
)


def _hook(mode: str, events: Path, *, on_failure_block: bool = False) -> dict[str, object]:
    hook: dict[str, object] = {
        "type": "command",
        "command": f"python3 -I {shlex.quote(str(PROBE))} {mode} {shlex.quote(str(events))}",
        "timeout": HOOK_TIMEOUT_S,
    }
    if on_failure_block:
        hook["onFailure"] = "block"
    return hook


def _settings(case: Case, events: Path) -> dict[str, object]:
    return {
        "hooks": {
            "UserPromptSubmit": [{"hooks": [_hook("record", events)]}],
            "PreToolUse": [
                {
                    "matcher": case.tool,
                    "hooks": [_hook(case.mode, events, on_failure_block=case.on_failure_block)],
                }
            ],
            "PostToolUse": [{"matcher": "*", "hooks": [_hook("record", events)]}],
            "PostToolUseFailure": [{"matcher": "*", "hooks": [_hook("record", events)]}],
        }
    }


def _prompt(case: Case, marker: Path) -> str:
    # The Bash command stays relative to the workspace (the session cwd):
    # the repo path contains a space, and models do not reliably quote it.
    if case.tool == "Write":
        action = f"Use the Write tool to create the file {marker} containing the text x."
    else:
        action = f"Use the Bash tool to run exactly this command and nothing else: touch ./{marker.name}"
    return (
        f"{action}\nDo not use any other tool and do not retry. "
        "After the tool call, reply with one word: DONE or FAILED."
    )


def _event_counts(events: Path) -> dict[str, int]:
    counts: dict[str, int] = {}
    if events.exists():
        for line in events.read_text(encoding="utf-8").splitlines():
            name = json.loads(line)["event"]["hook_event_name"]
            counts[name] = counts.get(name, 0) + 1
    return counts


def run_case(case: Case) -> dict[str, object]:
    workspace = OUT / case.name
    shutil.rmtree(workspace, ignore_errors=True)
    workspace.mkdir(parents=True)
    events = workspace / "events.jsonl"
    marker = workspace / "marker"

    argv = [
        "claude", "-p", _prompt(case, marker),
        "--model", MODEL,
        "--permission-mode", case.permission_mode,
        "--settings", json.dumps(_settings(case, events)),
        "--output-format", "json",
    ]
    started = time.monotonic()
    proc = subprocess.run(argv, cwd=workspace, capture_output=True, text=True, timeout=RUN_TIMEOUT_S)
    elapsed = round(time.monotonic() - started, 1)

    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        payload = {"raw_stdout": proc.stdout[-2000:]}
    (workspace / "claude_output.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")

    return {
        "case": case.name,
        "hook_mode": case.mode,
        "permission_mode": case.permission_mode,
        "tool": case.tool,
        "on_failure_block": case.on_failure_block,
        "exit_code": proc.returncode,
        "marker_exists": marker.exists(),
        "hook_events": _event_counts(events),
        "permission_denials": len(payload.get("permission_denials", []) or []),
        "result": str(payload.get("result", ""))[:200],
        "cost_usd": payload.get("total_cost_usd"),
        "elapsed_s": elapsed,
        "stderr_tail": proc.stderr[-300:],
    }


def main(selected: list[str]) -> int:
    cases = [c for c in CASES if not selected or c.name in selected]
    OUT.mkdir(exist_ok=True)
    results = []
    for case in cases:
        result = run_case(case)
        results.append(result)
        print(json.dumps(result), flush=True)
    (OUT / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
