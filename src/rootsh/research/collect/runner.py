"""Running one scenario under one agent, end to end (design 04 §2, §5-§7).

This module is the impure shell around the pure pieces: it builds the
workspace, launches the sandboxed agent, hashes what came back and stores it.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import shlex
import shutil
import socket
import sqlite3
import stat
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from rootsh import __version__
from rootsh.analysis.events import Agent
from rootsh.research.collect.claude_hook import HOOK_ERRORS
from rootsh.research.collect.sandbox import RECORD_DIR, SandboxSpec, sandbox_argv
from rootsh.research.collect.scenario import Scenario
from rootsh.research.collect.store import (
    Identity,
    RunRecord,
    claude_spend,
    parse_raw,
    sanitize,
    store_run,
    trace_id,
)
from rootsh.research.collect.workspace import build, tree_entries, workspace_sha256

# OpenCode's free tier stopped after ~25 runs (HTTP 429, design 04 §12); the
# author switched collection to NVIDIA NIM, reached only through keyproxy.
OPENCODE_MODEL = "nvidia/nvidia/nemotron-3.5-super-vl-120b-a12b"
CLAUDE_MODEL = "haiku"
CLAUDE_EFFORT = "low"
CLAUDE_RUN_CAP_USD = 0.04
CLAUDE_TOTAL_CAP_USD = 0.50
REPEAT_ATTEMPTS = (2, 3)
TIMEOUT_S = 300

# Every run is a transient systemd user scope, so a runaway agent cannot take
# the desktop's memory or process table, plus a per-file size cap (RLIMIT_FSIZE)
# so it cannot fill the disk with one file. Values are the design's (§5).
LIMITS = (
    "systemd-run",
    "--user",
    "--scope",
    "--quiet",
    "-p",
    "MemoryMax=1536M",
    "-p",
    "MemorySwapMax=256M",
    "-p",
    "TasksMax=512",
    "-p",
    "CPUQuota=400%",
    "--",
    "prlimit",
    f"--fsize={512 * 1024**2}",
    "--",
)

EVENTS_FILE = "events.jsonl"
PLUGIN_SOURCE = Path(__file__).resolve().parent / "opencode" / "index.ts"
CLAUDE_HOOKS = ("UserPromptSubmit", "PreToolUse", "PostToolUse", "PostToolUseFailure")
CREDENTIALS = Path(".claude") / ".credentials.json"


@dataclass(frozen=True, slots=True)
class Backend:
    """How OpenCode reaches its model in this collection: the model id and the
    host key proxy (keyproxy.py) it must use instead of holding a key."""

    model: str
    proxy_url: str
    proxy_token: str
    proxy_port: int

    @property
    def provider(self) -> str:
        return self.model.split("/", 1)[0]


@dataclass(frozen=True, slots=True)
class PlannedRun:
    scenario: Scenario
    agent: Agent
    attempt: int

    @property
    def trace_id(self) -> str:
        return trace_id(self.scenario.id, self.agent, self.attempt)


def plan(catalog: Sequence[Scenario]) -> tuple[PlannedRun, ...]:
    """Every run the design budgets for, in catalog order (design 04 §2)."""
    runs = [PlannedRun(s, Agent.OPENCODE, 1) for s in catalog]
    runs += [PlannedRun(s, Agent.OPENCODE, a) for s in catalog if s.repeat for a in REPEAT_ATTEMPTS]
    runs += [PlannedRun(s, Agent.CLAUDE_CODE, 1) for s in catalog if s.claude]
    return tuple(runs)


@dataclass(frozen=True, slots=True)
class Host:
    """Where things are on this machine; resolved once per collection."""

    home: Path
    who: Identity
    python: Path  # the repository's interpreter: hooks import rootsh with it
    read_only: tuple[Path, ...]
    path: str  # PATH inside the sandbox

    @classmethod
    def detect(cls, toolenv: Path | None) -> Host:
        """`toolenv`: an optional venv (e.g. with pytest) offered to the agents,
        so they need not install packages, which the policy would deny."""
        home = Path.home()
        node = shutil.which("node")
        node_dir = Path(node).resolve().parents[1] if node else None
        tool_dirs = [p for p in (toolenv, node_dir) if p is not None]
        # Agent installs; ~/.local/bin only for the claude launcher, so `uv`
        # (which downloads packages) is not offered to the agents.
        installs = (
            home / ".local" / "share" / "claude",
            home / ".local" / "bin" / "claude",
            home / ".opencode",
            home / ".local" / "share" / "uv",
        )
        # Only what the hooks need from the repository: not data/, which holds
        # the research DB and other runs' traces.
        repo = Path(__file__).resolve().parents[4]
        needed = (*installs, *tool_dirs, repo / ".venv", repo / "src")
        bins = [
            home / ".local" / "bin",
            home / ".opencode" / "bin",
            *(d / "bin" for d in tool_dirs),
        ]
        path = ":".join([*(str(b) for b in bins), "/usr/bin"])
        read_only = tuple(p for p in needed if p.exists())
        return cls(home, Identity.current(), Path(sys.executable), read_only, path)


# OpenCode inside Landlock: its --standalone server binds a random port,
# which confine forbids. Instead the harness picks one free port, OpenCode's
# server binds it inside the sandbox, and `run` talks to it; confine allows
# that one port only. Arguments are positional ($1..$3), never interpolated.
_OPENCODE_SCRIPT = """
opencode serve --hostname 127.0.0.1 --port "$1" > /rec/server.log 2>&1 &
i=0
until curl -s -o /dev/null "http://127.0.0.1:$1/"; do
  i=$((i + 1)); [ "$i" -ge 150 ] && { echo "rootsh: opencode server not ready" >&2; exit 70; }
  sleep 0.2
