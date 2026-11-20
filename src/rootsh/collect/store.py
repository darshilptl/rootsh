"""Turning a run's raw JSONL into research-DB rows (design 04 §7)."""

from __future__ import annotations

import hashlib
import json
import os
import pwd
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from rootsh.collect.scenario import Scenario
from rootsh.events import Agent, AgentEvent, EventKind, EventParseError, canonical_json
from rootsh.normalize import normalize

DENY_LINE = "rootsh.deny"  # written by the recorders, not by an agent hook
LOADED_LINE = "rootsh.loaded"  # the OpenCode plugin's first line

SANITIZED_HOME = "/home/u"
SANITIZED_USER = "u"
SANITIZED_NAME = "U"


@dataclass(frozen=True, slots=True)
class Identity:
    """What identifies the author in a trace: removed before anything is stored."""

    home: str
    user: str
    names: tuple[str, ...] = ()  # e.g. the full name from /etc/passwd (GECOS)

    @classmethod
    def current(cls) -> Identity:
        """The user running rootsh: the author, for a collection or a rebuild."""
        entry = pwd.getpwuid(os.getuid())
        home = os.path.expanduser("~")
        gecos = entry.pw_gecos.split(",", 1)[0].strip()
        return cls(home, os.path.basename(home), tuple(n for n in (gecos,) if len(n) >= 3))


def sanitize(text: str, who: Identity) -> str:
    """Remove the author's home path, user name and full name (P3 rule)."""
    text = text.replace(who.home, SANITIZED_HOME)
    for name in who.names:
        if name:
            text = text.replace(name, SANITIZED_NAME)
    # A raw line is JSON, where `\nalice` has no word boundary before the name:
    # a JSON escape (\n, \t, \r) counts as one (P4: `whoami` output leaked).
    return re.sub(rf"(?:(?<=\\[nrt])|(?<!\w)){re.escape(who.user)}(?!\w)", SANITIZED_USER, text)


@dataclass(frozen=True, slots=True)
class ParsedTrace:
    events: tuple[AgentEvent, ...]
    denied: int
    rejected: int
    recorder_loaded: bool  # OpenCode: the plugin's marker; Claude Code: a prompt was recorded


def parse_raw(raw: str, agent: Agent, who: Identity) -> ParsedTrace:
    """Normalize each recorded line. Never raises on a bad line: it is counted."""
    events: list[AgentEvent] = []
    denied = rejected = 0
    loaded = False
    for line in raw.splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(sanitize(line, who))
            hook, at, payload = record["hook"], record["at"], record["payload"]
            if hook == DENY_LINE:
                denied += 1
                continue
            if hook == LOADED_LINE:
                loaded = True
                continue
            events.append(normalize(agent, hook, payload, datetime.fromisoformat(at)))
        except (json.JSONDecodeError, KeyError, TypeError, ValueError, EventParseError):
            # EventParseError is a ValueError; listed for the reader.
            rejected += 1
    if agent is Agent.CLAUDE_CODE:
        loaded = any(e.kind is EventKind.PROMPT for e in events)
    return ParsedTrace(tuple(events), denied, rejected, loaded)


@dataclass(frozen=True, slots=True)
class RunRecord:
    trace_id: str
    agent: Agent
    scenario: Scenario
    attempt: int
    collected_at: datetime
    workspace_sha256: str
    final_sha256: str
    raw_sha256: str
    raw_path: str
    model: str
    agent_version: str
    rootsh_version: str
    exit_code: int | None
    timed_out: bool
    duration_ms: int
    cost_usd: float | None
    hook_errors: int
    agent_error: str | None
    output_path: str
    output_sha256: str
    home_files_json: str


def trace_id(scenario_id: str, agent: Agent, attempt: int) -> str:
    return f"{scenario_id}.{agent}.{attempt}"


def is_collected(conn: sqlite3.Connection, tid: str) -> bool:
    return conn.execute("SELECT 1 FROM traces WHERE id = ?", (tid,)).fetchone() is not None


def claude_spend(conn: sqlite3.Connection) -> float:
    (total,) = conn.execute(
        "SELECT COALESCE(SUM(r.cost_usd), 0) FROM trace_runs r "
        "JOIN traces t ON t.id = r.trace_id WHERE t.agent = 'claude_code'"
    ).fetchone()
    return float(total)


