from __future__ import annotations

import json
import os
import shutil
import socket
import sqlite3
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from rootsh import notify
from rootsh.daemon import client, load_model
from rootsh.daemon.peer import Peer, approval_refusal, in_agent_scope, peer_of
from rootsh.daemon.server import Server
from rootsh.daemon.service import Service
from rootsh.model.calibration import Conformal, Decision
from rootsh.model.features import NAMES
from rootsh.policy import Model
from rootsh.storage.db import DatabaseKind, open_database
from rootsh.storage.migrations import migrate
from rootsh.storage.schema import MIGRATIONS

UID = os.getuid()
ME = Peer(os.getpid(), UID, os.getgid())
USER_CGROUP = "0::/user.slice/user-1000.slice/user@1000.service/app.slice/term.scope\n"
AGENT_CGROUP = "0::/user.slice/user-1000.slice/user@1000.service/app.slice/rootsh-agent-7.scope\n"


def _model(risk: float) -> Model:
    return Model("f" * 64, NAMES, lambda _row: risk, lambda s: s, Conformal(0.1, 0.2, 0.2))


def _service(
    conn: sqlite3.Connection,
    home: Path,
    *,
    risk: float = 0.5,
    asker: Callable[[notify.Popup, int], notify.Answer] | None = None,
    cgroup: str = USER_CGROUP,
    model_error: str | None = None,
    timeout: int = 2,
) -> Service:
    return Service(
        conn,
        None if model_error else _model(risk),
        model_error=model_error,
        home=str(home),
        asker=asker or (lambda _p, _t: notify.Answer(Decision.BLOCK, "test: no click")),
        ask_timeout_s=timeout,
        cgroup_of=lambda _pid: cgroup,
    )


def _claude(hook: str, project: Path, **fields: Any) -> dict[str, Any]:
    payload = {"hook_event_name": hook, "session_id": "s1", "cwd": str(project), **fields}
    if hook == "UserPromptSubmit":
        payload.setdefault("prompt_id", "p1")
    return {"op": "event", "agent": "claude_code", "hook": hook, "payload": payload}


def _pre(project: Path, command: str, n: int = 1, ask: str = "native") -> dict[str, Any]:
    body = _claude(
        "PreToolUse", project, tool_name="Bash", tool_input={"command": command},
        tool_use_id=f"toolu_{n}", prompt_id="p1",
    )  # fmt: skip
    return {**body, "ask": ask}


