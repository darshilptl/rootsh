"""Atomic, recorded file changes that uninstall can revert exactly (design 08 §4).

Every file rootsh installs goes through `Changes.write()`, which keeps the
prior bytes. `revert()` restores them (or deletes a file that did not exist)
only if the file still holds exactly what rootsh wrote; a file the author
changed since is left alone and reported.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import os
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

MANIFEST_VERSION = 1


class InstallError(Exception):
    """Install or uninstall must stop; the message says why."""


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_regular(path: Path) -> bytes | None:
    """The file's bytes, None if absent. Refuses symlinks and non-files."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    except FileNotFoundError:
        return None
    except OSError as exc:  # ELOOP: a symlink
        raise InstallError(f"{path} is not a plain file ({exc.strerror})") from exc
    with os.fdopen(fd, "rb") as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            raise InstallError(f"{path} is not a regular file")
        return handle.read()


def atomic_write(path: Path, data: bytes, mode: int) -> None:
    """Replace `path` with `data` in one rename; never through a symlink."""
    read_regular(path)  # refuses a symlink or special file at the target
    tmp = path.with_name(f".{path.name}.rootsh-{os.getpid()}.tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
    fd = os.open(tmp, flags, 0o600)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fchmod(handle.fileno(), mode)
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            tmp.unlink()
        raise
    _fsync_dir(path.parent)


def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


@dataclass(slots=True)
class Changes:
    """What one install step changed, in order; serialised as the manifest."""

    files: list[dict[str, Any]] = field(default_factory=list)
    dirs: list[str] = field(default_factory=list)  # created by rootsh, parents first

    def mkdir(self, path: Path, mode: int = 0o755) -> None:
        missing = []
        for parent in (path, *path.parents):
            if parent.is_symlink():
                if parent == path:
                    raise InstallError(f"{path} is a symlink")
                break  # an existing link higher up is the author's own layout
            if parent.exists():
                if not parent.is_dir():
                    raise InstallError(f"{parent} exists and is not a directory")
                break
            missing.append(parent)
        for created in reversed(missing):
            created.mkdir(mode=mode)
            created.chmod(mode)  # umask-independent
            self.dirs.append(str(created))

    def write(self, path: Path, data: bytes, mode: int) -> bool:
        """Install `data` at `path`; returns False if it was already there.

        Start from the previous install's manifest (`load`) when installing
        over an install: a path it already records keeps its first prior
        bytes, so uninstall still restores what the author had."""
        known = next((f for f in self.files if f["path"] == str(path)), None)
        prior = read_regular(path)
        prior_mode = path.stat().st_mode & 0o7777 if prior is not None else None
        if prior == data and prior_mode == mode:
            if known is None:  # identical file, no record of who wrote it: never delete it
                self.files.append(_entry(path, data, mode, prior, prior_mode))
            return False
        self.mkdir(path.parent)
        atomic_write(path, data, mode)
        if known is not None:
            known["sha256"], known["mode"] = sha256(data), mode
        else:
            self.files.append(_entry(path, data, mode, prior, prior_mode))
        return True

    def dump(self) -> bytes:
        body = {"version": MANIFEST_VERSION, "files": self.files, "dirs": self.dirs}
        return json.dumps(body, indent=2, sort_keys=True).encode() + b"\n"

    @classmethod
    def load(cls, data: bytes) -> Changes:
        try:
            body = json.loads(data)
        except ValueError as exc:
            raise InstallError(f"manifest is not JSON ({exc})") from exc
        if not isinstance(body, dict) or body.get("version") != MANIFEST_VERSION:
            raise InstallError("manifest has an unknown format")
        files, dirs = body.get("files"), body.get("dirs")
        if not isinstance(files, list) or not isinstance(dirs, list):
            raise InstallError("manifest has an unknown format")
        return cls(files, [str(d) for d in dirs])


def _entry(
    path: Path, data: bytes, mode: int, prior: bytes | None, prior_mode: int | None
) -> dict[str, Any]:
    return {
        "path": str(path),
        "sha256": sha256(data),
        "mode": mode,
        "prior_b64": base64.b64encode(prior).decode() if prior is not None else None,
        "prior_mode": prior_mode,
    }


def revert(changes: Changes, force: bool = False) -> list[str]:
    """Undo `changes`, newest first. Returns problems (files left alone)."""
    problems = []
    for entry in reversed(changes.files):
        path = Path(entry["path"])
        try:
            current = read_regular(path)
        except InstallError as exc:
            problems.append(f"{exc}; left alone")
            continue
        if current is None:
            continue  # already gone
        if sha256(current) != entry["sha256"] and not force:
            problems.append(f"{path} changed since install; left alone (--force removes it)")
            continue
        prior = entry.get("prior_b64")
        if prior is not None:
            atomic_write(path, base64.b64decode(prior), _prior_mode(entry))
        else:
            path.unlink()
            _fsync_dir(path.parent)
    for created in reversed(changes.dirs):
        with contextlib.suppress(FileNotFoundError, OSError):
            Path(created).rmdir()  # only if empty: never removes the author's files
    return problems


def _prior_mode(entry: dict[str, Any]) -> int:
    mode = entry.get("prior_mode")
    return int(mode) if isinstance(mode, int) else int(entry["mode"])
