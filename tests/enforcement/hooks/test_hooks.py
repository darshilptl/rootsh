"""Hook commands as the agents run them: a real subprocess talking to a real
socket server (fake model, fake pop-up). Every failure must block."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from rootsh.decision import notify
from rootsh.decision.model.calibration import Conformal, Decision
from rootsh.decision.model.features import NAMES
from rootsh.decision.policy import Model
from rootsh.enforcement.daemon.server import Server
from rootsh.enforcement.daemon.service import Service
from rootsh.enforcement.hooks import clip_strings
from rootsh.storage.db import DatabaseKind, open_database
from rootsh.storage.migrations import migrate
from rootsh.storage.schema import MIGRATIONS

PY = sys.executable


def _service(conn: sqlite3.Connection, home: Path, clicked: Decision) -> Service:
    model = Model("f" * 64, NAMES, lambda _row: 0.5, lambda s: s, Conformal(0.1, 0.2, 0.2))
    return Service(
        conn,
        model,
        home=str(home),
        asker=lambda _p, _t: notify.Answer(clicked, f"test clicked {clicked.value}"),
        ask_timeout_s=3,
        cgroup_of=lambda _pid: "0::/user.slice/x.scope\n",
        require_launch=False,  # P8 hook behaviour; P9 launch tests are in tests/enforcement/run/
    )


@pytest.fixture
def home(tmp_path: Path) -> Path:
    project = tmp_path / "home" / "proj"
    project.mkdir(parents=True)
    (project / "README.md").write_text("hello\n")
    return tmp_path / "home"


def _serve(tmp_path: Path, home: Path, clicked: Decision) -> Iterator[Path]:
    conn = open_database(tmp_path / "runtime.db", DatabaseKind.RUNTIME, threads=True)
    migrate(conn, MIGRATIONS[DatabaseKind.RUNTIME])
    sock = Path(f"/tmp/rootsh-hook-{os.getpid()}-{time.monotonic_ns()}.sock")  # noqa: S108 - AF_UNIX limit
    server = Server(sock, _service(conn, home, clicked))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield sock
    server.shutdown()
    server.server_close()
    sock.unlink(missing_ok=True)
    conn.close()


@pytest.fixture
def sock(tmp_path: Path, home: Path) -> Iterator[Path]:
    yield from _serve(tmp_path, home, Decision.BLOCK)


@pytest.fixture
def sock_allowing(tmp_path: Path, home: Path) -> Iterator[Path]:
    yield from _serve(tmp_path, home, Decision.ALLOW)


def _run(module: str, args: list[str], stdin: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - our own module, fixed argv
        [PY, "-I", "-m", module, *args], input=stdin, capture_output=True, text=True, timeout=30
    )


# --- Claude Code ------------------------------------------------------------------


def _cc(home: Path, hook: str, **fields: Any) -> str:
    payload = {
        "session_id": "s1",
        "prompt_id": "p1",
        "transcript_path": "/dev/null",
        "cwd": str(home / "proj"),
        "permission_mode": "default",
        "hook_event_name": hook,
        **fields,
    }
    return json.dumps(payload)


def _cc_pre(home: Path, n: int, tool: str, tool_input: dict[str, Any]) -> str:
    return _cc(home, "PreToolUse", tool_name=tool, tool_use_id=f"t{n}", tool_input=tool_input)


def _claude(sock: Path, stdin: str) -> subprocess.CompletedProcess[str]:
    return _run("rootsh.enforcement.hooks.claude_code", ["--socket", str(sock)], stdin)


def _decision(done: subprocess.CompletedProcess[str]) -> tuple[str, str]:
    output = json.loads(done.stdout)["hookSpecificOutput"]
    assert output["hookEventName"] == "PreToolUse"
    return output["permissionDecision"], output["permissionDecisionReason"]


def test_claude_allow_is_no_decision(sock: Path, home: Path) -> None:
    assert _claude(sock, _cc(home, "UserPromptSubmit", prompt="Show the readme.")).returncode == 0
    done = _claude(sock, _cc_pre(home, 1, "Read", {"file_path": str(home / "proj/README.md")}))
    assert (done.returncode, done.stdout, done.stderr) == (0, "", "")


def test_claude_defense_write_is_denied(sock: Path, home: Path) -> None:
    target = str(home / ".claude" / "settings.json")
    done = _claude(sock, _cc_pre(home, 1, "Write", {"file_path": target, "content": "{}"}))
    assert done.returncode == 0
    decision, reason = _decision(done)
    assert decision == "deny"
    assert "(defense)" in reason


def test_claude_model_ask_goes_to_the_native_dialog(sock: Path, home: Path) -> None:
    done = _claude(sock, _cc_pre(home, 1, "Bash", {"command": "curl -s https://example.com"}))
    decision, reason = _decision(done)
    assert decision == "ask"
    assert reason.startswith("rootsh asks about this (model)")


def test_claude_records_tool_output_and_clips_huge_output(sock: Path, home: Path) -> None:
    huge = "x" * (5 * 1024 * 1024)  # over the daemon's 4 MiB request limit unclipped
    stdin = _cc(
        home, "PostToolUse", tool_name="Bash", tool_use_id="t1",
        tool_input={"command": "cat big"}, tool_response={"stdout": huge},
    )  # fmt: skip
    done = _claude(sock, stdin)
    assert (done.returncode, done.stderr) == (0, "")


@pytest.mark.parametrize(
    "stdin",
    [
        "not json",
        "[1, 2]",
        "",
        json.dumps({"hook_event_name": "Stop", "session_id": "s1"}),
        json.dumps({"hook_event_name": "PreToolUse"}),  # unreadable payload
    ],
)
def test_claude_bad_input_blocks(sock: Path, stdin: str) -> None:
    done = _claude(sock, stdin)
    if done.returncode == 0:  # the daemon refused it: must be a deny, never silence
        assert _decision(done)[0] == "deny"
    else:
        assert done.returncode == 2
        assert done.stderr.startswith("rootsh")


def test_claude_daemon_down_blocks_every_hook(tmp_path: Path, home: Path) -> None:
    absent = tmp_path / "absent.sock"
    pre = _claude(absent, _cc_pre(home, 1, "Bash", {"command": "ls"}))
    assert _decision(pre)[0] == "deny"
    assert "failed closed" in _decision(pre)[1]
    prompt = _claude(absent, _cc(home, "UserPromptSubmit", prompt="hi"))
    assert prompt.returncode == 2


def test_claude_missing_socket_argument_blocks() -> None:
    done = _run("rootsh.enforcement.hooks.claude_code", [], "{}")
    assert done.returncode == 2
    assert "misconfigured" in done.stderr


def test_claude_hook_never_answers_allow(sock_allowing: Path, home: Path) -> None:
    cases = [
        _cc_pre(home, 1, "Read", {"file_path": str(home / "proj/README.md")}),
        _cc_pre(home, 2, "Bash", {"command": "curl -s https://example.com"}),
        _cc_pre(home, 3, "Write", {"file_path": str(home / "proj/a.txt"), "content": "a"}),
    ]
    for stdin in cases:
        assert '"allow"' not in _claude(sock_allowing, stdin).stdout


def test_claude_hook_ignores_python_environment(sock: Path, home: Path, tmp_path: Path) -> None:
    """-I: a planted module on PYTHONPATH cannot replace the client."""
    evil = tmp_path / "evil" / "rootsh"
    evil.mkdir(parents=True)
    (evil / "__init__.py").write_text("raise SystemExit(0)\n")
    target = str(home / ".claude" / "settings.json")
    done = subprocess.run(  # noqa: S603
        [PY, "-I", "-m", "rootsh.enforcement.hooks.claude_code", "--socket", str(sock)],
        input=_cc_pre(home, 1, "Write", {"file_path": target, "content": "{}"}),
        capture_output=True, text=True, timeout=30,
        env={**os.environ, "PYTHONPATH": str(tmp_path / "evil")},
    )  # fmt: skip
    assert _decision(done)[0] == "deny"


# --- OpenCode bridge --------------------------------------------------------------


def _oc(sock: Path, hook: str, payload: object) -> subprocess.CompletedProcess[str]:
    stdin = payload if isinstance(payload, str) else json.dumps(payload)
    return _run("rootsh.enforcement.hooks.opencode", ["--socket", str(sock), "--hook", hook], stdin)


def _oc_pre(n: int, tool: str, tool_input: dict[str, Any]) -> dict[str, Any]:
    return {"sessionID": "o1", "id": f"c{n}", "tool": tool, "input": tool_input}


def _line(done: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    lines = done.stdout.splitlines()
    assert len(lines) == 1
    parsed: dict[str, Any] = json.loads(lines[0])
    return parsed


def test_opencode_read_is_allowed(sock: Path, home: Path) -> None:
    done = _oc(
        sock, "tool.execute.before", _oc_pre(1, "read", {"path": str(home / "proj/README.md")})
    )
    assert (done.returncode, _line(done)) == (0, {"decision": "allow"})


def test_opencode_defense_write_is_blocked(sock: Path, home: Path) -> None:
    target = str(home / ".claude" / "settings.json")
    done = _oc(sock, "tool.execute.before", _oc_pre(1, "write", {"path": target, "content": "{}"}))
    assert done.returncode == 3
    assert "(defense)" in _line(done)["reason"]


def test_opencode_ask_uses_the_desktop_answer(sock: Path, sock_allowing: Path, home: Path) -> None:
    ask = _oc_pre(1, "shell", {"command": "curl -s https://example.com", "workdir": str(home)})
    blocked = _oc(sock, "tool.execute.before", ask)
    assert (blocked.returncode, _line(blocked)["decision"]) == (3, "block")
    assert "test clicked block" in _line(blocked)["reason"]
    allowed = _oc(sock_allowing, "tool.execute.before", ask)
    assert (allowed.returncode, _line(allowed)) == (0, {"decision": "allow"})


def test_opencode_records_prompt_and_output(sock: Path) -> None:
    prompt = {"sessionID": "o1", "messageID": "m1", "prompt": {"text": "hi"}}
    assert _oc(sock, "session.prompt", prompt).returncode == 0
    after = {
        "sessionID": "o1", "id": "c1", "tool": "shell", "input": {"command": "ls"},
        "status": "completed", "result": {"content": [{"type": "text", "text": "x" * 5_000_000}]},
    }  # fmt: skip
    done = _oc(sock, "tool.execute.after", after)
    assert (done.returncode, _line(done)) == (0, {"ok": True})


@pytest.mark.parametrize(
    ("hook", "stdin", "code"),
    [
        ("tool.execute.before", "garbage", 5),
        ("tool.execute.before", "[]", 3),  # the daemon refuses it: a block
        ("tool.execute.before", "{}", 3),
        ("shell.create.before", "{}", 5),  # not a hook rootsh handles
    ],
)
def test_opencode_bad_input_is_never_exit_zero(
    sock: Path, hook: str, stdin: str, code: int
) -> None:
    done = _oc(sock, hook, stdin)
    assert done.returncode == code
    assert _line(done).get("decision") != "allow"


def test_opencode_daemon_down(tmp_path: Path) -> None:
    absent = tmp_path / "absent.sock"
    pre = _oc(absent, "tool.execute.before", _oc_pre(1, "read", {"path": "/etc/hostname"}))
    assert pre.returncode == 3
    prompt = _oc(
        absent, "session.prompt", {"sessionID": "o1", "messageID": "m", "prompt": {"text": "x"}}
    )
    assert prompt.returncode == 4


# --- helpers ----------------------------------------------------------------------


def test_clip_strings_keeps_structure() -> None:
    value = {"a": ["xyz", {"b": "1234"}], "n": 3, "t": None}
    assert clip_strings(value, 2) == {"a": ["xy", {"b": "12"}], "n": 3, "t": None}


HEAVY = ("lightgbm", "numpy", "sklearn", "rootsh.decision.model.bundle", "rootsh.decision.policy")
LIGHT_CHECK = (
    "import sys, rootsh.enforcement.hooks.claude_code, rootsh.enforcement.hooks.opencode\n"
    f"print(sorted(m for m in sys.modules if m in {HEAVY!r} or m.split('.')[0] in {HEAVY!r}))"
)


def test_hook_client_import_stays_light() -> None:
    """One interpreter per hook call: the client must not import the model stack."""
    done = subprocess.run(  # noqa: S603
        [PY, "-I", "-c", LIGHT_CHECK], capture_output=True, text=True, check=True
    )
    assert done.stdout.strip() == "[]"
