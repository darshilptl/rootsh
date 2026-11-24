"""The daemon's request handling, without the socket (design 07 §1-§4).

Thread-safe: the socket server calls `handle()` from one thread per
connection. One lock guards the database and the session index; a pending
desktop ask waits outside it.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from rootsh import notify
from rootsh.daemon.peer import Peer, approval_refusal, cgroup_text, launch_ids, read_cgroup
from rootsh.effects import Analysis, Context, analyze_event
from rootsh.events import Agent, AgentEvent, EventKind, EventParseError
from rootsh.hoststate import probe
from rootsh.model.calibration import Decision
from rootsh.model.features import paths
from rootsh.normalize import normalize
from rootsh.policy import Action, Layer, Model, Verdict, failure, safe_decide
from rootsh.provenance import Session
from rootsh.provenance.sources import event_context
from rootsh.storage.events_store import ingest, load_session, session_key

TMP_ROOT = "/tmp"  # noqa: S108 - a containment root compared, never opened
CHANNELS = frozenset({"native", "desktop"})
Asker = Callable[[notify.Popup, int], notify.Answer]
CLAMP_REASON = "model allow clamped to ask: the model's allow is not relied on (ADR-0018)"


class Endpoint(StrEnum):
    """Which socket a request arrived on (design 09 §2)."""

    EVENT = "event"  # visible inside the agent's sandbox
    CONTROL = "control"  # never visible there: approvals and launch registration
    ANY = "any"  # tests and P7/P8 callers without a split


EVENT_OPS = frozenset({"ping", "event"})
CONTROL_OPS = frozenset({"ping", "approve", "register", "unregister"})


@dataclass(frozen=True, slots=True)
class Launch:
    id: str
    agent: Agent
    project: str
    home: str
    layers: tuple[str, ...]


@dataclass(slots=True)
class _State:
    index: Session
    project: str | None
    launch: str | None = None
    asked: str | None = None


@dataclass(slots=True)
class _Pending:
    done: threading.Event = field(default_factory=threading.Event)
    answer: notify.Answer | None = None

    def resolve(self, answer: notify.Answer) -> None:
        if not self.done.is_set():  # first answer wins
            self.answer = answer
            self.done.set()


class Service:
    def __init__(
        self,
        conn: sqlite3.Connection,
        model: Model | None,
        *,
        model_error: str | None = None,
        home: str,
        asker: Asker = notify.ask,
        ask_timeout_s: int = notify.ASK_TIMEOUT_S,
        cgroup_of: Callable[[int], str] = read_cgroup,
        own_uid: int | None = None,
        require_launch: bool = True,
    ) -> None:
        self._conn = conn
        self._model = model
        self._model_error = model_error if model is None else None
        self._home = home
        self._asker = asker
        self._ask_timeout_s = ask_timeout_s
        self._cgroup_of = cgroup_of
        self._uid = os.getuid() if own_uid is None else own_uid
        self._lock = threading.Lock()
        self._sessions: dict[str, _State] = {}
        self._pending: dict[str, _Pending] = {}
        self._launches: dict[str, Launch] = {}
        self._require_launch = require_launch

    # --- requests -------------------------------------------------------------

    def handle(
        self, request: object, peer: Peer, endpoint: Endpoint = Endpoint.ANY
    ) -> dict[str, Any]:
        if not isinstance(request, dict):
            return {"error": "request must be a JSON object"}
        op = request.get("op")
        allowed = {Endpoint.EVENT: EVENT_OPS, Endpoint.CONTROL: CONTROL_OPS}.get(endpoint)
        if allowed is not None and op not in allowed:
            if op == "event":
                return _block_reply(failure("events are not accepted on the control socket"))
            return {"error": f"op {op!r} is not accepted on the {endpoint.value} socket"}
        if op == "ping":
            return {"ok": True, "model": self._model.sha256 if self._model else None}
        if op == "approve":
            return self._approve(request, peer)
        if op in ("register", "unregister"):
            return self._register(request, peer, op == "register")
        if op == "event":
            if peer.uid != self._uid:
                return _block_reply(failure(f"caller uid {peer.uid} is not the daemon's"))
            return self._event(request, peer)
        return {"error": f"unknown op {op!r}"}

    # --- launches (design 09 §3) ----------------------------------------------

    def _register(self, request: Mapping[str, Any], peer: Peer, add: bool) -> dict[str, Any]:
        refusal = approval_refusal(peer, self._uid, self._cgroup_of)
        if refusal is not None:  # same rule as approvals: never from an agent scope
            return {
                "error": refusal.replace("approver", "registrar").replace("approvals", "launches")
            }
        launch_id = request.get("launch")
        if not isinstance(launch_id, str) or len(launch_id) != 32 or not _is_hex(launch_id):
            return {"error": "launch must be 32 lowercase hex characters"}
        now = datetime.now(UTC).isoformat()
        if not add:
            with self._lock:
                gone = self._launches.pop(launch_id, None)
                self._conn.execute(
                    "UPDATE launches SET ended_at = ? WHERE id = ? AND ended_at IS NULL",
                    (now, launch_id),
                )
            return {"ok": True} if gone else {"error": "no such launch"}
        try:
            agent = Agent(str(request.get("agent")))
        except ValueError:
            return {"error": "unknown agent"}
        project, home = request.get("project"), request.get("home")
        layers = request.get("layers", [])
        if not (isinstance(project, str) and isinstance(home, str)):
            return {"error": "project and home are required"}
        if not (Path(project).is_absolute() and Path(home).is_absolute()):
            return {"error": "project and home must be absolute"}
        if not isinstance(layers, list) or not all(isinstance(x, str) for x in layers):
            return {"error": "layers must be a list of strings"}
        launch = Launch(launch_id, agent, project, home, tuple(layers))
        with self._lock:
            if launch_id in self._launches:
                return {"error": "launch already registered"}
            self._conn.execute(
                "INSERT INTO launches (id, agent, project, home, layers_json, registered_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (launch_id, agent.value, project, home, json.dumps(list(layers)), now),
            )
            self._launches[launch_id] = launch
        return {"ok": True}

    def _launch_of(self, peer: Peer) -> tuple[Launch | None, str]:
        """The caller's launch from its cgroup, or None with the reason."""
        try:
            ids = launch_ids(cgroup_text(peer, self._cgroup_of))
        except OSError as exc:
            return None, f"caller cgroup unreadable ({exc})"
        if len(ids) != 1:
            return None, "caller is not inside a `rootsh run` launch"
        (launch_id,) = ids
        with self._lock:
            launch = self._launches.get(launch_id)
        if launch is None:
            return None, f"launch {launch_id[:12]} is not registered"
        return launch, ""

    def _approve(self, request: Mapping[str, Any], peer: Peer) -> dict[str, Any]:
        refusal = approval_refusal(peer, self._uid, self._cgroup_of)
        if refusal is not None:
            return {"error": refusal}
        answer = request.get("answer")
        if answer not in ("allow", "block"):
            return {"error": "answer must be allow or block"}
        with self._lock:
            pending = self._pending.get(str(request.get("id")))
        if pending is None:
            return {"error": "no pending ask with that id"}
        pending.resolve(notify.Answer(Decision(answer), f"approved from pid {peer.pid}"))
        return {"ok": True}

    def _event(self, request: Mapping[str, Any], peer: Peer) -> dict[str, Any]:
        started = time.monotonic()
        hook = request.get("hook")
        channel = request.get("ask", "native")
        try:
            agent = Agent(str(request.get("agent")))
            event = normalize(agent, str(hook), request.get("payload"), datetime.now(UTC))
        except (ValueError, EventParseError) as exc:
            # A tool call rootsh cannot read is blocked; other events just fail.
            return _block_reply(failure(f"unreadable hook payload: {exc}"))
        launch: Launch | None = None
        if self._require_launch:
            launch, why = self._launch_of(peer)
            if launch is not None and launch.agent is not agent:
                why = f"event claims {agent.value}, launch is {launch.agent.value}"
                launch = None
            if launch is None:
                if event.kind is EventKind.TOOL_PRE:
                    return _block_reply(failure(why))
                return {"error": f"not recorded: {why}"}
        if event.kind is not EventKind.TOOL_PRE:
            with self._lock:
                refused = self._bound_elsewhere(event, launch)
                if refused is None:
                    self._record(event, launch)
            return {"error": f"not recorded: {refused}"} if refused else {"ok": True}
        if channel not in CHANNELS:
            return _block_reply(failure(f"unknown ask channel {channel!r}"))
        with self._lock:
            refused = self._bound_elsewhere(event, launch)
            if refused is not None:
                return _block_reply(failure(refused))
            state, seq, verdict = self._decide(event, launch)
        verdict = _clamped(verdict)
        decision_id = uuid.uuid4().hex
        answer: notify.Answer | None = None
        if verdict.decision is Decision.ASK and channel == "desktop":
            answer = self._ask_desktop(decision_id, state.asked, event, verdict)
        final = answer.decision if answer else verdict.decision
        with self._lock:
            self._log(decision_id, event, seq, verdict, channel, answer, started, state.launch)
        return {
            "decision": final.value,
            "layer": verdict.layer.value,
            "reasons": list(verdict.reasons) + ([answer.detail] if answer else []),
            "id": decision_id,
        }

    # --- sessions -------------------------------------------------------------

    def _bound_elsewhere(self, event: AgentEvent, launch: Launch | None) -> str | None:
        """A session belongs to the launch that first sent it; another launch
        sending the same session id is refused (a cross-launch forgery)."""
        state = self._sessions.get(session_key(event))
        if state is None or launch is None or state.launch in (None, launch.id):
            return None
        return "session belongs to another launch"

    def _state(self, event: AgentEvent, launch: Launch | None = None) -> _State:
        key = session_key(event)
        state = self._sessions.get(key)
        if state is None:
            earlier = load_session(self._conn, event.agent, event.session_id)
            if launch is not None:  # the launcher's project, never the hook's cwd
                project: str | None = launch.project
            else:
                project = next((e.cwd for e in earlier if e.cwd), None) or event.cwd
            state = _State(
                Session(Context(project, self._home)), project, launch.id if launch else None
            )
            for old in earlier:
                self._index(state, old)
            self._sessions[key] = state
        return state

    @staticmethod
    def _index(state: _State, event: AgentEvent) -> None:
        state.index.add(event)
        if event.kind is EventKind.PROMPT:
            state.asked = event.text

    def _record(self, event: AgentEvent, launch: Launch | None = None) -> tuple[_State, int]:
        state = self._state(event, launch)
        stored = ingest(self._conn, event)
        if not stored.duplicate:
            self._index(state, event)
        return state, stored.seq

    def _decide(
        self, event: AgentEvent, launch: Launch | None = None
    ) -> tuple[_State, int, Verdict]:
        state = self._state(event, launch)
        stored = ingest(self._conn, event)
        if self._model_error is not None:
            verdict = failure(self._model_error)
        else:
            verdict = safe_decide(lambda: self._action(state, event), self._model)
        if not stored.duplicate:
            self._index(state, event)
        return state, stored.seq, verdict

    def _action(self, state: _State, event: AgentEvent) -> Action:
        base = Context(state.project, self._home)
        analysis = analyze_event(event, event_context(event, base))
        roots = (state.project, TMP_ROOT) if state.project else (TMP_ROOT,)
        return Action(
            analysis,
            state.index.trace(event),
            roots,
            self._home,
            _probed(analysis, state.project, self._home),
        )

    # --- asking ---------------------------------------------------------------

    def _ask_desktop(
        self, decision_id: str, asked: str | None, event: AgentEvent, verdict: Verdict
    ) -> notify.Answer:
        pending = _Pending()
        with self._lock:
            self._pending[decision_id] = pending
        popup = notify.content(asked, _doing(event), verdict.reasons)

        def run() -> None:
            try:
                pending.resolve(self._asker(popup, self._ask_timeout_s))
            except Exception as exc:  # noqa: BLE001 - a broken pop-up is a block
                pending.resolve(notify.Answer(Decision.BLOCK, f"pop-up failed: {exc}"))

        threading.Thread(target=run, daemon=True).start()
        try:
            if not pending.done.wait(self._ask_timeout_s + 5):
                pending.resolve(notify.Answer(Decision.BLOCK, "no answer in time"))
        finally:
            with self._lock:
                self._pending.pop(decision_id, None)
        assert pending.answer is not None  # resolve() always sets it before done
        return pending.answer

    def pending_ids(self) -> list[str]:
        with self._lock:
            return list(self._pending)

    # --- log ------------------------------------------------------------------

    def _log(
        self,
        decision_id: str,
        event: AgentEvent,
        seq: int,
        verdict: Verdict,
        channel: str,
        answer: notify.Answer | None,
        started: float,
        launch_id: str | None,
    ) -> None:
        self._conn.execute(
            "INSERT INTO decisions (id, session_key, seq, decision, layer, reasons_json, "
            "features_json, risk, model_sha256, channel, answer, answer_detail, decided_at, "
            "latency_ms, launch_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                decision_id,
                session_key(event),
                seq,
                verdict.decision.value,
                verdict.layer.value,
                json.dumps(list(verdict.reasons)),
                json.dumps(dict(verdict.features), sort_keys=True),
                verdict.risk,
                self._model.sha256 if self._model else None,
                channel,
                answer.decision.value if answer else None,
                answer.detail if answer else None,
                datetime.now(UTC).isoformat(),
                (time.monotonic() - started) * 1000,
                launch_id,
            ),
        )


def _clamped(verdict: Verdict) -> Verdict:
    """ADR-0018 §1: a model `allow` becomes `ask`; fast paths are unchanged."""
    if verdict.layer is Layer.MODEL and verdict.decision is Decision.ALLOW:
        return replace(verdict, decision=Decision.ASK, reasons=(*verdict.reasons, CLAMP_REASON))
    return verdict


def _is_hex(text: str) -> bool:
    return all(c in "0123456789abcdef" for c in text)


def _probed(analysis: Analysis, project: str | None, home: str) -> dict[str, frozenset[str]]:
    targets = [p for e in analysis.effects for p in paths(e) if "*" not in p]
    states = probe(targets, project_root=project, home=home)
    return {path: state.sensitivity for path, state in states.items()}


def _doing(event: AgentEvent) -> str:
    command = event.args.get("command")
    if isinstance(command, str):
        return command
    return f"{event.tool_raw} {json.dumps(dict(event.args), sort_keys=True)}"


def _block_reply(verdict: Verdict) -> dict[str, Any]:
    return {
        "decision": verdict.decision.value,
        "layer": verdict.layer.value,
        "reasons": list(verdict.reasons),
    }
