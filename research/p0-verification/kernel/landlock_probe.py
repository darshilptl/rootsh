"""Landlock probe (unprivileged, x86_64).

Reports the kernel's Landlock ABI version, then runs each enforcement check in
a forked child so the parent stays unrestricted. A check passes when the
allowed operation succeeds and the denied one fails with EACCES (filesystem,
network) or EPERM (scoped signal).

Usage: python3 -I landlock_probe.py <scratch_dir>
"""

from __future__ import annotations

import ctypes
import errno
import json
import os
import socket
import struct
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

SYS_CREATE_RULESET, SYS_ADD_RULE, SYS_RESTRICT_SELF = 444, 445, 446
CREATE_RULESET_VERSION = 1 << 0
RULE_PATH_BENEATH, RULE_NET_PORT = 1, 2
PR_SET_NO_NEW_PRIVS = 38

FS_EXECUTE = 1 << 0
FS_WRITE_FILE = 1 << 1
FS_READ_FILE = 1 << 2
FS_READ_DIR = 1 << 3
FS_REMOVE_DIR = 1 << 4
FS_REMOVE_FILE = 1 << 5
FS_MAKE_DIR = 1 << 7
FS_MAKE_REG = 1 << 8
FS_REFER = 1 << 13  # ABI 2
FS_TRUNCATE = 1 << 14  # ABI 3
NET_CONNECT_TCP = 1 << 1  # ABI 4
SCOPE_SIGNAL = 1 << 1  # ABI 6

FS_READ = FS_EXECUTE | FS_READ_FILE | FS_READ_DIR
FS_ALL = FS_READ | FS_WRITE_FILE | FS_REMOVE_DIR | FS_REMOVE_FILE | FS_MAKE_DIR | FS_MAKE_REG | FS_REFER | FS_TRUNCATE
SYSTEM_READ_PATHS = ("/usr", "/bin", "/lib", "/lib64", "/etc", "/proc", "/dev")

_libc = ctypes.CDLL(None, use_errno=True)
_libc.syscall.restype = ctypes.c_long


class RulesetAttr(ctypes.Structure):
    _fields_ = [("handled_access_fs", ctypes.c_uint64), ("handled_access_net", ctypes.c_uint64), ("scoped", ctypes.c_uint64)]


def path_beneath_attr(allowed_access: int, parent_fd: int) -> ctypes.Array[ctypes.c_char]:
    # struct landlock_path_beneath_attr is packed (12 bytes); building the
    # bytes directly avoids ctypes' version-dependent packed-layout rules.
    return ctypes.create_string_buffer(struct.pack("<Qi", allowed_access, parent_fd), 12)


class NetPortAttr(ctypes.Structure):
    _fields_ = [("allowed_access", ctypes.c_uint64), ("port", ctypes.c_uint64)]


def _check(result: int) -> int:
    if result < 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code))
    return result


def abi_version() -> int:
    return _check(_libc.syscall(SYS_CREATE_RULESET, None, ctypes.c_size_t(0), ctypes.c_uint32(CREATE_RULESET_VERSION)))


def restrict(
    *,
    paths: dict[str, int],
    handled_fs: int = 0,
    tcp_ports: tuple[int, ...] | None = None,
    scoped: int = 0,
) -> None:
    """Enforce a Landlock domain on the calling process (irreversible)."""
    attr = RulesetAttr(handled_fs, NET_CONNECT_TCP if tcp_ports is not None else 0, scoped)
    ruleset = _check(_libc.syscall(SYS_CREATE_RULESET, ctypes.byref(attr), ctypes.c_size_t(ctypes.sizeof(attr)), ctypes.c_uint32(0)))
    try:
        for path, access in paths.items():
            fd = os.open(path, os.O_PATH | os.O_CLOEXEC)
            try:
                rule = path_beneath_attr(access, fd)
                _check(_libc.syscall(SYS_ADD_RULE, ruleset, RULE_PATH_BENEATH, rule, ctypes.c_uint32(0)))
            finally:
                os.close(fd)
        for port in tcp_ports or ():
            rule = NetPortAttr(NET_CONNECT_TCP, port)
            _check(_libc.syscall(SYS_ADD_RULE, ruleset, RULE_NET_PORT, ctypes.byref(rule), ctypes.c_uint32(0)))
        _check(_libc.prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0))
        _check(_libc.syscall(SYS_RESTRICT_SELF, ruleset, ctypes.c_uint32(0)))
    finally:
        os.close(ruleset)


