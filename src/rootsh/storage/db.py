"""Opening rootsh's SQLite databases: identity check and connection pragmas."""

from __future__ import annotations

import sqlite3
from enum import Enum
from pathlib import Path


class DatabaseKind(Enum):
    RUNTIME = ("runtime", 0x52535254)  # "RSRT"
    RESEARCH = ("research", 0x52535253)  # "RSRS"

    def __init__(self, label: str, application_id: int) -> None:
        self.label = label
        self.application_id = application_id

    @classmethod
    def from_label(cls, label: str) -> DatabaseKind:
        for kind in cls:
            if kind.label == label:
                return kind
        raise ValueError(f"unknown database kind {label!r}")


class DatabaseKindError(Exception):
    """The file is not a rootsh database of the expected kind."""


_PRAGMAS = (
    "PRAGMA foreign_keys = ON",
    "PRAGMA synchronous = FULL",  # events are an audit trail
    "PRAGMA busy_timeout = 5000",
    "PRAGMA trusted_schema = OFF",
)


def _is_empty(conn: sqlite3.Connection) -> bool:
    (count,) = conn.execute("SELECT count(*) FROM sqlite_schema").fetchone()
    return int(count) == 0


def _claim_or_check(conn: sqlite3.Connection, kind: DatabaseKind, *, create: bool) -> None:
    (found,) = conn.execute("PRAGMA application_id").fetchone()
    if found == kind.application_id:
        return
    if found == 0 and _is_empty(conn):
        if not create:
            raise DatabaseKindError("database is empty; run `rootsh db migrate` first")
        conn.execute(f"PRAGMA application_id = {kind.application_id:d}")
        return
    other = next((k for k in DatabaseKind if k.application_id == found), None)
    if other is not None:
        raise DatabaseKindError(f"this is a {other.label} database, not {kind.label}")
    raise DatabaseKindError(f"not a rootsh database (application_id {found:#x})")


def open_database(path: Path, kind: DatabaseKind, *, create: bool = True) -> sqlite3.Connection:
    """Open a rootsh database. Transactions are explicit (autocommit mode).

    With create=False a missing or empty file is an error instead of a new
    database, so read-only callers never initialize one by accident.
    """
    if not create and not path.exists():
        raise DatabaseKindError(f"{path} does not exist")
    conn = sqlite3.connect(path, isolation_level=None)
    try:
        for pragma in _PRAGMAS:
            conn.execute(pragma)
        _claim_or_check(conn, kind, create=create)
        (mode,) = conn.execute("PRAGMA journal_mode = WAL").fetchone()
        if mode != "wal":
            raise DatabaseKindError(f"could not enable WAL journaling (got {mode!r})")
    except BaseException:
        conn.close()
        raise
    return conn
