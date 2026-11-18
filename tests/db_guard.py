"""Test-only guard: no test may open a database outside pytest's temp tree."""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any


class DatabaseGuardError(RuntimeError):
    """A test tried to open a database outside the temporary directory."""


def check_database_target(database: object, allowed_root: Path) -> None:
    if database in (":memory:", b":memory:"):
        return
    if not isinstance(database, str | bytes | os.PathLike):
        raise DatabaseGuardError(f"unsupported database argument {database!r}")
    raw = os.fsdecode(database)
    if raw.startswith("file:"):
        raise DatabaseGuardError(f"URI databases are not allowed in tests: {raw}")
    # resolve() follows symlinks, so a link inside tmp pointing out is caught.
    target = Path(raw).resolve()
    if not target.is_relative_to(allowed_root):
        raise DatabaseGuardError(f"test tried to open a non-temporary database: {target}")


def guarded_connect(
    real_connect: Callable[..., sqlite3.Connection], allowed_root: Path
) -> Callable[..., sqlite3.Connection]:
    root = allowed_root.resolve()

    def connect(database: object, *args: Any, **kwargs: Any) -> sqlite3.Connection:
        check_database_target(database, root)
        return real_connect(database, *args, **kwargs)

    return connect
