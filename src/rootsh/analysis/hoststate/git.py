"""Git state through libgit2 (pygit2), never the git CLI.

The git CLI runs commands from repository config during `git status`
(core.fsmonitor, filter.<x>.clean). A repository the agent controls could
set those, and Rootsh would run them. libgit2 runs no config-defined external
commands; tests/analysis/hoststate/test_hoststate.py proves it against a hostile repository.
"""

from __future__ import annotations

import os
from typing import Literal

import pygit2
from pygit2.enums import FileStatus

GitState = Literal["clean", "modified", "untracked", "ignored", "not_in_repo", "unknown"]

# Worst first: a directory is as unrecoverable as the worst thing inside it.
_RANK: dict[GitState, int] = {"modified": 3, "untracked": 2, "ignored": 1, "clean": 0}
_MODIFIED = (
    FileStatus.WT_MODIFIED
    | FileStatus.WT_DELETED
    | FileStatus.WT_TYPECHANGE
    | FileStatus.WT_RENAMED
    | FileStatus.WT_UNREADABLE
    | FileStatus.CONFLICTED
)


def from_flags(flags: int) -> GitState:
    if flags & _MODIFIED:
        return "modified"
    if flags & FileStatus.WT_NEW:
        return "untracked"
    if flags & FileStatus.IGNORED:
        return "ignored"
    return "clean"  # current, or staged only: the content is in git's object store


def _worst(states: list[GitState]) -> GitState | None:
    if not states:
        return None
    ranked: list[tuple[int, GitState]] = [(_RANK[state], state) for state in states]
    return max(ranked)[1]


class GitView:
    """Answers git-state questions for one probe call; caches per repository."""

    def __init__(self) -> None:
        self._repos: dict[str, tuple[pygit2.Repository, dict[str, int]] | None] = {}

    def _open(self, location: str) -> tuple[pygit2.Repository, dict[str, int]] | None:
        if location not in self._repos:
            try:
                repo = pygit2.Repository(location)
                self._repos[location] = (
                    repo,
                    dict(repo.status(untracked_files="all", ignored=True)),
                )
            except (pygit2.GitError, OSError):
                self._repos[location] = None  # e.g. owned by another user
        return self._repos[location]

    def state(self, path: str, is_dir: bool) -> GitState:
        start = path if is_dir else os.path.dirname(path)
        try:
            location = pygit2.discover_repository(start)
        except pygit2.GitError:
            return "unknown"
        if location is None:
            return "not_in_repo"
        found = self._open(location)
        if found is None:
            return "unknown"
        repo, statuses = found
        if repo.workdir is None:
            return "unknown"
        rel = os.path.relpath(path, repo.workdir)
        if rel == ".git" or rel.startswith((".git/", "../")) or rel == "..":
            return "unknown"
        if not is_dir:
            return self._file(repo, statuses, rel)
        return self._directory(repo, statuses, rel)

    @staticmethod
    def _file(repo: pygit2.Repository, statuses: dict[str, int], rel: str) -> GitState:
        if rel in statuses:
            return from_flags(statuses[rel])
        for entry, flags in statuses.items():  # inside a directory reported as a whole
            if entry.endswith("/") and rel.startswith(entry):
                return from_flags(flags)
        if rel in repo.index:
            return "clean"
        return "ignored" if repo.path_is_ignored(rel) else "unknown"

    @staticmethod
    def _directory(repo: pygit2.Repository, statuses: dict[str, int], rel: str) -> GitState:
        prefix = "" if rel == "." else rel.rstrip("/") + "/"
        inside: list[GitState] = [
            from_flags(flags)
            for entry, flags in statuses.items()
            # entries under the directory, or a whole directory entry ("dir/") containing it
            if entry.startswith(prefix) or (entry.endswith("/") and prefix.startswith(entry))
        ]
        worst = _worst(inside)
        if worst is not None:
            return worst
        if any(entry.path.startswith(prefix) for entry in repo.index):
            return "clean"
        return "ignored" if repo.path_is_ignored(rel) else "untracked"
