from __future__ import annotations

import copy
import json
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from rootsh.events import Agent, EventKind, Tool
from rootsh.normalize import normalize
from rootsh.storage.db import DatabaseKind, DatabaseKindError, open_database
from rootsh.storage.events_store import ingest
from rootsh.storage.migrations import (
    Migration,
    MigrationError,
    _apply,
    check_sequence,
    migrate,
    split_statements,
    status,
)
from rootsh.storage.schema import MIGRATIONS, RESEARCH, RUNTIME
from support import FIXTURES_DIR

NOW = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)
FIXTURES: dict[str, dict[str, dict[str, Any]]] = json.loads(
    (FIXTURES_DIR / "hook_payloads.json").read_text(encoding="utf-8")
)


def tables(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_schema WHERE type = 'table'").fetchall()
    return {row[0] for row in rows}


# --- database identity --------------------------------------------------------


def test_new_database_is_claimed_with_wal_and_pragmas(tmp_path: Path) -> None:
    conn = open_database(tmp_path / "r.db", DatabaseKind.RUNTIME)
    assert conn.execute("PRAGMA application_id").fetchone()[0] == 0x52535254
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert conn.execute("PRAGMA synchronous").fetchone()[0] == 2  # FULL
    conn.close()


def test_kinds_cannot_be_swapped(tmp_path: Path) -> None:
    open_database(tmp_path / "r.db", DatabaseKind.RUNTIME).close()
    with pytest.raises(DatabaseKindError, match="runtime database, not research"):
        open_database(tmp_path / "r.db", DatabaseKind.RESEARCH)


def test_foreign_sqlite_file_is_rejected(tmp_path: Path) -> None:
    foreign = sqlite3.connect(tmp_path / "other.db")
    foreign.execute("CREATE TABLE t (x)")
    foreign.close()
    with pytest.raises(DatabaseKindError, match="not a rootsh database"):
        open_database(tmp_path / "other.db", DatabaseKind.RUNTIME)


def test_create_false_never_initializes(tmp_path: Path) -> None:
    with pytest.raises(DatabaseKindError, match="does not exist"):
        open_database(tmp_path / "missing.db", DatabaseKind.RUNTIME, create=False)
    assert not (tmp_path / "missing.db").exists()
    sqlite3.connect(tmp_path / "empty.db").close()
    with pytest.raises(DatabaseKindError, match="empty"):
        open_database(tmp_path / "empty.db", DatabaseKind.RUNTIME, create=False)


@pytest.mark.parametrize("kind", list(DatabaseKind))
def test_kind_labels_round_trip(kind: DatabaseKind) -> None:
    assert DatabaseKind.from_label(kind.label) is kind


# --- migrations ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("kind", "expected", "versions"),
    [
        (DatabaseKind.RUNTIME, {"sessions", "events"}, [1]),
        (DatabaseKind.RESEARCH, {"traces", "trace_events", "runs", "trace_runs"}, [1, 2]),
    ],
)
def test_migrate_from_nothing_then_noop(
    tmp_path: Path, kind: DatabaseKind, expected: set[str], versions: list[int]
) -> None:
    conn = open_database(tmp_path / "db", kind)
    assert migrate(conn, MIGRATIONS[kind]) == versions
    assert expected <= tables(conn)
    assert migrate(conn, MIGRATIONS[kind]) == []
    current = status(conn, MIGRATIONS[kind])
    assert current.ok
    assert current.applied == tuple(versions)
    assert current.pending == ()


def test_edited_migration_is_refused_and_nothing_applies(runtime_db: sqlite3.Connection) -> None:
    edited = (Migration(1, "sessions_events", RUNTIME[0].sql + "\n-- edited"),)
    with pytest.raises(MigrationError, match="differs"):
        migrate(runtime_db, edited)
    assert status(runtime_db, RUNTIME).applied == (1,)
    assert tables(runtime_db) == {"schema_migrations", "sessions", "events"}


def test_newer_database_is_refused(runtime_db: sqlite3.Connection) -> None:
    newer = (*RUNTIME, Migration(2, "future", "CREATE TABLE future (x INTEGER) STRICT;"))
    migrate(runtime_db, newer)
    with pytest.raises(MigrationError, match="newer"):
        migrate(runtime_db, RUNTIME)
    assert "future" in tables(runtime_db)


def test_failed_migration_rolls_back_completely(runtime_db: sqlite3.Connection) -> None:
    broken = (
        *RUNTIME,
        Migration(
            2, "half", "CREATE TABLE half (x INTEGER) STRICT; INSERT INTO nowhere VALUES (1);"
        ),
    )
    with pytest.raises(MigrationError, match="migration 2 failed"):
        migrate(runtime_db, broken)
    assert "half" not in tables(runtime_db)
    assert status(runtime_db, broken).pending == (2,)
    assert not runtime_db.in_transaction


def test_split_statements_respects_strings_and_triggers() -> None:
    sql = """
CREATE TABLE a (x TEXT);
INSERT INTO a VALUES ('semi;colon');
CREATE TRIGGER t AFTER INSERT ON a BEGIN INSERT INTO a VALUES ('x'); END;
"""
    statements = list(split_statements(sql))
    assert len(statements) == 3
    assert "'semi;colon'" in statements[1]
    assert statements[2].endswith("END;")


def test_split_statements_rejects_unterminated_input() -> None:
    with pytest.raises(ValueError, match="incomplete"):
        list(split_statements("INSERT INTO a VALUES ('open"))