_INSERT_EVENT = (
    "INSERT INTO trace_events (trace_id, seq, kind, turn_id, tool_call_id, tool, tool_raw, "
    "args_json, text, truncated, cwd, received_at, raw_sha256) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)
_SELECT_EVENTS = (
    "SELECT trace_id, seq, kind, turn_id, tool_call_id, tool, tool_raw, args_json, text, "
    "truncated, cwd, received_at, raw_sha256 FROM trace_events WHERE trace_id = ? ORDER BY seq"
)

type EventRow = tuple[object, ...]


def event_rows(tid: str, events: tuple[AgentEvent, ...]) -> list[EventRow]:
    return [
        (
            tid,
            seq,
            str(e.kind),
            e.turn_id,
            e.tool_call_id,
            None if e.tool is None else str(e.tool),
            e.tool_raw,
            canonical_json(dict(e.args)),
            e.text,
            int(e.truncated),
            e.cwd,
            e.received_at.isoformat(),
            e.raw_sha256,
        )
        for seq, e in enumerate(events, start=1)
    ]


def stored_event_rows(conn: sqlite3.Connection, tid: str) -> list[EventRow]:
    return [tuple(row) for row in conn.execute(_SELECT_EVENTS, (tid,))]


def store_run(conn: sqlite3.Connection, run: RunRecord, parsed: ParsedTrace) -> None:
    """Insert one run, its metadata and its events, all or nothing."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(
            "INSERT INTO traces (id, agent, task_id, scenario, collected_at, notes) "
            "VALUES (?, ?, ?, ?, ?, NULL)",
            (
                run.trace_id,
                run.agent,
                run.scenario.repository,
                run.scenario.id,
                run.collected_at.isoformat(),
            ),
        )
        conn.execute(
            "INSERT INTO trace_runs (trace_id, scenario_sha256, workspace_sha256, final_sha256, "
            "raw_sha256, raw_path, model, agent_version, rootsh_version, attempt, exit_code, "
            "timed_out, duration_ms, cost_usd, denied, events_rejected, hook_errors, "
            "recorder_loaded, agent_error, output_path, output_sha256, home_files_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                run.trace_id,
                run.scenario.sha256,
                run.workspace_sha256,
                run.final_sha256,
                run.raw_sha256,
                run.raw_path,
                run.model,
                run.agent_version,
                run.rootsh_version,
                run.attempt,
                run.exit_code,
                int(run.timed_out),
                run.duration_ms,
                run.cost_usd,
                parsed.denied,
                parsed.rejected,
                run.hook_errors,
                int(parsed.recorder_loaded),
                run.agent_error,
                run.output_path,
                run.output_sha256,
                run.home_files_json,
            ),
        )
        conn.executemany(_INSERT_EVENT, event_rows(run.trace_id, parsed.events))
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise


@dataclass(frozen=True, slots=True)
class RebuildSummary:
    runs: int
    events_before: int
    events_after: int
    events_changed_runs: int
    outputs_rewritten: int


def rebuild(conn: sqlite3.Connection, data: Path, who: Identity) -> RebuildSummary:
    """Re-derive every run's events from its raw file, and re-sanitize its
    output, with the current normalizer and sanitizer: one transaction.

    The raw files are the record; events and outputs are derived from them.
    Run it as the author who collected (the raw files hold their identity).
    """
    runs = conn.execute(
        "SELECT t.id, t.agent, r.raw_path, r.output_path FROM traces t "
        "JOIN trace_runs r ON r.trace_id = t.id ORDER BY t.id"
    ).fetchall()
    (before,) = conn.execute("SELECT count(*) FROM trace_events").fetchone()
    changed_runs = rewritten = 0
    pending: list[tuple[Path, bytes]] = []
    conn.execute("BEGIN IMMEDIATE")
    try:
        for tid, agent, raw_path, output_path in runs:
            raw = (data / raw_path).read_bytes().decode("utf-8", "replace")
            parsed = parse_raw(raw, Agent(agent), who)
            rows = event_rows(tid, parsed.events)
            if rows != stored_event_rows(conn, tid):
                changed_runs += 1
                conn.execute("DELETE FROM trace_events WHERE trace_id = ?", (tid,))
                conn.executemany(_INSERT_EVENT, rows)
            output = (data / output_path).read_bytes()
            clean = sanitize(output.decode("utf-8", "replace"), who).encode()
            if clean != output:
                rewritten += 1
                pending.append((data / output_path, clean))
            conn.execute(
                "UPDATE trace_runs SET denied = ?, events_rejected = ?, recorder_loaded = ?, "
                "output_sha256 = ?, agent_error = ?, home_files_json = ? WHERE trace_id = ?",
                (
                    parsed.denied,
                    parsed.rejected,
                    int(parsed.recorder_loaded),
                    hashlib.sha256(clean).hexdigest(),
                    *(
                        None if value is None else sanitize(value, who)
                        for value in conn.execute(
                            "SELECT agent_error, home_files_json FROM trace_runs "
                            "WHERE trace_id = ?",
                            (tid,),
                        ).fetchone()
                    ),
                    tid,
                ),
            )
        # Files are replaced only after every row is ready; a crash between
        # here and COMMIT leaves outputs that `verify` reports, never silence.
        for path, content in pending:
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_bytes(content)
            tmp.replace(path)
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    (after,) = conn.execute("SELECT count(*) FROM trace_events").fetchone()
    return RebuildSummary(len(runs), before, after, changed_runs, rewritten)
