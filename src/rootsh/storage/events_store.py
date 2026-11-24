"""Idempotent ingest of AgentEvents into the runtime database."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime

from rootsh.analysis.events import Agent, AgentEvent, EventKind, Tool, canonical_json


@dataclass(frozen=True, slots=True)
class IngestResult:
    event_id: int
    seq: int
    turn_id: str | None
    duplicate: bool


def session_key(event: AgentEvent) -> str:
    return f"{event.agent}:{event.session_id}"


def _resolve_turn(conn: sqlite3.Connection, key: str, event: AgentEvent) -> str | None:
    """An event's own turn wins; an unlinked tool event joins the session's
    latest prompt (OpenCode tool events carry the assistant's message id)."""
    if event.turn_id is not None or event.kind is EventKind.PROMPT:
        return event.turn_id
    row = conn.execute(
        "SELECT turn_id FROM events WHERE session_key = ? AND kind = 'prompt' "
        "ORDER BY seq DESC LIMIT 1",
        (key,),
    ).fetchone()
    return None if row is None else row[0]


def ingest(conn: sqlite3.Connection, event: AgentEvent) -> IngestResult:
    key = session_key(event)
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(
            "INSERT INTO sessions (key, agent, session_id, cwd, first_seen_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT (key) DO UPDATE SET cwd = COALESCE(sessions.cwd, excluded.cwd)",
            (key, event.agent, event.session_id, event.cwd, event.received_at.isoformat()),
        )
        existing = conn.execute(
            "SELECT id, seq, turn_id FROM events WHERE session_key = ? AND raw_sha256 = ?",
            (key, event.raw_sha256),
        ).fetchone()
        if existing is not None:
            conn.execute("COMMIT")
            return IngestResult(existing[0], existing[1], existing[2], duplicate=True)

        turn_id = _resolve_turn(conn, key, event)
        (seq,) = conn.execute(
            "SELECT COALESCE(MAX(seq), 0) + 1 FROM events WHERE session_key = ?", (key,)
        ).fetchone()
        cursor = conn.execute(
            "INSERT INTO events (session_key, seq, kind, turn_id, tool_call_id, tool, tool_raw, "
            "args_json, text, truncated, cwd, received_at, raw_sha256) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                key,
                seq,
                event.kind,
                turn_id,
                event.tool_call_id,
                event.tool,
                event.tool_raw,
                canonical_json(dict(event.args)),
                event.text,
                int(event.truncated),
                event.cwd,
                event.received_at.isoformat(),
                event.raw_sha256,
            ),
        )
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    event_id = cursor.lastrowid
    if event_id is None:  # an INSERT always sets it; guard for the type checker
        raise RuntimeError("insert did not return a row id")
    return IngestResult(event_id, int(seq), turn_id, duplicate=False)


def load_session(conn: sqlite3.Connection, agent: Agent, session_id: str) -> list[AgentEvent]:
    """A session's stored events in order, as delivered (the daemon replays
    them after a restart to rebuild its provenance index)."""
    rows = conn.execute(
        "SELECT kind, turn_id, tool_call_id, tool, tool_raw, args_json, text, truncated, cwd, "
        "received_at, raw_sha256 FROM events WHERE session_key = ? ORDER BY seq",
        (f"{agent}:{session_id}",),
    ).fetchall()
    return [
        AgentEvent(
            agent=agent,
            kind=EventKind(kind),
            session_id=session_id,
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
