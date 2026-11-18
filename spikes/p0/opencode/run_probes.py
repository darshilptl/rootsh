"""Run OpenCode plugin-API probes.

Each case starts `opencode run --standalone` (a private server, so the
project-local plugin is loaded fresh) in its own workspace containing the
probe plugin, asks the model to create a marker file, and records whether the
marker exists afterwards.

Usage: python3 -I run_probes.py [case ...]
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "out"
PLUGIN = HERE / "probe-plugin" / "index.ts"
MODEL = "opencode/deepseek-v4.1-flash-free"
RUN_TIMEOUT_S = 300


@dataclass(frozen=True)
class Case:
    name: str
    mode: str
    auto: bool = True
    tool: str = "shell"


CASES = (
    Case("control_record_auto", "record"),
    Case("record_no_auto", "record", auto=False),
    Case("deny_evaluate_auto", "deny"),
    Case("ask_evaluate_auto", "ask"),
    Case("ask_evaluate_no_auto", "ask", auto=False),
    Case("slow_deny_evaluate_auto", "slow-deny"),
    Case("throw_execute_before_auto", "throw"),
    Case("throw_shell_create_auto", "shell-throw"),
    Case("setup_throw_auto", "setup-throw"),
    Case("throw_execute_before_write_auto", "throw", tool="write"),
    Case("deny_evaluate_write_auto", "deny", tool="write"),
)


def _prompt(case: Case) -> str:
    if case.tool == "write":
        action = "Use the write tool to create the file ./marker containing the text x."
    else:
        action = "Use the shell tool to run exactly this command and nothing else: touch ./marker"
    return (
        f"{action} Do not use any other tool and do not retry. "
        "After the tool call, reply with one word: DONE or FAILED."
    )


def _events(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _tool_outcomes(stdout: str) -> list[dict[str, object]]:
    outcomes = []
    for line in stdout.splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if item.get("type") == "tool_use":
            state = item["part"]["state"]
            outcomes.append(
                {
                    "tool": item["part"]["tool"],
                    "status": state.get("status"),
                    "error": str(state.get("error", ""))[:160],
                }
            )
    return outcomes


def run_case(case: Case) -> dict[str, object]:
    workspace = OUT / case.name
    shutil.rmtree(workspace, ignore_errors=True)
    plugin_dir = workspace / ".opencode" / "plugins" / "rootsh-probe"
    plugin_dir.mkdir(parents=True)
    shutil.copy(PLUGIN, plugin_dir / "index.ts")
    events = workspace / "events.jsonl"
    marker = workspace / "marker"

    argv = ["opencode", "run", "--standalone", "--format", "json", "-m", MODEL]
    if case.auto:
        argv.append("--auto")
    argv.append(_prompt(case))
    # OpenCode resolves its project directory from $PWD, not the process cwd.
    env = {
        **os.environ,
        "PWD": str(workspace),
        "ROOTSH_PROBE_MODE": case.mode,
        "ROOTSH_PROBE_EVENTS": str(events),
    }

    started = time.monotonic()
    proc = subprocess.run(argv, cwd=workspace, env=env, capture_output=True, text=True, timeout=RUN_TIMEOUT_S)
    elapsed = round(time.monotonic() - started, 1)
    (workspace / "opencode_stdout.jsonl").write_text(proc.stdout, encoding="utf-8")
    (workspace / "opencode_stderr.log").write_text(proc.stderr, encoding="utf-8")

    recorded = _events(events)
    kinds: dict[str, int] = {}
    for item in recorded:
        kinds[str(item["kind"])] = kinds.get(str(item["kind"]), 0) + 1

    return {
        "case": case.name,
        "mode": case.mode,
        "auto": case.auto,
        "tool": case.tool,
        "exit_code": proc.returncode,
        "marker_exists": marker.exists(),
        "hook_events": kinds,
        "tool_outcomes": _tool_outcomes(proc.stdout),
        "elapsed_s": elapsed,
        "stderr_tail": proc.stderr[-300:],
    }


def main(selected: list[str]) -> int:
    cases = [c for c in CASES if not selected or c.name in selected]
    OUT.mkdir(exist_ok=True)
    results = [run_case(case) for case in cases]
    for result in results:
        print(json.dumps(result), flush=True)
    (OUT / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
