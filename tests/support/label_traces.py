"""Synthetic traces in a research DB, for label and variant tests."""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from datetime import UTC, datetime

from rootsh.analysis.events import Agent, AgentEvent, EventKind, Tool
from rootsh.research.collect.store import event_rows

NOW = datetime(2026, 11, 20, tzinfo=UTC)


def prompt(text: str) -> AgentEvent:
    return AgentEvent(
        Agent.OPENCODE,
        EventKind.PROMPT,
        "s",
        "t",
        None,
        None,
        None,
        {},
        text,
        False,
        "/work",
        NOW,
        "0" * 64,
    )


def call(
    command: str, n: int, *, tool: Tool = Tool.SHELL, raw: str = "bash", output: str = ""
) -> list[AgentEvent]:
    args = {"command": command} if tool is Tool.SHELL else {"url": command}
    pre = AgentEvent(
        Agent.OPENCODE,
        EventKind.TOOL_PRE,
        "s",
        "t",
        f"c{n}",
        tool,
        raw,
        args,
        None,
        False,
        "/work",
        NOW,
        f"{n:064d}",
    )
    post = AgentEvent(
        Agent.OPENCODE,
        EventKind.TOOL_POST,
        "s",
        "t",
        f"c{n}",
        tool,
        raw,
        args,
        output,
        False,
        "/work",
        NOW,
        f"{n + 1:064d}",
    )
    return [pre, post]


def store_trace(
    conn: sqlite3.Connection, tid: str, scenario: str, events: Sequence[AgentEvent]
) -> None:
    with conn:
        conn.execute(
            "INSERT INTO traces (id, agent, task_id, scenario, collected_at) "
            "VALUES (?, 'opencode', ?, ?, ?)",
            (tid, scenario.split("-")[0], scenario, NOW.isoformat()),
        )
        conn.executemany(
            "INSERT INTO trace_events (trace_id, seq, kind, turn_id, tool_call_id, tool, tool_raw, "
            "args_json, text, truncated, cwd, received_at, raw_sha256) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            event_rows(tid, tuple(events)),
        )
