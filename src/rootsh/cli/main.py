"""`rootsh` command line."""

from __future__ import annotations

import argparse
import sqlite3
import sys
from collections.abc import Sequence
from pathlib import Path

from rootsh import __version__
from rootsh.storage.db import DatabaseKind, DatabaseKindError, open_database
from rootsh.storage.migrations import MigrationError, MigrationStatus, migrate, status
from rootsh.storage.schema import MIGRATIONS

EXIT_OK, EXIT_FAILED, EXIT_USAGE = 0, 1, 2


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="rootsh", description=__doc__)
    parser.add_argument("--version", action="version", version=f"rootsh {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)

    db = commands.add_parser("db", help="manage rootsh databases")
    db_commands = db.add_subparsers(dest="db_command", required=True)
    for name, text in (
        ("migrate", "apply pending migrations (creates the database if missing)"),
        ("status", "show applied and pending migrations without changing anything"),
    ):
        sub = db_commands.add_parser(name, help=text)
        sub.add_argument("kind", choices=[kind.label for kind in DatabaseKind])
        sub.add_argument("path", type=Path)
    return parser


def _describe(kind: DatabaseKind, path: Path, current: MigrationStatus) -> str:
    names = {m.version: m.name for m in MIGRATIONS[kind]}

    def listing(versions: tuple[int, ...]) -> str:
        if not versions:
            return "none"
        return ", ".join(f"{v} ({names.get(v, 'unknown')})" for v in versions)

    lines = [
        f"{kind.label} database: {path}",
        f"applied: {listing(current.applied)}",
        f"pending: {listing(current.pending)}",
        f"status: {'ok' if current.ok else 'PROBLEM'}",
        *(f"  - {problem}" for problem in current.problems),
    ]
    return "\n".join(lines)


def _db(command: str, kind: DatabaseKind, path: Path) -> int:
    conn = open_database(path, kind, create=command == "migrate")
    try:
        migrations = MIGRATIONS[kind]
        if command == "migrate":
            applied = migrate(conn, migrations)
            print(
                f"applied now: {', '.join(map(str, applied)) if applied else 'none (up to date)'}"
            )
        current = status(conn, migrations)
        print(_describe(kind, path, current))
        return EXIT_OK if current.ok and not current.pending else EXIT_FAILED
    finally:
        conn.close()


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return _db(args.db_command, DatabaseKind.from_label(args.kind), args.path)
    except (DatabaseKindError, MigrationError, sqlite3.Error) as exc:
        print(f"rootsh: error: {exc}", file=sys.stderr)
        return EXIT_FAILED