done
exec opencode run --server "http://127.0.0.1:$1" --auto --format json -m "$2" "$3"
"""
SERVER_LOG = "server.log"


def free_port() -> int:
    """A port nothing on the host listens on right now. If something takes it
    before OpenCode binds it, the bind fails and the run reports an error."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def agent_command(
    agent: Agent, prompt: str, host: Host, port: int | None = None, model: str = OPENCODE_MODEL
) -> tuple[list[str], dict[str, str]]:
    """argv and extra environment for the agent inside the sandbox."""
    env = {"PATH": host.path, "PYTHONDONTWRITEBYTECODE": "1"}
    events = f"{RECORD_DIR}/{EVENTS_FILE}"
    if agent is Agent.OPENCODE:
        if port is None:
            raise ValueError("OpenCode runs need a local port for its server")
        # The in-sandbox server requires a password [RUN]; a fresh one per run.
        password = secrets.token_urlsafe(24)
        env |= {
            "ROOTSH_EVENTS": events,
            "ROOTSH_PYTHON": str(host.python),
            "OPENCODE_SERVER_PASSWORD": password,
            "OPENCODE_PASSWORD": password,
        }
        argv = ["sh", "-c", _OPENCODE_SCRIPT, "rootsh-opencode", str(port), model]
        return [*argv, prompt], env
    hook = {
        "type": "command",
        "command": shlex.join(
            [str(host.python), "-m", "rootsh.research.collect.claude_hook", events]
        ),
        "onFailure": "block",
    }
    settings = {"hooks": {h: [{"matcher": "*", "hooks": [hook]}] for h in CLAUDE_HOOKS}}
    argv = [
        "claude",
        "-p",
        prompt,
        "--model",
        CLAUDE_MODEL,
        "--effort",
        CLAUDE_EFFORT,
        "--max-budget-usd",
        f"{CLAUDE_RUN_CAP_USD:.2f}",
        "--permission-mode",
        "bypassPermissions",
        "--strict-mcp-config",
        "--output-format",
        "json",
        "--settings",
        json.dumps(settings),
    ]
    return argv, env


def harness_argv(
    host: Host,
    workspace: Path,
    record_dir: Path,
    home_dir: Path,
    command: Sequence[str],
    env: Mapping[str, str],
    local_port: int | None = None,
    connect_ports: Sequence[int] = (),
) -> list[str]:
    """The full wrapping of every collection run, outermost first: resource
    limits, the bwrap sandbox, then Landlock (confine) around the command.
    The security tests run exactly this."""
    port = [] if local_port is None else ["--local-port", str(local_port)]
    port += [arg for extra in connect_ports for arg in ("--connect-port", str(extra))]
    confined = [str(host.python), "-m", "rootsh.research.collect.confine", *port, "--", *command]
    spec = SandboxSpec(workspace, record_dir, host.home, home_dir, host.read_only, env)
    return [*LIMITS, *sandbox_argv(spec, confined)]


