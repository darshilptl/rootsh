"""Launch trust and the two endpoints (design 09 §2-§3), and the launcher.

The daemon finds a caller's launch from its cgroup; these tests inject the
cgroup text (cgroup_of) except where a real socket or scope is the point.
"""

from __future__ import annotations

import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import uuid
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

import pytest

from rootsh import notify
from rootsh.daemon.peer import Peer, launch_ids, peer_cgroup
from rootsh.daemon.service import CLAMP_REASON, Endpoint, Service
from rootsh.events import Agent
from rootsh.model.calibration import Conformal, Decision
from rootsh.model.features import NAMES
from rootsh.policy import Model
from rootsh.run import LaunchError, k2_state, launch
from rootsh.run.sandbox import Host
from rootsh.storage.db import DatabaseKind, open_database
from rootsh.storage.migrations import migrate
from rootsh.storage.schema import MIGRATIONS

A, B = "a" * 32, "b" * 32
SLICE = "0::/user.slice/user-1000.slice/user@1000.service/app.slice/"
USER = SLICE + "term.scope\n"


def cgroup(launch_id: str) -> str:
    return f"{SLICE}rootsh-agent-{launch_id}.scope\n"


def peer(text: str) -> Peer:
    return Peer(os.getpid(), os.getuid(), os.getgid(), cgroup=text)


ME = peer(USER)