@pytest.mark.parametrize("versions", [[2], [1, 3], [1, 1]])
def test_check_sequence_rejects_gaps(versions: list[int]) -> None:
    with pytest.raises(ValueError, match=r"1\.\.N"):
        check_sequence([Migration(v, f"m{v}", "SELECT 1;") for v in versions])


def test_schema_enum_lists_match_python_enums() -> None:
    sql = RUNTIME[0].sql + RESEARCH[0].sql
    for enum in (Agent, EventKind, Tool):
        for member in enum:
            assert re.search(rf"'{member.value}'", sql), f"{enum.__name__}.{member.name}"


# --- ingest --------------------------------------------------------------------


def event(agent: Agent, name: str, **overrides: Any) -> Any:
    payload = copy.deepcopy(FIXTURES[agent.value][name])
    payload.update(overrides)
    return normalize(agent, name.split(":")[0], payload, NOW)


def test_ingest_assigns_per_session_sequence(runtime_db: sqlite3.Connection) -> None:
    results = [
        ingest(runtime_db, event(Agent.CLAUDE_CODE, hook))
        for hook in ("UserPromptSubmit", "PreToolUse", "PostToolUse")
    ]
    assert [r.seq for r in results] == [1, 2, 3]
    assert not any(r.duplicate for r in results)
    assert len({r.turn_id for r in results}) == 1


def test_ingest_is_idempotent(runtime_db: sqlite3.Connection) -> None:
    first = ingest(runtime_db, event(Agent.CLAUDE_CODE, "PreToolUse"))
    again = ingest(runtime_db, event(Agent.CLAUDE_CODE, "PreToolUse"))
    assert again.duplicate
    assert again.event_id == first.event_id
    assert runtime_db.execute("SELECT count(*) FROM events").fetchone()[0] == 1


def test_opencode_tool_events_join_the_latest_prompt(runtime_db: sqlite3.Connection) -> None:
    ingest(runtime_db, event(Agent.OPENCODE, "session.prompt", messageID="msg_first"))
    ingest(runtime_db, event(Agent.OPENCODE, "session.prompt", messageID="msg_second"))
    before = ingest(runtime_db, event(Agent.OPENCODE, "tool.execute.before"))
    after = ingest(runtime_db, event(Agent.OPENCODE, "tool.execute.after"))
    assert before.turn_id == "msg_second"
    assert after.turn_id == "msg_second"


def test_opencode_tool_event_without_prompt_has_no_turn(runtime_db: sqlite3.Connection) -> None:
    assert ingest(runtime_db, event(Agent.OPENCODE, "tool.execute.before")).turn_id is None


def test_same_session_id_from_two_agents_stays_separate(runtime_db: sqlite3.Connection) -> None:
    ingest(runtime_db, event(Agent.CLAUDE_CODE, "PreToolUse", session_id="shared"))
    oc = ingest(runtime_db, event(Agent.OPENCODE, "tool.execute.before", sessionID="shared"))
    assert oc.seq == 1
    assert runtime_db.execute("SELECT count(*) FROM sessions").fetchone()[0] == 2


def test_session_cwd_is_filled_from_first_event_that_has_one(
    runtime_db: sqlite3.Connection,
) -> None:
    ingest(runtime_db, event(Agent.CLAUDE_CODE, "PreToolUse", cwd=None))
    ingest(runtime_db, event(Agent.CLAUDE_CODE, "PostToolUse"))
    (cwd,) = runtime_db.execute("SELECT cwd FROM sessions").fetchone()
    assert cwd == "/home/user/project"


def test_stored_row_round_trips_the_event(runtime_db: sqlite3.Connection) -> None:
    source = event(Agent.CLAUDE_CODE, "PreToolUse")
    ingest(runtime_db, source)
    row = runtime_db.execute(
        "SELECT kind, tool, tool_raw, args_json, truncated, raw_sha256 FROM events"
    ).fetchone()
    assert row[:3] == ("tool_pre", "shell", "Bash")
    assert json.loads(row[3]) == dict(source.args)
    assert row[4:] == (0, source.raw_sha256)


def test_schema_rejects_out_of_range_values(runtime_db: sqlite3.Connection) -> None:
    ingest(runtime_db, event(Agent.CLAUDE_CODE, "PreToolUse"))
    with pytest.raises(sqlite3.IntegrityError):
        runtime_db.execute("UPDATE events SET kind = 'kernel_deny'")
    with pytest.raises(sqlite3.IntegrityError):
        runtime_db.execute("UPDATE events SET session_key = 'claude_code:nobody'")


# --- research ------------------------------------------------------------------


def add_run(conn: sqlite3.Connection, run_id: str, kind: str, model: str = "m1") -> None:
    conn.execute(
        "INSERT INTO runs (id, kind, model_sha256, dataset_sha256, metrics_json, created_at) "
        "VALUES (?, ?, ?, 'd1', '{}', ?)",
        (run_id, kind, model, NOW.isoformat()),
    )


def test_heldout_is_evaluated_once_per_model_and_dataset(research_db: sqlite3.Connection) -> None:
    add_run(research_db, "r1", "heldout")
    add_run(research_db, "r2", "validation")
    add_run(research_db, "r3", "validation")
    add_run(research_db, "r4", "heldout", model="m2")
    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
        add_run(research_db, "r5", "heldout")


def test_a_migration_applied_by_another_process_is_skipped(runtime_db: sqlite3.Connection) -> None:
    # migrate() re-reads the ledger under the write lock; a version applied by a
    # concurrent migrator is skipped instead of failing on "table exists".
    assert _apply(runtime_db, RUNTIME[0]) is False
    assert status(runtime_db, RUNTIME).applied == (1,)
