"""Read-only filesystem facts: kind, real path, bounded walks, glob expansion."""

from __future__ import annotations

import glob
import os
import stat
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

MAX_ENTRIES = 10_000
MAX_DEPTH = 32
MAX_SECONDS = 0.5
MAX_GLOB_MATCHES = 1_000

PathKind = Literal["file", "dir", "symlink", "other", "missing"]


def kind_of(path: str) -> PathKind:
    try:
        mode = os.lstat(path).st_mode
    except (FileNotFoundError, NotADirectoryError):
        return "missing"
    except OSError:
        return "other"
    if stat.S_ISLNK(mode):
        return "symlink"
    if stat.S_ISDIR(mode):
        return "dir"
    if stat.S_ISREG(mode):
        return "file"
    return "other"


@dataclass(frozen=True, slots=True)
class Coverage:
    files: int
    bytes: int
    capped: bool


def walk(
    root: str,
    visit: Callable[[str], None],
    *,
    max_entries: int = MAX_ENTRIES,
    max_depth: int = MAX_DEPTH,
    max_seconds: float = MAX_SECONDS,
) -> Coverage:
    """Count regular files and bytes under `root`, calling `visit` on every
    entry. Never follows symlinks. Any cap makes the counts lower bounds."""
    deadline = time.monotonic() + max_seconds
    stack: list[tuple[str, int]] = [(root, 0)]
    files = size = seen = 0
    capped = False
    while stack:
        directory, depth = stack.pop()
        try:
            entries = os.scandir(directory)
        except OSError:
            continue  # unreadable: nothing to count, nothing we can see
        with entries:
            for entry in entries:
                seen += 1
                if seen > max_entries or time.monotonic() > deadline:
                    return Coverage(files, size, capped=True)
                visit(entry.path)
                try:
                    if entry.is_dir(follow_symlinks=False):
                        if depth + 1 >= max_depth:
                            capped = True
                        else:
                            stack.append((entry.path, depth + 1))
                    elif entry.is_file(follow_symlinks=False):
                        files += 1
                        size += entry.stat(follow_symlinks=False).st_size
                except OSError:
                    continue
    return Coverage(files, size, capped)


def expand_glob(pattern: str) -> tuple[list[str], bool]:
    """Matches for a glob pattern (no shell involved), capped."""
    matches: list[str] = []
    for match in glob.iglob(pattern, include_hidden=False):
        if len(matches) >= MAX_GLOB_MATCHES:
            return sorted(matches), True
        matches.append(match)
    return sorted(matches), False