def outcome(action: Callable[[], object]) -> str:
    try:
        action()
    except OSError as exc:
        return errno.errorcode.get(exc.errno or 0, str(exc.errno))
    return "ok"


def in_child(check: Callable[[], dict[str, str]]) -> dict[str, str]:
    """Run *check* in a forked child; Landlock domains cannot be lifted."""
    read_fd, write_fd = os.pipe()
    pid = os.fork()
    if pid == 0:
        os.close(read_fd)
        try:
            payload = check()
        except Exception as exc:  # report, never let the child fall into parent code
            payload = {"error": repr(exc)}
        os.write(write_fd, json.dumps(payload).encode())
        os._exit(0)
    os.close(write_fd)
    with os.fdopen(read_fd) as reader:
        data = reader.read()
    os.waitpid(pid, 0)
    return json.loads(data)


def filesystem_check(root: Path) -> dict[str, str]:
    allowed, protected, secret = root / "allowed", root / "protected", root / "secret"
    for directory in (allowed, protected, secret):
        directory.mkdir(parents=True, exist_ok=True)
    (protected / "keep.txt").write_text("keep")
    (protected / "big.txt").write_text("data")
    (secret / "key").write_text("secret")

    def check() -> dict[str, str]:
        paths = {p: FS_READ for p in SYSTEM_READ_PATHS if os.path.exists(p)}
        paths[str(allowed)] = FS_ALL
        paths[str(protected)] = FS_READ
        restrict(paths=paths, handled_fs=FS_ALL)
        shell = subprocess.run(["sh", "-c", f"touch '{protected}/via_exec'"], capture_output=True, text=True)
        return {
            "write_allowed_dir": outcome(lambda: (allowed / "new.txt").write_text("x")),
            "write_protected_dir": outcome(lambda: (protected / "new.txt").write_text("x")),
            "unlink_protected": outcome(lambda: (protected / "keep.txt").unlink()),
            "rename_out_of_protected": outcome(lambda: os.rename(protected / "keep.txt", allowed / "moved.txt")),
            "truncate_protected": outcome(lambda: os.truncate(protected / "big.txt", 0)),
            "read_protected": outcome(lambda: (protected / "keep.txt").read_text()),
            "read_unlisted_secret": outcome(lambda: (secret / "key").read_text()),
            "child_exec_write_protected": "ok" if shell.returncode == 0 else f"exit {shell.returncode}: {shell.stderr.strip()[-80:]}",
        }

    result = in_child(check)
    result["protected_files_intact"] = str((protected / "keep.txt").exists() and (protected / "big.txt").read_text() == "data")
    return result


def network_check() -> dict[str, str]:
    listeners = [socket.create_server(("127.0.0.1", 0)) for _ in range(2)]
    allowed_port, denied_port = (s.getsockname()[1] for s in listeners)

    def connect(port: int) -> None:
        with socket.create_connection(("127.0.0.1", port), timeout=2):
            pass

    def check() -> dict[str, str]:
        restrict(paths={}, tcp_ports=(allowed_port,))
        return {"connect_allowed_port": outcome(lambda: connect(allowed_port)), "connect_denied_port": outcome(lambda: connect(denied_port))}

    try:
        return in_child(check)
    finally:
        for listener in listeners:
            listener.close()


def signal_scope_check() -> dict[str, str]:
    parent = os.getpid()

    def check() -> dict[str, str]:
        before = outcome(lambda: os.kill(parent, 0))
        restrict(paths={}, scoped=SCOPE_SIGNAL)
        return {"signal_parent_before": before, "signal_parent_after_scope": outcome(lambda: os.kill(parent, 0))}

    return in_child(check)


def main(scratch: Path) -> int:
    abi = abi_version()
    report: dict[str, object] = {"abi": abi}
    report["filesystem"] = filesystem_check(scratch)
    report["network"] = network_check() if abi >= 4 else "requires ABI 4"
    report["signal_scope"] = signal_scope_check() if abi >= 6 else "requires ABI 6"
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1]).resolve()))
