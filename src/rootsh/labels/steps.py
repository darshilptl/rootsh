"""Steps in context, rebuilt from the research DB (docs/design/05-labelling.md §2).

A step is one `tool_pre` event plus everything before it in its trace. All
paths in the gold DB are already sanitized: the workspace is `/work` and the
home `/home/u`.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime

from rootsh.collect.sandbox import WORKDIR
from rootsh.collect.store import SANITIZED_HOME
from rootsh.effects import Analysis, Context, analyze_event
from rootsh.events import Agent, AgentEvent, EventKind, Tool
from rootsh.provenance import Provenance, Session
from rootsh.provenance.sources import event_context

BASE = Context(WORKDIR, SANITIZED_HOME)


@dataclass(frozen=True, slots=True)
class TraceInfo:
    id: str
    agent: Agent
    repository: str
    scenario: str


@dataclass(frozen=True, slots=True)
class Step:
    trace: TraceInfo
    seq: int  # 1-based, as stored
    action: AgentEvent
    history: tuple[AgentEvent, ...]
    analysis: Analysis
    provenance: Provenance
    prior_programs: frozenset[str]  # every program an earlier step in the trace ran

    @property
    def key(self) -> tuple[str, int]:
        return (self.trace.id, self.seq)

    @property
    def sha256(self) -> str:
        """Hash of what the labeller is shown. The scenario is not part of it."""
        return hashlib.sha256(_canonical(self).encode("utf-8")).hexdigest()


def _event_view(event: AgentEvent) -> dict[str, object]:
    return {
        "kind": event.kind.value,
        "tool": event.tool_raw,
        "args": event.args,
        "text": event.text,
        "cwd": event.cwd,
    }


def _canonical(step: Step) -> str:
    view = {
        "agent": step.trace.agent.value,
        "history": [_event_view(e) for e in step.history],
        "action": _event_view(step.action),
        "effects": [
            [e.kind.value, e.target, e.detail, e.recursive, e.dynamic, e.glob, e.dest]
            for e in step.analysis.effects
        ],
        "opaque": list(step.analysis.opaque),
        "provenance": [
            [a.target, a.kind.value, sorted(s.value for s in a.best)]
            for a in step.provenance.arguments
        ],
    }
    return json.dumps(view, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=dict)


def load_events(conn: sqlite3.Connection, trace_id: str, agent: Agent) -> list[AgentEvent]:
    rows = conn.execute(
        "SELECT kind, turn_id, tool_call_id, tool, tool_raw, args_json, text, truncated, cwd, "
        "received_at, raw_sha256 FROM trace_events WHERE trace_id = ? ORDER BY seq",
        (trace_id,),
    ).fetchall()
    return [
        AgentEvent(
            agent=agent,
            kind=EventKind(kind),
            session_id=trace_id,
            turn_id=turn,
            tool_call_id=call,
            tool=None if tool is None else Tool(tool),
            tool_raw=raw_tool,
            args=json.loads(args),
            text=text,
            truncated=bool(truncated),
            cwd=cwd,
            received_at=datetime.fromisoformat(at),
            raw_sha256=digest,
        )
        for kind, turn, call, tool, raw_tool, args, text, truncated, cwd, at, digest in rows
    ]


def traces(conn: sqlite3.Connection) -> list[TraceInfo]:
    rows = conn.execute("SELECT id, agent, task_id, scenario FROM traces ORDER BY id").fetchall()
    return [TraceInfo(tid, Agent(agent), repo, scenario) for tid, agent, repo, scenario in rows]


def trace_steps(conn: sqlite3.Connection, trace: TraceInfo) -> Iterator[Step]:
    events = load_events(conn, trace.id, trace.agent)
    session = Session(BASE)
    programs: set[str] = set()
    for index, event in enumerate(events):
        if event.kind is EventKind.TOOL_PRE and event.tool is not None:
            analysis = analyze_event(event, event_context(event, BASE))
            yield Step(
                trace,
                index + 1,
                event,
                tuple(events[:index]),
                analysis,
                session.trace(event),
                frozenset(programs),
            )
            programs.update(analysis.programs)
        session.add(event)


def all_steps(conn: sqlite3.Connection) -> Iterator[Step]:
    for trace in traces(conn):
        yield from trace_steps(conn, trace)