@pytest.fixture
def db(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    conn = open_database(tmp_path / "runtime.db", DatabaseKind.RUNTIME, threads=True)
    migrate(conn, MIGRATIONS[DatabaseKind.RUNTIME])
    yield conn
    conn.close()


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "home" / "proj"
    root.mkdir(parents=True)
    (root / "notes.md").write_text("hello\n")
    return root


def service(db: sqlite3.Connection, home: Path, risk: float = 0.5) -> Service:
    model = Model("f" * 64, NAMES, lambda _row: risk, lambda s: s, Conformal(0.1, 0.2, 0.2))
    return Service(
        db,
        model,
        home=str(home),
        asker=lambda _p, _t: notify.Answer(Decision.BLOCK, "test: no click"),
        ask_timeout_s=2,
    )


def register(svc: Service, launch_id: str, project: Path, agent: str = "claude_code") -> Any:
    body = {
        "op": "register",
        "launch": launch_id,
        "agent": agent,
        "project": str(project),
        "home": str(project.parent),
        "layers": ["bwrap", "K1", "K3"],
    }
    return svc.handle(body, ME, Endpoint.CONTROL)


def claude(hook: str, cwd: Path, session: str = "s1", **fields: Any) -> dict[str, Any]:
    payload = {"hook_event_name": hook, "session_id": session, "cwd": str(cwd), **fields}
    if hook == "UserPromptSubmit":
        payload.setdefault("prompt_id", "p1")
    return {"op": "event", "agent": "claude_code", "hook": hook, "payload": payload}


def pre(cwd: Path, command: str, session: str = "s1", n: int = 1) -> dict[str, Any]:
    return claude(
        "PreToolUse", cwd, session, tool_name="Bash", tool_input={"command": command},
        tool_use_id=f"toolu_{n}", prompt_id="p1",
    )  # fmt: skip


def oc_edit(path: Path, n: int = 1) -> dict[str, Any]:
    payload = {"sessionID": "o1", "id": f"c{n}", "tool": "edit", "input": {"path": str(path)}}
    return {"op": "event", "agent": "opencode", "hook": "tool.execute.before", "payload": payload}


# --- attribution -------------------------------------------------------------


def test_cgroup_names_the_launch() -> None:
    assert launch_ids(cgroup(A)) == {A}
    assert launch_ids(USER) == set()
    assert launch_ids(SLICE + "rootsh-agent-7.scope\n") == set()  # not a launch id


def test_unregistered_callers_are_blocked_and_not_recorded(
    db: sqlite3.Connection, project: Path
) -> None:
    svc = service(db, project.parent)
    for who in (ME, peer(cgroup(A))):  # a direct launch, and a scope nobody registered
        reply = svc.handle(pre(project, f"cat {project}/notes.md"), who, Endpoint.EVENT)
        assert (reply["decision"], reply["layer"]) == ("block", "failure")
        prompt = claude("UserPromptSubmit", project, prompt="hi")
        assert "not recorded" in svc.handle(prompt, who, Endpoint.EVENT)["error"]
    assert db.execute("SELECT count(*) FROM events").fetchone() == (0,)


def test_registered_launch_decides_and_logs_its_id(db: sqlite3.Connection, project: Path) -> None:
    svc = service(db, project.parent)
    assert register(svc, A, project) == {"ok": True}
    inside = peer(cgroup(A))
    assert svc.handle(claude("UserPromptSubmit", project, prompt="hi"), inside) == {"ok": True}
    reply = svc.handle(pre(project, f"cat {project}/notes.md"), inside, Endpoint.EVENT)
    assert (reply["decision"], reply["layer"]) == ("allow", "read_only")
    assert db.execute("SELECT launch_id FROM decisions").fetchone() == (A,)


def test_a_forged_launch_field_is_ignored(db: sqlite3.Connection, project: Path) -> None:
    svc = service(db, project.parent)
    register(svc, A, project)
    body = {**pre(project, f"cat {project}/notes.md"), "launch": A}
    reply = svc.handle(body, ME, Endpoint.EVENT)  # claims A, but its cgroup is not A's
    assert reply["decision"] == "block"


@pytest.fixture
def outside_tmp(tmp_path: Path) -> Iterator[Path]:
    """The temp root is allowed by design, so cwd tests need dirs outside it."""
    runtime = Path(os.environ.get("XDG_RUNTIME_DIR", ""))
    if not runtime.is_absolute() or not runtime.is_dir():
        pytest.skip("needs XDG_RUNTIME_DIR outside /tmp")
    root = runtime / f"rootsh-test-{uuid.uuid4().hex[:12]}"
    root.mkdir(mode=0o700)
    try:
        yield root
    finally:
        shutil.rmtree(root)


def _edit(cwd: Path, target: Path, n: int) -> dict[str, Any]:
    return claude(
        "PreToolUse", cwd, tool_name="Edit", tool_use_id=f"t{n}", prompt_id="p1",
        tool_input={"file_path": str(target), "old_string": "x", "new_string": "y"},
    )  # fmt: skip


def test_project_comes_from_the_launcher_not_the_hook_cwd(
    db: sqlite3.Connection, outside_tmp: Path
) -> None:
    project, elsewhere = outside_tmp / "proj", outside_tmp / "elsewhere"
    for d in (project, elsewhere):
        d.mkdir()
        (d / "x.md").write_text("x\n")
    svc = service(db, outside_tmp)
    register(svc, A, project)
    inside = peer(cgroup(A))
    svc.handle(claude("UserPromptSubmit", elsewhere, prompt="edit x"), inside)
    # Every hook cwd claims `elsewhere` is the project; only the launcher's counts.
    assert svc.handle(_edit(elsewhere, elsewhere / "x.md", 1), inside)["layer"] == "model"
    assert svc.handle(_edit(elsewhere, project / "x.md", 2), inside)["layer"] == "project_edit"


def test_opencode_gets_a_project_so_f2_can_fire(db: sqlite3.Connection, project: Path) -> None:
    svc = service(db, project.parent)
    register(svc, B, project, agent="opencode")
    reply = svc.handle(oc_edit(project / "notes.md"), peer(cgroup(B)), Endpoint.EVENT)
    assert (reply["decision"], reply["layer"]) == ("allow", "project_edit")


def test_agent_must_match_its_launch(db: sqlite3.Connection, project: Path) -> None:
    svc = service(db, project.parent)
    register(svc, B, project, agent="opencode")
    reply = svc.handle(pre(project, "true"), peer(cgroup(B)), Endpoint.EVENT)
    assert reply["decision"] == "block"
    assert "launch is opencode" in reply["reasons"][0]


def test_a_session_cannot_move_to_another_launch(db: sqlite3.Connection, project: Path) -> None:
    svc = service(db, project.parent)
    register(svc, A, project)
    register(svc, B, project)
    svc.handle(claude("UserPromptSubmit", project, prompt="hi"), peer(cgroup(A)))
    reply = svc.handle(pre(project, f"cat {project}/notes.md"), peer(cgroup(B)), Endpoint.EVENT)
    assert reply["decision"] == "block"
    assert "another launch" in reply["reasons"][0]


def test_unregister_ends_the_launch(db: sqlite3.Connection, project: Path) -> None:
    svc = service(db, project.parent)
    register(svc, A, project)
    assert svc.handle({"op": "unregister", "launch": A}, ME, Endpoint.CONTROL) == {"ok": True}
    reply = svc.handle(pre(project, f"cat {project}/notes.md"), peer(cgroup(A)), Endpoint.EVENT)
    assert reply["decision"] == "block"
    assert db.execute("SELECT ended_at IS NOT NULL FROM launches").fetchone() == (1,)


def test_model_allow_is_clamped_to_ask(db: sqlite3.Connection, project: Path) -> None:
    svc = service(db, project.parent, risk=0.0)
    register(svc, A, project)
    inside = peer(cgroup(A))
    svc.handle(claude("UserPromptSubmit", project, prompt=f"Delete {project}/notes.md"), inside)
    reply = svc.handle(pre(project, f"rm {project}/notes.md"), inside, Endpoint.EVENT)
    assert (reply["decision"], reply["layer"]) == ("ask", "model")
    assert CLAMP_REASON in reply["reasons"]


# --- endpoints ---------------------------------------------------------------


def test_each_socket_accepts_only_its_ops(db: sqlite3.Connection, project: Path) -> None:
    svc = service(db, project.parent)
    approve = {"op": "approve", "id": "x", "answer": "allow"}
    assert "not accepted on the event socket" in svc.handle(approve, ME, Endpoint.EVENT)["error"]
    reg = {"op": "register", "launch": A}
    assert "not accepted on the event socket" in svc.handle(reg, ME, Endpoint.EVENT)["error"]
    reply = svc.handle(pre(project, "true"), ME, Endpoint.CONTROL)
    assert reply["decision"] == "block"
    assert svc.handle({"op": "ping"}, ME, Endpoint.EVENT)["ok"] is True


def test_registration_is_refused_from_an_agent_scope(db: sqlite3.Connection, project: Path) -> None:
    svc = service(db, project.parent)
    register(svc, A, project)
    body = {"op": "register", "launch": B, "agent": "claude_code", "project": "/", "home": "/"}
    assert "refused" in svc.handle(body, peer(cgroup(A)), Endpoint.CONTROL)["error"]
    bad = {**body, "launch": "../" + "a" * 29}
    assert "hex" in svc.handle(bad, ME, Endpoint.CONTROL)["error"]
    assert "already" in register(svc, A, project)["error"]


def test_a_caller_that_exited_cannot_be_identified(tmp_path: Path) -> None:
    path = tmp_path / "s.sock"
    server = socket.socket(socket.AF_UNIX)
    server.bind(str(path))
    server.listen(1)
    child = subprocess.run(  # noqa: S603 - connects, then exits before the accept
        [
            sys.executable,
            "-c",
            f"import socket;s=socket.socket(socket.AF_UNIX);s.connect({str(path)!r})",
        ],
        check=True,
        timeout=10,
    )
    assert child.returncode == 0
    conn, _ = server.accept()
    pid = conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
    # Reaped: /proc/<pid> is gone. Recycled: the pidfd shows the caller exited.
    with pytest.raises((FileNotFoundError, ProcessLookupError)):
        peer_cgroup(conn, int.from_bytes(pid[:4], "little"))
    conn.close()
    server.close()


def test_a_live_caller_is_identified(tmp_path: Path) -> None:
    left, right = socket.socketpair(socket.AF_UNIX)
    with left, right:
        text = peer_cgroup(right, os.getpid())
    assert text == Path("/proc/self/cgroup").read_text(encoding="utf-8")


# --- launcher -----------------------------------------------------------------


def test_launcher_registers_runs_in_a_scope_and_always_unregisters(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sent: list[dict[str, Any]] = []

    def fake_request(_path: Path, body: dict[str, Any], *_rest: object) -> dict[str, Any]:
        sent.append(body)
        return {"ok": True}

    monkeypatch.setattr("rootsh.run.client.request", fake_request)
    home, project = tmp_path / "home", tmp_path / "proj"
    home.mkdir()
    project.mkdir()
    host = Host(
        home, os.getuid(), tmp_path, tmp_path / "e" / "s", tmp_path / "c" / "s", Path("/py"), ()
    )
    ran: list[Sequence[str]] = []

    def boom(argv: Sequence[str]) -> int:
        ran.append(argv)
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        launch(Agent.CLAUDE_CODE, project, ["-p", "hi"], host=host, env={}, runner=boom, k2=False)
    (argv,) = ran
    launch_id = sent[0]["launch"]
    assert argv[:5] == ["systemd-run", "--user", "--scope", "--quiet", "--collect"]
    assert f"--unit=rootsh-agent-{launch_id}" in argv
    assert "bwrap" in argv
    assert sent[0]["op"] == "register"
    assert sent[0]["project"] == str(project.resolve())
    assert sent[-1] == {"op": "unregister", "launch": launch_id}
    assert argv[-3:] == ["claude", "-p", "hi"]


def test_launcher_refuses_when_the_daemon_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("rootsh.run.client.request", lambda *_a: {"error": "nope"})
    host = Host(tmp_path, os.getuid(), tmp_path, tmp_path / "e/s", tmp_path / "c/s", Path("/p"), ())
    project = tmp_path / "proj"
    project.mkdir()
    ran: list[Sequence[str]] = []

    def record(argv: Sequence[str]) -> int:
        ran.append(argv)
        return 0

    with pytest.raises(LaunchError, match="refused"):
        launch(Agent.CLAUDE_CODE, project, [], host=host, env={}, runner=record, k2=False)
    assert ran == []


def test_k2_installed_but_not_loaded_refuses(tmp_path: Path) -> None:
    installed = tmp_path / "rootsh-profile"
    installed.write_text("x")
    with pytest.raises(LaunchError, match="not loaded"):
        k2_state(installed, lambda: False)
    assert k2_state(tmp_path / "absent", lambda: False) is False
    assert k2_state(tmp_path / "absent", lambda: True) is True


@pytest.mark.skipif(shutil.which("systemd-run") is None, reason="needs systemd-run")
def test_a_real_user_scope_is_found_from_proc() -> None:
    launch_id = uuid.uuid4().hex
    done = subprocess.run(  # noqa: S603 - fixed argv, a scope that only reads its own cgroup
        ["/usr/bin/systemd-run", "--user", "--scope", "--quiet", "--collect",
         f"--unit=rootsh-agent-{launch_id}", "--", "cat", "/proc/self/cgroup"],
        capture_output=True, text=True, timeout=30, check=False,
    )  # fmt: skip
    if done.returncode != 0:
        pytest.skip(f"no user systemd manager here: {done.stderr.strip()[:200]}")
    assert launch_ids(done.stdout) == {launch_id}


def test_run_options_after_the_agent_are_parsed_not_swallowed() -> None:
    from rootsh.cli.main import _parser

    args = _parser().parse_args(["run", "claude", "--project", "/p", "--", "-p", "hi"])
    assert (args.agent, args.project, args.agent_args) == ("claude", Path("/p"), ["-p", "hi"])
    args = _parser().parse_args(["run", "opencode", "--env", "K", "--", "--continue"])
    assert (args.project, args.env, args.agent_args) == (None, ["K"], ["--continue"])
