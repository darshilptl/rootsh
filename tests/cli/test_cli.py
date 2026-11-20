from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from rootsh import __version__
from rootsh.cli import EXIT_FAILED, EXIT_OK, EXIT_USAGE, main
from rootsh.storage.db import DatabaseKind, open_database
from rootsh.storage.migrations import Migration, migrate
from rootsh.storage.schema import RUNTIME


def run(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str, str]:
    code = main(list(argv))
    out, err = capsys.readouterr()
    return code, out, err


@pytest.mark.parametrize("kind", ["runtime", "research"])
def test_migrate_then_status(tmp_path: Path, capsys: pytest.CaptureFixture[str], kind: str) -> None:
    db = tmp_path / f"{kind}.db"
    code, out, _ = run(capsys, "db", "migrate", kind, str(db))
    assert code == EXIT_OK
    assert "applied now: 1" in out
    assert "status: ok" in out

    code, out, _ = run(capsys, "db", "migrate", kind, str(db))
    assert code == EXIT_OK
    assert "applied now: none (up to date)" in out

    code, out, _ = run(capsys, "db", "status", kind, str(db))
    assert code == EXIT_OK
    assert "pending: none" in out


def test_status_never_creates_a_database(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "missing.db"
    code, _, err = run(capsys, "db", "status", "runtime", str(db))
    assert code == EXIT_FAILED
    assert "does not exist" in err
    assert not db.exists()


def test_status_reports_pending_as_failure(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "r.db"
    open_database(db, DatabaseKind.RUNTIME).close()  # claimed, never migrated
    code, out, _ = run(capsys, "db", "status", "runtime", str(db))
    assert code == EXIT_FAILED
    assert "pending: 1 (sessions_events)" in out
    # status is read-only: not even the migration ledger was created
    tables = sqlite3.connect(db).execute("SELECT name FROM sqlite_schema").fetchall()
    assert tables == []


def test_wrong_kind_is_an_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db = tmp_path / "r.db"
    run(capsys, "db", "migrate", "runtime", str(db))
    code, _, err = run(capsys, "db", "migrate", "research", str(db))
    assert code == EXIT_FAILED
    assert "runtime database, not research" in err


def test_newer_database_is_reported(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db = tmp_path / "r.db"
    conn = open_database(db, DatabaseKind.RUNTIME)
    migrate(
        conn,
        (
            *RUNTIME,
            Migration(len(RUNTIME) + 1, "future", "CREATE TABLE future (x INTEGER) STRICT;"),
        ),
    )
    conn.close()
    code, out, _ = run(capsys, "db", "status", "runtime", str(db))
    assert code == EXIT_FAILED
    assert "status: PROBLEM" in out
    assert "newer than this rootsh" in out
    code, _, err = run(capsys, "db", "migrate", "runtime", str(db))
    assert code == EXIT_FAILED
    assert "newer than this rootsh" in err


def test_usage_errors_exit_2(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["db", "migrate", "nonsense", "x.db"])
    assert exit_info.value.code == EXIT_USAGE
    with pytest.raises(SystemExit) as exit_info:
        main(["--version"])
    assert exit_info.value.code == EXIT_OK
    assert __version__ in capsys.readouterr().out