@pytest.fixture
def runtime_db(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    """The daemon's connection is shared across its request threads."""
    conn = open_database(tmp_path / "runtime.db", DatabaseKind.RUNTIME, threads=True)
    migrate(conn, MIGRATIONS[DatabaseKind.RUNTIME])
    yield conn
    conn.close()


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "home" / "proj"
    root.mkdir(parents=True)
    (root / "README.md").write_text("hello\n")
    return root


def _prompted(svc: Service, project: Path, text: str = "Tidy the notes.") -> None:
    assert svc.handle(_claude("UserPromptSubmit", project, prompt=text), ME) == {"ok": True}


# --- decisions --------------------------------------------------------------


def test_a_read_is_allowed_and_logged(runtime_db: sqlite3.Connection, project: Path) -> None:
    svc = _service(runtime_db, project.parent)
    _prompted(svc, project)
    reply = svc.handle(_pre(project, f"cat {project}/README.md"), ME)
    assert (reply["decision"], reply["layer"]) == ("allow", "read_only")
    row = runtime_db.execute("SELECT decision, layer, seq, channel FROM decisions").fetchone()
    assert row == ("allow", "read_only", 2, "native")


def test_native_ask_is_returned_to_the_agent(runtime_db: sqlite3.Connection, project: Path) -> None:
    svc = _service(runtime_db, project.parent, risk=0.5)
    _prompted(svc, project)
    reply = svc.handle(_pre(project, f"rm {project}/README.md"), ME)
    assert (reply["decision"], reply["layer"]) == ("ask", "model")


def test_desktop_ask_shows_the_popup_and_uses_the_answer(
    runtime_db: sqlite3.Connection, project: Path
) -> None:
    shown: list[notify.Popup] = []

    def click_allow(popup: notify.Popup, _timeout: int) -> notify.Answer:
        shown.append(popup)
        return notify.Answer(Decision.ALLOW, "user clicked Allow")

    svc = _service(runtime_db, project.parent, asker=click_allow)
    _prompted(svc, project, "Remove the readme please.")
    reply = svc.handle(_pre(project, f"rm {project}/README.md", ask="desktop"), ME)
    assert reply["decision"] == "allow"
    assert shown[0].body.startswith("You asked: Remove the readme please.\nAgent is doing: rm ")
    row = runtime_db.execute("SELECT decision, answer, channel FROM decisions").fetchone()
    assert row == ("ask", "allow", "desktop")


def test_a_replayed_session_keeps_its_provenance(
    runtime_db: sqlite3.Connection, project: Path
) -> None:
    _prompted(_service(runtime_db, project.parent), project, f"Delete {project}/README.md.")
    restarted = _service(runtime_db, project.parent, risk=0.0)  # a daemon restart
    reply = restarted.handle(_pre(project, f"rm {project}/README.md"), ME)
    assert reply["decision"] == "allow"
    features = json.loads(runtime_db.execute("SELECT features_json FROM decisions").fetchone()[0])
    assert features["user_backed"] == 1.0


# --- errors -> block (gate 1) -------------------------------------------------


def test_tampered_or_missing_bundle_blocks_everything(
    runtime_db: sqlite3.Connection, project: Path, tmp_path: Path
) -> None:
    model, error = load_model(tmp_path / "no-bundle")
    assert model is None
    assert error is not None
    svc = _service(runtime_db, project.parent, model_error=error)
    reply = svc.handle(_pre(project, f"cat {project}/README.md"), ME)
    assert (reply["decision"], reply["layer"]) == ("block", "failure")


def test_a_tampered_real_bundle_is_refused(tmp_path: Path) -> None:
    source = Path("data/models")
    bundles = sorted(source.glob("p6-*")) if source.is_dir() else []
    if not bundles:
        pytest.skip("no local P6 bundle (data/ is git-ignored)")
    copy = tmp_path / "bundle"
    shutil.copytree(bundles[0], copy)
    assert load_model(copy)[0] is not None
    with (copy / "full.model").open("a") as model_file:
        model_file.write("\n")
    model, error = load_model(copy)
    assert model is None
    assert error is not None
    assert "tampered" in error


@pytest.mark.parametrize(
    "request_body",
    [
        {"op": "event", "agent": "claude_code", "hook": "PreToolUse", "payload": {"x": 1}},
        {"op": "event", "agent": "nope", "hook": "PreToolUse", "payload": {}},
        {"op": "event", "agent": "claude_code", "hook": "PreToolUse", "payload": "junk"},
    ],
)
def test_unreadable_tool_calls_block(
    runtime_db: sqlite3.Connection, project: Path, request_body: dict[str, Any]
) -> None:
    reply = _service(runtime_db, project.parent).handle(request_body, ME)
    assert (reply["decision"], reply["layer"]) == ("block", "failure")


def test_unknown_channel_blocks(runtime_db: sqlite3.Connection, project: Path) -> None:
    reply = _service(runtime_db, project.parent).handle(_pre(project, "ls", ask="smoke"), ME)
    assert reply["decision"] == "block"


def test_a_crashing_popup_blocks(runtime_db: sqlite3.Connection, project: Path) -> None:
    def crash(_p: notify.Popup, _t: int) -> notify.Answer:
        raise RuntimeError("no session bus")

    svc = _service(runtime_db, project.parent, asker=crash)
    reply = svc.handle(_pre(project, f"rm {project}/README.md", ask="desktop"), ME)
    assert reply["decision"] == "block"
    assert "no session bus" in reply["reasons"][-1]


def test_an_unanswered_popup_blocks(runtime_db: sqlite3.Connection, project: Path) -> None:
    def hang(_p: notify.Popup, _t: int) -> notify.Answer:
        time.sleep(30)
        return notify.Answer(Decision.ALLOW, "too late")

    svc = _service(runtime_db, project.parent, asker=hang, timeout=0)
    started = time.monotonic()
    reply = svc.handle(_pre(project, f"rm {project}/README.md", ask="desktop"), ME)
    assert reply["decision"] == "block"
    assert time.monotonic() - started < 10


def test_another_uid_cannot_submit_events(runtime_db: sqlite3.Connection, project: Path) -> None:
    other = Peer(1, UID + 1, 0)
    reply = _service(runtime_db, project.parent).handle(_pre(project, "ls"), other)
    assert reply["decision"] == "block"


def test_client_blocks_when_the_daemon_is_down(tmp_path: Path) -> None:
    reply = client.decide(tmp_path / "absent.sock", "claude_code", "PreToolUse", {})
    assert (reply["decision"], reply["layer"]) == ("block", "failure")


def _fake_daemon(path: Path, answer: bytes | None) -> threading.Thread:
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(path))
    listener.listen(1)

    def serve() -> None:
        conn, _ = listener.accept()
        conn.recv(65536)
        if answer is None:
            time.sleep(client.TIMEOUT_S + 2)  # never answers
        else:
            conn.sendall(answer)
        conn.close()
        listener.close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    return thread


@pytest.mark.parametrize(
    "answer",
    [
        None,
        b"not json\n",
        b'{"decision": "maybe", "reasons": []}\n',
        b'{"decision": "allow"}\n',
        b"[1]\n",
        b"",
    ],
)
def test_client_blocks_on_timeout_or_malformed_reply(tmp_path: Path, answer: bytes | None) -> None:
    path = tmp_path / "d.sock"
    _fake_daemon(path, answer)
    reply = client.decide(path, "claude_code", "PreToolUse", {})
    assert (reply["decision"], reply["layer"]) == ("block", "failure")


