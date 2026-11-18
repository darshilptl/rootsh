from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from rootsh.storage.db import DatabaseKind, open_database
from rootsh.storage.migrations import migrate
from rootsh.storage.schema import MIGRATIONS
from support.db_guard import guarded_connect


@pytest.fixture(autouse=True, scope="session")
def _sqlite_guard(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    """Every sqlite3.connect in the test run must target pytest's temp tree."""
    root = tmp_path_factory.getbasetemp()
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(sqlite3, "connect", guarded_connect(sqlite3.connect, root))
        yield


@pytest.fixture
def runtime_db(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    conn = open_database(tmp_path / "runtime.db", DatabaseKind.RUNTIME)
    migrate(conn, MIGRATIONS[DatabaseKind.RUNTIME])
    yield conn
    conn.close()


@pytest.fixture
def research_db(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    conn = open_database(tmp_path / "research.db", DatabaseKind.RESEARCH)
    migrate(conn, MIGRATIONS[DatabaseKind.RESEARCH])
    yield conn
    conn.close()
