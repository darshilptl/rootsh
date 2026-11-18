"""Host-state probes: read-only facts about the paths an action touches."""

from __future__ import annotations

import os
import posixpath
from collections.abc import Iterable
from dataclasses import dataclass

from rootsh.hoststate.fs import PathKind, expand_glob, kind_of, walk
from rootsh.hoststate.git import GitState, GitView
from rootsh.hoststate.sensitivity import classify, contained_prefix_classes


@dataclass(frozen=True, slots=True)
class PathState:
    path: str
    exists: bool
    kind: PathKind
    real_path: str | None
    in_project: bool
    git: GitState
    files: int | None
    bytes: int | None
    capped: bool
    sensitivity: frozenset[str]


def _under(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip("/") + "/")


def _probe(path: str, project_real: str | None, home: str, git: GitView) -> PathState:
    if not posixpath.isabs(path):
        raise ValueError(f"probe needs an absolute path, got {path!r}")
    path = posixpath.normpath(path)
    kind = kind_of(path)
    exists = kind != "missing"
    real = os.path.realpath(path) if exists else None
    sensitivity = set(classify(path, home))
    if real is not None and real != path:
        sensitivity |= classify(real, home)
    files: int | None = None
    size: int | None = None
    capped = False
    if kind == "dir":
        sensitivity |= contained_prefix_classes(path, home)

        def visit(entry: str) -> None:
            sensitivity.update(classify(entry, home))

        coverage = walk(path, visit)
        files, size, capped = coverage.files, coverage.bytes, coverage.capped
    elif kind == "file":
        files, size = 1, os.lstat(path).st_size
    return PathState(
        path=path,
        exists=exists,
        kind=kind,
        real_path=real,
        # realpath resolves the existing prefix, so a file not yet created
        # inside a symlinked project still counts as inside it.
        in_project=project_real is not None and _under(os.path.realpath(path), project_real),
        git=git.state(path, is_dir=kind == "dir") if exists else "unknown",
        files=files,
        bytes=size,
        capped=capped,
        sensitivity=frozenset(sensitivity),
    )


def probe(paths: Iterable[str], *, project_root: str | None, home: str) -> dict[str, PathState]:
    """Facts about each absolute path. Reads only: no writes, no commands."""
    project_real = os.path.realpath(project_root) if project_root is not None else None
    git = GitView()
    return {path: _probe(path, project_real, home, git) for path in dict.fromkeys(paths)}


__all__ = ["PathState", "expand_glob", "probe"]
