"""Forward-only, checksummed schema migrations."""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime


class MigrationError(Exception):
    """The schema cannot be brought to the expected state safely."""


@dataclass(frozen=True, slots=True)
class Migration:
    version: int
    name: str
    sql: str

    @property
    def checksum(self) -> str:
        return hashlib.sha256(self.sql.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class MigrationStatus:
    applied: tuple[int, ...]
    pending: tuple[int, ...]
    problems: tuple[str, ...] = field(default=())

    @property
    def ok(self) -> bool:
        return not self.problems


_LEDGER = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    INTEGER PRIMARY KEY,
    name       TEXT NOT NULL,
    checksum   TEXT NOT NULL,
    applied_at TEXT NOT NULL
) STRICT
"""


def check_sequence(migrations: Sequence[Migration]) -> None:
    """Versions must be exactly 1..N in order; anything else is a code bug."""
    versions = [m.version for m in migrations]
    if versions != list(range(1, len(migrations) + 1)):
        raise ValueError(f"migration versions must be 1..N without gaps, got {versions}")


def split_statements(sql: str) -> Iterator[str]:
    """Split a script into complete statements (semicolons in strings or
    trigger bodies do not end a statement)."""
    buffer = ""
    for chunk in sql.split(";"):
        buffer += chunk + ";"
        if sqlite3.complete_statement(buffer):
            statement = buffer.strip()
            if statement != ";":
                yield statement
            buffer = ""
    if buffer.strip(" \n\t;"):
        raise ValueError("migration ends with an incomplete statement")


def _applied(conn: sqlite3.Connection) -> dict[int, tuple[str, str]]:
    ledger = conn.execute(
        "SELECT 1 FROM sqlite_schema WHERE type = 'table' AND name = 'schema_migrations'"
    ).fetchone()
    if ledger is None:
        return {}
    rows = conn.execute("SELECT version, name, checksum FROM schema_migrations").fetchall()
    return {int(version): (str(name), str(checksum)) for version, name, checksum in rows}


def _problems(applied: dict[int, tuple[str, str]], migrations: Sequence[Migration]) -> list[str]:
    known = {m.version: m for m in migrations}
    problems = []
    for version, (name, checksum) in sorted(applied.items()):
        migration = known.get(version)
        if migration is None:
            problems.append(f"version {version} ({name}) is newer than this rootsh")
        elif checksum != migration.checksum:
            problems.append(f"version {version} ({name}) differs from the shipped migration")
    return problems


def status(conn: sqlite3.Connection, migrations: Sequence[Migration]) -> MigrationStatus:
    """Read-only: never creates or changes anything."""
    check_sequence(migrations)
    applied = _applied(conn)
    return MigrationStatus(
        applied=tuple(sorted(applied)),
        pending=tuple(m.version for m in migrations if m.version not in applied),
        problems=tuple(_problems(applied, migrations)),
    )


def _apply(conn: sqlite3.Connection, migration: Migration) -> bool:
    """Apply one migration atomically; False if another process got there first."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        # Re-read under the write lock: a concurrent migrator may have applied it.
        if migration.version in _applied(conn):
            conn.execute("COMMIT")
            return False
        for statement in split_statements(migration.sql):
            conn.execute(statement)
        conn.execute(
            "INSERT INTO schema_migrations (version, name, checksum, applied_at) VALUES (?,?,?,?)",
            (migration.version, migration.name, migration.checksum, datetime.now(UTC).isoformat()),
        )
        conn.execute("COMMIT")
    except BaseException as exc:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        if isinstance(exc, sqlite3.Error):
            raise MigrationError(f"migration {migration.version} failed: {exc}") from exc
        raise
    return True


def migrate(conn: sqlite3.Connection, migrations: Sequence[Migration]) -> list[int]:
    """Apply pending migrations in order; return the versions applied here.

    Refuses to touch the schema if it is newer than the code or if an
    applied migration's checksum differs from the shipped one.
    """
    current = status(conn, migrations)
    if not current.ok:
        raise MigrationError("; ".join(current.problems))
    conn.execute(_LEDGER)
    return [m.version for m in migrations if m.version in current.pending and _apply(conn, m)]
