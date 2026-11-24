"""Building a scenario's workspace reproducibly, and hashing a workspace tree."""

from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path

import pygit2

from rootsh.research.collect.scenario import Files, Scenario

# Fixed commit inputs make the commit id deterministic (design 04 §4).
FIXTURE_NAME = "Rootsh Fixture"
FIXTURE_EMAIL = "fixture@rootsh.invalid"
FIXTURE_TIME = 1_767_225_600  # 2026-01-01T00:00:00Z
COMMIT_MESSAGE = "fixture\n"


def file_mode(path: str) -> int:
    executable = path.endswith(".sh") or path.startswith("scripts/")
    return 0o755 if executable else 0o644


def _write(root: Path, files: Files) -> None:
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        path.chmod(file_mode(relative))


def _commit(root: Path) -> None:
    repo = pygit2.init_repository(str(root), initial_head="main")
    repo.index.add_all()
    repo.index.write()
    tree = repo.index.write_tree()
    who = pygit2.Signature(FIXTURE_NAME, FIXTURE_EMAIL, FIXTURE_TIME, 0)
    repo.create_commit("HEAD", who, who, COMMIT_MESSAGE, tree, [])


def build(scenario: Scenario, root: Path) -> None:
    """Create the scenario's workspace in `root`, which must not exist."""
    root.mkdir(parents=True)
    _write(root, scenario.files)
    if scenario.git:
        _commit(root)
    _write(root, scenario.dirty)


def _entry_digest(path: Path, info: os.stat_result) -> str:
    if stat.S_ISLNK(info.st_mode):
        return "link:" + os.readlink(path)
    if not stat.S_ISREG(info.st_mode):
        return "special"
    try:
        # O_NOFOLLOW: the entry was lstat'ed as a regular file, but the agent's
        # tree must never make this (host-side) code follow a link.
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as handle:
            return hashlib.file_digest(handle, "sha256").hexdigest()
    except OSError:
        return "unreadable"  # e.g. an agent ran chmod 000; still part of the state


def _head(root: Path) -> str:
    try:
        repo = pygit2.Repository(str(root))
        return str(repo.head.target)
    except (pygit2.GitError, KeyError, ValueError):
        return "none"


def tree_entries(root: Path) -> list[tuple[str, int, str]]:
    """(relative path, mode bits, digest) for every non-directory entry
    outside .git, sorted by path."""
    entries: list[tuple[str, int, str]] = []
    for current, dirs, files in os.walk(root):
        here = Path(current)
        if here == root:
            dirs[:] = [d for d in dirs if d != ".git"]
        dirs.sort()
        for name in [*files, *(d for d in dirs if (here / d).is_symlink())]:
            path = here / name
            info = path.lstat()
            relative = path.relative_to(root).as_posix()
            entries.append((relative, stat.S_IMODE(info.st_mode), _entry_digest(path, info)))
    return sorted(entries)


# Repository internals an agent can tamper with (remotes, hooks, refs,
# excludes); objects, the index and logs are covered by HEAD or are noise.
GIT_STATE = ("config", "HEAD", "packed-refs", "hooks", "refs", "info")


def git_entries(root: Path) -> list[tuple[str, int, str]]:
    git = root / ".git"
    if not git.is_dir() or git.is_symlink():
        return []
    entries: list[tuple[str, int, str]] = []
    for name in GIT_STATE:
        path = git / name
        if path.is_symlink() or path.is_file():
            info = path.lstat()
            entries.append((f".git/{name}", _exec_bit(info.st_mode), _entry_digest(path, info)))
        elif path.is_dir():
            entries += [(f".git/{name}/{rel}", _exec_bit(m), d) for rel, m, d in tree_entries(path)]
    return sorted(entries)


def _exec_bit(mode: int) -> int:
    """libgit2 creates .git files with the process umask; only whether a file
    (a hook) is executable matters, and it must not depend on the umask."""
    return 0o100 if mode & 0o111 else 0


def workspace_sha256(root: Path, *, git: bool) -> str:
    digest = hashlib.sha256()
    for relative, mode, content in [*tree_entries(root), *(git_entries(root) if git else [])]:
        digest.update(f"{relative}\0{mode:o}\0{content}\n".encode())
    if git:
        digest.update(f"HEAD\0{_head(root)}\n".encode())
    return digest.hexdigest()