# --- who may approve (gate 2) ----------------------------------------------


def test_cgroup_scope_detection() -> None:
    assert in_agent_scope(AGENT_CGROUP)
    assert in_agent_scope(AGENT_CGROUP.replace(".scope", ".scope/child"))
    assert not in_agent_scope(USER_CGROUP)


def test_approval_refusals() -> None:
    assert approval_refusal(ME, UID, lambda _p: USER_CGROUP) is None
    assert approval_refusal(ME, UID, lambda _p: AGENT_CGROUP) is not None
    assert approval_refusal(Peer(1, UID + 1, 0), UID, lambda _p: USER_CGROUP) is not None
    assert approval_refusal(ME, UID, lambda _p: "") is not None

    def gone(_pid: int) -> str:
        raise FileNotFoundError("/proc/1/cgroup")

    assert approval_refusal(ME, UID, gone) is not None


def _ask_in_background(
    svc: Service, project: Path
) -> tuple[threading.Thread, list[dict[str, Any]]]:
    replies: list[dict[str, Any]] = []
    thread = threading.Thread(
        target=lambda: replies.append(
            svc.handle(_pre(project, f"rm {project}/README.md", ask="desktop"), ME)
        )
    )
    thread.start()
    for _ in range(200):
        if svc.pending_ids():
            break
        time.sleep(0.01)
    return thread, replies


def _never_clicked(_p: notify.Popup, timeout: int) -> notify.Answer:
    time.sleep(timeout)
    return notify.Answer(Decision.BLOCK, "no click")


def test_agent_scope_cannot_approve_but_the_user_can(
    runtime_db: sqlite3.Connection, project: Path
) -> None:
    svc = _service(runtime_db, project.parent, asker=_never_clicked, cgroup=AGENT_CGROUP, timeout=3)
    thread, replies = _ask_in_background(svc, project)
    (pending,) = svc.pending_ids()
    refused = svc.handle({"op": "approve", "id": pending, "answer": "allow"}, ME)
    assert "agent" in refused["error"]
    thread.join()
    assert replies[0]["decision"] == "block"

    user = _service(runtime_db, project.parent, asker=_never_clicked, timeout=3)
    thread, replies = _ask_in_background(user, project)
    (pending,) = user.pending_ids()
    assert user.handle({"op": "approve", "id": pending, "answer": "allow"}, ME) == {"ok": True}
    thread.join()
    assert replies[0]["decision"] == "allow"


@pytest.fixture
def served(runtime_db: sqlite3.Connection, project: Path) -> Iterator[tuple[Path, Service]]:
    sock = Path(f"/tmp/rootsh-test-{os.getpid()}-{time.monotonic_ns()}.sock")  # noqa: S108 - AF_UNIX path limit
    svc = _service(runtime_db, project.parent, asker=_never_clicked, timeout=3)
    server = Server(sock, svc)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield sock, svc
    server.shutdown()
    server.server_close()
    sock.unlink(missing_ok=True)


def test_socket_round_trip_and_peer_credentials(
    served: tuple[Path, Service], project: Path
) -> None:
    sock, _ = served
    assert oct(sock.stat().st_mode & 0o777) == "0o600"
    reply = client.decide(sock, "claude_code", "PreToolUse", _pre(project, "ls")["payload"])
    assert reply["decision"] == "allow"
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as raw:
        raw.connect(str(sock))
        assert peer_of(raw).pid > 0  # the server's pid, as the kernel reports it


def test_approval_over_the_real_socket_checks_the_real_cgroup(
    served: tuple[Path, Service], project: Path, runtime_db: sqlite3.Connection
) -> None:
    sock, svc = served
    # This test process is not in an agent scope, so the real /proc check lets it approve.
    real = Service(
        runtime_db, _model(0.5), home=str(project.parent), asker=_never_clicked, ask_timeout_s=3
    )
    svc_server = Server(sock.with_suffix(".2"), real)
    thread = threading.Thread(target=svc_server.serve_forever, daemon=True)
    thread.start()
    try:
        asking, replies = _ask_in_background(real, project)
        (pending,) = real.pending_ids()
        body = {"op": "approve", "id": pending, "answer": "allow"}
        assert client.request(sock.with_suffix(".2"), body) == {"ok": True}
        asking.join()
        assert replies[0]["decision"] == "allow"
    finally:
        svc_server.shutdown()
        svc_server.server_close()
        sock.with_suffix(".2").unlink(missing_ok=True)
    assert svc.pending_ids() == []


def test_oversized_or_garbage_requests_get_an_error(served: tuple[Path, Service]) -> None:
    sock, _ = served
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as raw:
        raw.connect(str(sock))
        raw.sendall(b"{not json\n")
        assert b"error" in raw.recv(4096)