def agent_version(agent: Agent) -> str:
    program = "claude" if agent is Agent.CLAUDE_CODE else "opencode"
    out = subprocess.run(  # noqa: S603 - fixed argv
        [program, "--version"], capture_output=True, text=True, check=True, timeout=30
    )
    return out.stdout.strip()


def claude_cost(stdout: str) -> float | None:
    """total_cost_usd from Claude Code's JSON result, if it printed one."""
    try:
        value = json.loads(stdout).get("total_cost_usd")
    except (json.JSONDecodeError, AttributeError):
        return None
    return float(value) if isinstance(value, int | float) else None


def budget_left(conn: sqlite3.Connection) -> float:
    """Claude Code budget not yet spent. A run whose cost is unknown counts at
    its cap, so the total can never be exceeded unnoticed."""
    (unknown,) = conn.execute(
        "SELECT count(*) FROM trace_runs r JOIN traces t ON t.id = r.trace_id "
        "WHERE t.agent = 'claude_code' AND r.cost_usd IS NULL"
    ).fetchone()
    return CLAUDE_TOTAL_CAP_USD - claude_spend(conn) - int(unknown) * CLAUDE_RUN_CAP_USD


def _make_writable(func: Callable[..., object], path: str, _exc: BaseException) -> None:
    """rmtree helper: an agent may have removed permissions on what it made.
    rmtree never follows symlinks, and neither does this (islink guard)."""
    parent = os.path.dirname(path)
    os.chmod(parent, stat.S_IRWXU)
    if os.path.isdir(path) and not os.path.islink(path):
        os.chmod(path, stat.S_IRWXU)
    func(path)


class CollectionStopError(Exception):
    """Collection must stop; the current run is not stored."""


# Agent state in the sandbox home that every run writes; not agent behaviour.
_AGENT_STATE = (
    ".claude",
    ".claude.json",
    ".local/share/opencode",
    ".local/state/opencode",
    ".cache/opencode",
    ".config/opencode/plugins/rootsh-collect",
    ".config/opencode/opencode.json",
)
MIN_TOKEN_LIFETIME_S = 3600
MIN_FREE_BYTES = 2 * 1024**3


def read_credential(host: Host) -> bytes:
    """The author's Claude Code credential file; tests replace this so they
    never read, or copy into temp directories, a real token."""
    return (host.home / CREDENTIALS).read_bytes()


def staged_credential(raw: bytes, now_s: float) -> bytes:
    """The Claude Code credential the sandbox gets: the access token only.

    No refresh token, so nothing in the sandbox can rotate it and log the
    author out, and no other stored secret (e.g. MCP OAuth tokens). The token
    must outlive any run, so Claude Code never needs to refresh it.
    """
    oauth = json.loads(raw).get("claudeAiOauth")
    if not isinstance(oauth, dict) or not isinstance(oauth.get("expiresAt"), int):
        raise CollectionStopError("Claude Code credential has no claudeAiOauth.expiresAt")
    if oauth["expiresAt"] / 1000 - now_s < MIN_TOKEN_LIFETIME_S:
        raise CollectionStopError(
            "Claude Code access token expires within an hour; run any `claude` command "
            "on the host to refresh it, then resume"
        )
    kept = {k: v for k, v in oauth.items() if k not in ("refreshToken", "refreshTokenExpiresAt")}
    return json.dumps({"claudeAiOauth": kept}).encode()


def opencode_config(backend: Backend) -> str:
    """OpenCode v2 config pointing the provider at the key proxy [RUN]; the
    "API key" it holds is the proxy's per-collection access token."""
    options = {"baseURL": backend.proxy_url, "apiKey": backend.proxy_token}
    return json.dumps({"provider": {backend.provider: {"options": options}}})


def _stage_home(home_dir: Path, host: Host, agent: Agent, backend: Backend | None) -> None:
    """A fresh home. The OpenCode recorder is a global plugin here, not in the
    workspace, so the scenario's files are exactly the catalog's."""
    home_dir.mkdir(parents=True)
    if agent is Agent.OPENCODE:
        plugin = home_dir / ".config" / "opencode" / "plugins" / "rootsh-collect"
        plugin.mkdir(parents=True)
        shutil.copy(PLUGIN_SOURCE, plugin / "index.ts")
        if backend is None:
            raise CollectionStopError("OpenCode runs need a model backend (key proxy)")
        (home_dir / ".config" / "opencode" / "opencode.json").write_text(opencode_config(backend))
    else:
        (home_dir / ".claude").mkdir()
        credential = staged_credential(read_credential(host), time.time())
        (home_dir / CREDENTIALS).write_bytes(credential)
        (home_dir / CREDENTIALS).chmod(0o600)
        (home_dir / ".claude.json").write_text('{"hasCompletedOnboarding": true}\n')


