"""Shipped migrations for each database kind.

Applied migrations are immutable: their checksums are recorded in each
database. Change the schema only by appending a new migration.
"""

from __future__ import annotations

from collections.abc import Mapping

from rootsh.storage.db import DatabaseKind
from rootsh.storage.migrations import Migration, check_sequence

# Enum value lists are literal on purpose: migration text must never change
# when a Python enum does. tests/storage/test_storage.py checks they stay in sync.
_EVENT_COLUMNS = """
    kind         TEXT NOT NULL CHECK (kind IN ('prompt', 'tool_pre', 'tool_post', 'tool_failure')),
    turn_id      TEXT,
    tool_call_id TEXT,
    tool         TEXT CHECK (tool IS NULL OR tool IN
                     ('shell', 'write', 'edit', 'read', 'fetch', 'mcp', 'other')),
    tool_raw     TEXT,
    args_json    TEXT NOT NULL,
    text         TEXT,
    truncated    INTEGER NOT NULL CHECK (truncated IN (0, 1)),
    cwd          TEXT,
    received_at  TEXT NOT NULL,
    raw_sha256   TEXT NOT NULL"""

_AGENT_CHECK = "CHECK (agent IN ('claude_code', 'opencode'))"

RUNTIME: tuple[Migration, ...] = (
    Migration(
        1,
        "sessions_events",
        f"""
CREATE TABLE sessions (
    key           TEXT PRIMARY KEY,
    agent         TEXT NOT NULL {_AGENT_CHECK},
    session_id    TEXT NOT NULL,
    cwd           TEXT,
    first_seen_at TEXT NOT NULL,
    UNIQUE (agent, session_id)
) STRICT;

CREATE TABLE events (
    id          INTEGER PRIMARY KEY,
    session_key TEXT NOT NULL REFERENCES sessions (key),
    seq         INTEGER NOT NULL CHECK (seq >= 1),{_EVENT_COLUMNS},
    UNIQUE (session_key, seq),
    UNIQUE (session_key, raw_sha256)
) STRICT;

CREATE INDEX events_by_turn ON events (session_key, turn_id);
""",
    ),
)

RESEARCH: tuple[Migration, ...] = (
    Migration(
        1,
        "traces_runs",
        f"""
CREATE TABLE traces (
    id           TEXT PRIMARY KEY,
    agent        TEXT NOT NULL {_AGENT_CHECK},
    task_id      TEXT NOT NULL,
    scenario     TEXT NOT NULL,
    collected_at TEXT NOT NULL,
    notes        TEXT
) STRICT;

CREATE TABLE trace_events (
    id       INTEGER PRIMARY KEY,
    trace_id TEXT NOT NULL REFERENCES traces (id),
    seq      INTEGER NOT NULL CHECK (seq >= 1),{_EVENT_COLUMNS},
    UNIQUE (trace_id, seq)
) STRICT;

CREATE TABLE runs (
    id             TEXT PRIMARY KEY,
    kind           TEXT NOT NULL CHECK (kind IN ('train', 'validation', 'heldout')),
    model_sha256   TEXT NOT NULL,
    dataset_sha256 TEXT NOT NULL,
    metrics_json   TEXT NOT NULL,
    created_at     TEXT NOT NULL,
    notes          TEXT
) STRICT;

-- Heldout data is evaluated once per frozen model (AGENTS.md, rule 6).
CREATE UNIQUE INDEX one_heldout_per_model ON runs (model_sha256, dataset_sha256)
    WHERE kind = 'heldout';
""",
    ),
    Migration(
        2,
        "trace_runs",
        """
-- One row per collected trace: the hashes that make it reproducible and
-- verifiable, and how the run ended (docs/design/04-trace-harness.md §7).
CREATE TABLE trace_runs (
    trace_id         TEXT PRIMARY KEY REFERENCES traces (id),
    scenario_sha256  TEXT NOT NULL CHECK (length(scenario_sha256) = 64),
    workspace_sha256 TEXT NOT NULL CHECK (length(workspace_sha256) = 64),
    final_sha256     TEXT NOT NULL CHECK (length(final_sha256) = 64),
    raw_sha256       TEXT NOT NULL CHECK (length(raw_sha256) = 64),
    raw_path         TEXT NOT NULL,
    model            TEXT NOT NULL,
    agent_version    TEXT NOT NULL,
    rootsh_version   TEXT NOT NULL,
    attempt          INTEGER NOT NULL CHECK (attempt >= 1),
    exit_code        INTEGER,
    timed_out        INTEGER NOT NULL CHECK (timed_out IN (0, 1)),
    duration_ms      INTEGER NOT NULL CHECK (duration_ms >= 0),
    cost_usd         REAL CHECK (cost_usd IS NULL OR cost_usd >= 0),
    denied           INTEGER NOT NULL CHECK (denied >= 0),
    events_rejected  INTEGER NOT NULL CHECK (events_rejected >= 0),
    hook_errors      INTEGER NOT NULL CHECK (hook_errors >= 0),
    recorder_loaded  INTEGER NOT NULL CHECK (recorder_loaded IN (0, 1)),
    agent_error      TEXT,            -- the agent's own error report, if any
    output_path      TEXT NOT NULL,   -- agent stdout+stderr, relative to the data dir
    output_sha256    TEXT NOT NULL CHECK (length(output_sha256) = 64),
    home_files_json  TEXT NOT NULL,   -- files the agent left in its sandbox home
    CHECK ((exit_code IS NULL) = (timed_out = 1))
) STRICT;
""",
    ),
)

MIGRATIONS: Mapping[DatabaseKind, tuple[Migration, ...]] = {
    DatabaseKind.RUNTIME: RUNTIME,
    DatabaseKind.RESEARCH: RESEARCH,
}

for _migrations in MIGRATIONS.values():
    check_sequence(_migrations)
