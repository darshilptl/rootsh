"""P7 latency (design 07 §8 item 5): host-state probes and whole decisions.

Nothing is executed: commands are only decided. Probes are read-only.
Since P9 the daemon blocks tool calls from unregistered launches, so the
script registers one launch (from this process's own, non-agent cgroup) and
sends events as a peer inside that launch's `rootsh-agent-<id>.scope`.
Usage (runtime DB goes to a scratch directory):

  uv run python research/policy_latency.py <large repo> data/model <scratch dir>
"""

from __future__ import annotations

import os
import shlex
import statistics
import sys
import time
import uuid
from pathlib import Path

import pygit2

from rootsh.analysis.hoststate import probe
from rootsh.enforcement.daemon import load_model
from rootsh.enforcement.daemon.peer import Peer
from rootsh.enforcement.daemon.service import Service
from rootsh.storage.db import DatabaseKind, open_database
from rootsh.storage.migrations import migrate
from rootsh.storage.schema import MIGRATIONS

FILES = 60
ME = Peer(os.getpid(), os.getuid(), os.getgid())


def _ms(samples: list[float]) -> str:
    q = statistics.quantiles(samples, n=100, method="inclusive")
    return f"n={len(samples)} p50={q[49]:.1f} ms p99={q[98]:.1f} ms max={max(samples):.1f} ms"


def _sample(repo: Path) -> tuple[list[str], list[str]]:
    tracked = sorted(entry.path for entry in pygit2.Repository(str(repo)).index)
    step = max(1, len(tracked) // FILES)
    files = [str(repo / f) for f in tracked[::step][:FILES]]
    dirs = [str(repo), *sorted(str(p) for p in repo.iterdir() if p.is_dir())]
    return files, dirs


def _timed(fn: object, *args: object) -> float:
    started = time.perf_counter()
    fn(*args)  # type: ignore[operator]
    return (time.perf_counter() - started) * 1000


def main(repo_arg: str, bundle: str, scratch: str) -> None:
    repo = Path(repo_arg).resolve()
    home = str(Path.home())
    files, dirs = _sample(repo)
    total = sum(1 for _ in repo.rglob("*"))
    print(f"repo entries (rglob): {total}; sampled files: {len(files)}; directories: {len(dirs)}")

    def one(path: str) -> None:
        probe([path], project_root=str(repo), home=home)

    print("probe, tracked file:", _ms([_timed(one, p) for p in files]))
    dir_ms = [_timed(one, p) for p in dirs]
    print("probe, directory:  ", _ms(dir_ms) if len(dir_ms) > 1 else dir_ms)

    model, error = load_model(Path(bundle))
    if error:
        raise SystemExit(error)
    conn = open_database(Path(scratch) / "latency-runtime.db", DatabaseKind.RUNTIME, threads=True)
    migrate(conn, MIGRATIONS[DatabaseKind.RUNTIME])
    svc = Service(conn, model, home=home)
    launch = uuid.uuid4().hex
    reg = svc.handle({"op": "register", "launch": launch, "agent": "claude_code",
                      "project": str(repo), "home": home, "layers": []}, ME)  # fmt: skip
    if not reg.get("ok"):
        raise SystemExit(f"register failed: {reg}")
    agent = Peer(ME.pid, ME.uid, ME.gid, cgroup=f"0::/user.slice/rootsh-agent-{launch}.scope")
    session = f"latency-{time.time_ns()}"
    base = {"session_id": session, "cwd": str(repo)}
    svc.handle({"op": "event", "agent": "claude_code", "hook": "UserPromptSubmit",
                "payload": {**base, "hook_event_name": "UserPromptSubmit", "prompt": "Tidy up.",
                            "prompt_id": "p"}}, agent)  # fmt: skip
    n = 0

    def decide(command: str) -> tuple[float, str]:
        nonlocal n
        n += 1
        payload = {
            **base, "hook_event_name": "PreToolUse", "tool_name": "Bash",
            "tool_input": {"command": command}, "tool_use_id": f"t{n}", "prompt_id": "p",
        }  # fmt: skip
        started = time.perf_counter()
        reply = svc.handle({"op": "event", "agent": "claude_code", "hook": "PreToolUse",
                            "payload": payload, "ask": "native"}, agent)  # fmt: skip
        return (time.perf_counter() - started) * 1000, f"{reply['layer']}:{reply['decision']}"

    for label, commands in (
        ("decide `cat <file>`", [f"cat {shlex.quote(p)}" for p in files]),
        ("decide `rm <file>`", [f"rm {shlex.quote(p)}" for p in files]),
        ("decide `rm -rf <dir>`", [f"rm -rf {shlex.quote(p)}" for p in dirs]),
    ):
        results = [decide(c) for c in commands]
        layers: dict[str, int] = {}
        for _, outcome in results:
            layers[outcome] = layers.get(outcome, 0) + 1
        print(f"{label}: {_ms([ms for ms, _ in results])}; outcomes {layers}")
    conn.close()


if __name__ == "__main__":
    main(*sys.argv[1:4])