def home_files(home_dir: Path, host: Host) -> list[tuple[str, str]]:
    """(path, digest) of what the agent left in its home, minus agent state and
    the empty mount points bwrap created for the read-only binds."""
    mounts = {
        p.relative_to(host.home).as_posix() for p in host.read_only if p.is_relative_to(host.home)
    }
    skip = (*_AGENT_STATE, *mounts)
    found = []
    for relative, _mode, digest in tree_entries(home_dir):
        if any(relative == s or relative.startswith(s + "/") for s in skip):
            continue
        found.append((relative, digest))
    return found


def agent_error(agent: Agent, stdout: str) -> tuple[str | None, bool]:
    """The agent's own error report, and whether it is a provider usage limit
    (which ends the collection: every further run would fail the same way)."""
    if agent is Agent.OPENCODE:
        for line in stdout.splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict) and event.get("type") == "error":
                raw_error = event.get("error")
                error: dict[str, object] = raw_error if isinstance(raw_error, dict) else {}
                text = f"{error.get('type')}: {error.get('message')}"
                # Any provider-side failure (limit, outage, auth) says nothing
                # about the agent: storing it would hide a missing run.
                fatal = str(error.get("type", "")).startswith("provider.")
                return text, fatal or error.get("status") == 429
        return None, False
    try:
        result = json.loads(stdout)
    except json.JSONDecodeError:
        return None, False
    if not isinstance(result, dict) or not result.get("is_error"):
        return None, False
    text = f"{result.get('subtype')}: {str(result.get('result'))[:500]}"
    fatal = result.get("api_error_status") is not None or "limit" in text.lower()
    return text, fatal


MAX_RECORD_BYTES = 256 * 1024**2


def read_record(path: Path) -> bytes:
    """Read a file the sandboxed agent could have tampered with: never follow
    a symlink (it could point at a host secret, read here outside the
    sandbox), accept only a regular file, and cap the size."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return b""
    except OSError as exc:  # ELOOP: a symlink was planted
        raise CollectionStopError(
            f"{path.name} is not a plain file ({exc}); run not stored"
        ) from exc
    with os.fdopen(fd, "rb") as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            raise CollectionStopError(f"{path.name} is not a regular file; run not stored")
        data = handle.read(MAX_RECORD_BYTES + 1)
    if len(data) > MAX_RECORD_BYTES:
        raise CollectionStopError(f"{path.name} exceeds {MAX_RECORD_BYTES} bytes; run not stored")
    return data


def _text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    return value.decode("utf-8", "replace") if isinstance(value, bytes) else value


def _abort(data: Path, run: PlannedRun, output: bytes, raw: bytes, work: Path) -> None:
    """Keep an unstored run's output and raw events for inspection; clean up."""
    aborted = data / "aborted"
    aborted.mkdir(parents=True, exist_ok=True)
    (aborted / f"{run.trace_id}.out").write_bytes(output)
    (aborted / f"{run.trace_id}.jsonl").write_bytes(raw)
    shutil.rmtree(work, onexc=_make_writable)


