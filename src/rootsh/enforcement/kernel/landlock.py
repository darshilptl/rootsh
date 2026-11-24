"""K1: Landlock filesystem allowlist, TCP ports and scoping (design 09 §5).

Unprivileged, inherited by every child, and impossible to remove. Each rule
names a path that must exist when the ruleset is built; a missing path is an
error, so a typo cannot silently widen or narrow the sandbox.
"""

from __future__ import annotations

import ctypes
import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

SYS_CREATE_RULESET, SYS_ADD_RULE, SYS_RESTRICT_SELF = 444, 445, 446
CREATE_RULESET_VERSION = 1
RULE_PATH_BENEATH, RULE_NET_PORT = 1, 2
NET_BIND_TCP, NET_CONNECT_TCP = 1 << 0, 1 << 1
SCOPE_ABSTRACT_UNIX_SOCKET, SCOPE_SIGNAL = 1 << 0, 1 << 1
RESTRICT_SELF_LOG_NEW_EXEC_ON = 1 << 1  # ABI 7
PR_SET_NO_NEW_PRIVS = 38
O_PATH, O_CLOEXEC = 0o10000000, 0o2000000
MIN_ABI = 6  # scoping arrived in ABI 6

EXECUTE, WRITE_FILE, READ_FILE, READ_DIR = 1 << 0, 1 << 1, 1 << 2, 1 << 3
REFER, TRUNCATE, IOCTL_DEV = 1 << 13, 1 << 14, 1 << 15
READ = EXECUTE | READ_FILE | READ_DIR
FILE_ONLY = EXECUTE | WRITE_FILE | READ_FILE | TRUNCATE | IOCTL_DEV


class LandlockError(OSError):
    """Landlock could not be applied; the agent must not start."""


def fs_mask(abi: int) -> int:
    """Every filesystem right the running ABI knows (ABI 1: bits 0-12)."""
    mask = (1 << 13) - 1
    if abi >= 2:
        mask |= REFER
    if abi >= 3:
        mask |= TRUNCATE
    if abi >= 5:
        mask |= IOCTL_DEV
    return mask


class _RulesetAttr(ctypes.Structure):
    _fields_ = (
        ("handled_access_fs", ctypes.c_uint64),
        ("handled_access_net", ctypes.c_uint64),
        ("scoped", ctypes.c_uint64),
    )


class _PathBeneath(ctypes.Structure):
    _pack_ = 1  # the kernel struct is packed
    _fields_ = (("allowed_access", ctypes.c_uint64), ("parent_fd", ctypes.c_int32))


class _NetPort(ctypes.Structure):
    _fields_ = (("allowed_access", ctypes.c_uint64), ("port", ctypes.c_uint64))


def _libc() -> ctypes.CDLL:
    libc = ctypes.CDLL(None, use_errno=True)
    libc.syscall.restype = ctypes.c_long
    return libc


def _check(result: int, what: str) -> int:
    if result < 0:
        errno = ctypes.get_errno()
        raise LandlockError(errno, f"{what}: {os.strerror(errno)}")
    return result


def abi_version() -> int:
    return _check(
        _libc().syscall(
            SYS_CREATE_RULESET, None, ctypes.c_size_t(0), ctypes.c_uint32(CREATE_RULESET_VERSION)
        ),
        "landlock ABI query",
    )


@dataclass(frozen=True, slots=True)
class Rules:
    read_write: Sequence[Path] = ()
    read_only: Sequence[Path] = ()
    connect_ports: Sequence[int] = (443,)
    bind_ports: Sequence[int] = ()  # bind + connect (OpenCode's own server)
    # False: network and scope rules only, the filesystem is left to bwrap
    # (the P4 collection sandbox, rootsh.research.collect.confine).
    filesystem: bool = True


def apply(rules: Rules) -> int:
    """Restrict this process and every future child. Irreversible. Returns
    the ABI used."""
    abi = abi_version()
    if abi < MIN_ABI:
        raise LandlockError(0, f"Landlock ABI {abi} < {MIN_ABI}")
    if not rules.filesystem and (rules.read_write or rules.read_only):
        raise LandlockError(0, "path rules given with filesystem=False")
    libc = _libc()
    handled = fs_mask(abi) if rules.filesystem else 0
    attr = _RulesetAttr(
        handled, NET_BIND_TCP | NET_CONNECT_TCP, SCOPE_ABSTRACT_UNIX_SOCKET | SCOPE_SIGNAL
    )
    ruleset = _check(
        libc.syscall(
            SYS_CREATE_RULESET, ctypes.byref(attr), ctypes.c_size_t(ctypes.sizeof(attr)), 0
        ),
        "create ruleset",
    )
    try:
        for path in rules.read_write:
            _add_path(libc, ruleset, path, handled)
        for path in rules.read_only:
            _add_path(libc, ruleset, path, READ & handled)
        ports = [(NET_CONNECT_TCP, p) for p in rules.connect_ports]
        ports += [(NET_BIND_TCP | NET_CONNECT_TCP, p) for p in rules.bind_ports]
        for access, port in ports:
            rule = _NetPort(access, port)
            _check(
                libc.syscall(SYS_ADD_RULE, ruleset, RULE_NET_PORT, ctypes.byref(rule), 0),
                f"allow port {port}",
            )
        _check(libc.prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0), "no_new_privs")
        flags = RESTRICT_SELF_LOG_NEW_EXEC_ON if abi >= 7 else 0
        _check(libc.syscall(SYS_RESTRICT_SELF, ruleset, flags), "restrict self")
    finally:
        os.close(ruleset)
    return abi


def _add_path(libc: ctypes.CDLL, ruleset: int, path: Path, access: int) -> None:
    try:
        fd = os.open(path, O_PATH | O_CLOEXEC)
    except OSError as exc:
        raise LandlockError(exc.errno or 0, f"rule path {path}: {exc.strerror}") from exc
    try:
        if not os.path.isdir(f"/proc/self/fd/{fd}"):
            access &= FILE_ONLY  # directory rights are invalid on a file
        rule = _PathBeneath(access, fd)
        _check(
            libc.syscall(SYS_ADD_RULE, ruleset, RULE_PATH_BENEATH, ctypes.byref(rule), 0),
            f"allow {path}",
        )
    finally:
        os.close(fd)
