"""P1 gate: a test that writes to a non-temporary database fails."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from rootsh.storage.db import DatabaseKind, open_database
from support.db_guard import DatabaseGuardError


def test_opening_a_database_outside_tmp_fails_before_creating_it() -> None:
    target = Path.home() / "rootsh-guard-probe.db"
    assert not target.exists()
    with pytest.raises(DatabaseGuardError, match="non-temporary"):
        open_database(target, DatabaseKind.RUNTIME)
    assert not target.exists()


def test_raw_sqlite_connect_is_guarded_too() -> None:
    with pytest.raises(DatabaseGuardError):
        sqlite3.connect(Path.home() / "rootsh-guard-probe-raw.db")


def test_symlink_from_tmp_to_outside_is_rejected(tmp_path: Path) -> None:
    link = tmp_path / "link.db"
    link.symlink_to(Path.home() / "rootsh-guard-probe.db")
    with pytest.raises(DatabaseGuardError):
        sqlite3.connect(link)


@pytest.mark.parametrize("database", ["file:/tmp/x.db?mode=rwc", "file::memory:", 42])
def test_uris_and_odd_arguments_are_rejected(database: object) -> None:
    with pytest.raises(DatabaseGuardError):
        sqlite3.connect(database)  # type: ignore[call-overload]


def test_temporary_and_memory_databases_are_allowed(tmp_path: Path) -> None:
    open_database(tmp_path / "ok.db", DatabaseKind.RUNTIME).close()
    assert (tmp_path / "ok.db").exists()
    memory = sqlite3.connect(":memory:")
    assert memory.execute("SELECT 1").fetchone() == (1,)
    memory.close()