def collect(
    run: PlannedRun,
    host: Host,
    data: Path,
    conn: sqlite3.Connection,
    version: str,
    backend: Backend | None = None,
) -> str:
    """Run, hash and store one planned run. Returns a one-line summary.

    Raises CollectionStopError (and stores nothing) on a provider usage limit, low
    disk space, or a Claude Code token about to expire."""
    data.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(data).free < MIN_FREE_BYTES:
        raise CollectionStopError("less than 2 GiB free on the data disk")
    work = data / "work" / run.trace_id
    if work.exists():
        shutil.rmtree(work, onexc=_make_writable)  # leftover of an interrupted run
    workspace, home_dir, record_dir = work / "ws", work / "home", work / "rec"
    build(run.scenario, workspace)
    before = workspace_sha256(workspace, git=run.scenario.git)
    _stage_home(home_dir, host, run.agent, backend)
    record_dir.mkdir()

    opencode = run.agent is Agent.OPENCODE and backend is not None
    port = free_port() if opencode else None
    model = backend.model if backend is not None else OPENCODE_MODEL
    command, env = agent_command(run.agent, run.scenario.prompt, host, port, model)
    extra = (backend.proxy_port,) if backend is not None and opencode else ()
    argv = harness_argv(host, workspace, record_dir, home_dir, command, env, port, extra)
    collected_at = datetime.now(UTC)
    start = time.monotonic()
    try:
        done = subprocess.run(  # noqa: S603 - argv built by sandbox_argv
            argv,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_S,
        )
        exit_code: int | None = done.returncode
        stdout, stderr = done.stdout, done.stderr
    except subprocess.TimeoutExpired as expired:
        exit_code, stdout, stderr = None, _text(expired.stdout), _text(expired.stderr)
    duration_ms = int((time.monotonic() - start) * 1000)

    server_log = read_record(record_dir / SERVER_LOG).decode("utf-8", "replace")
    output = sanitize(
        f"--- stdout\n{stdout}\n--- stderr\n{stderr}\n--- server\n{server_log}", host.who
    ).encode()
    error, limited = agent_error(run.agent, stdout)
    if limited:
        _abort(data, run, output, read_record(record_dir / EVENTS_FILE), work)
        raise CollectionStopError(f"{run.trace_id}: provider error ({error}); run not stored")

    after = workspace_sha256(workspace, git=run.scenario.git)
    raw = read_record(record_dir / EVENTS_FILE)
    hook_text = read_record(record_dir / HOOK_ERRORS).decode("utf-8", "replace")
    hook_errors = len(hook_text.splitlines())
    parsed = parse_raw(raw.decode("utf-8", "replace"), run.agent, host.who)
    if not parsed.recorder_loaded or hook_errors:
        # A harness defect, not agent behaviour: storing it would mark the run
        # collected and hide the gap. Keep the evidence, store nothing, stop.
        _abort(data, run, output, raw, work)
        problem = "recorder did not load" if not parsed.recorder_loaded else hook_text.strip()
        raise CollectionStopError(f"{run.trace_id}: {problem}; run not stored")
    raw_rel = Path("traces") / f"{run.trace_id}.jsonl"
    out_rel = Path("outputs") / f"{run.trace_id}.out"
    for relative, content in ((raw_rel, raw), (out_rel, output)):
        (data / relative).parent.mkdir(parents=True, exist_ok=True)
        (data / relative).write_bytes(content)

    cost = claude_cost(stdout) if run.agent is Agent.CLAUDE_CODE else None
    left = sanitize(json.dumps(home_files(home_dir, host)), host.who)
    record = RunRecord(
        trace_id=run.trace_id,
        agent=run.agent,
        scenario=run.scenario,
        attempt=run.attempt,
        collected_at=collected_at,
        workspace_sha256=before,
        final_sha256=after,
        raw_sha256=hashlib.sha256(raw).hexdigest(),
        raw_path=raw_rel.as_posix(),
        model=CLAUDE_MODEL if run.agent is Agent.CLAUDE_CODE else model,
        agent_version=version,
        rootsh_version=__version__,
        exit_code=exit_code,
        timed_out=exit_code is None,
        duration_ms=duration_ms,
        cost_usd=cost,
        hook_errors=hook_errors,
        agent_error=None if error is None else sanitize(error, host.who),
        output_path=out_rel.as_posix(),
        output_sha256=hashlib.sha256(output).hexdigest(),
        home_files_json=left,
    )
    store_run(conn, record, parsed)
    shutil.rmtree(work, onexc=_make_writable)
    status = "timeout" if exit_code is None else f"exit {exit_code}"
    flags = [
        *([f"${cost:.4f}"] if cost is not None else []),
        *(["workspace unchanged"] if before == after else []),
        *(["RECORDER NOT LOADED"] if not parsed.recorder_loaded else []),
        *([f"{hook_errors} HOOK ERRORS"] if hook_errors else []),
        *([f"agent error: {error[:80]}"] if error else []),
    ]
    return (
        f"{run.trace_id}: {status}, {len(parsed.events)} events, {parsed.denied} denied, "
        f"{parsed.rejected} rejected, {duration_ms / 1000:.1f} s" + "".join(f", {f}" for f in flags)
    )
