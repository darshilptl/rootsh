"""seccomp probe (unprivileged, x86_64).

Loads a minimal deny-list filter (the K3 design: escape-relevant syscalls
only, never unlink or socket) in a forked child and checks that a denied
syscall fails with EPERM, ordinary work still succeeds, and the filter
survives exec. A control child without the filter shows the denied call
normally succeeds.

Usage: python3 -I seccomp_probe.py
"""

from __future__ import annotations

import ctypes
import errno
import json
import os
import subprocess
import sys
import tempfile
from collections.abc import Callable

PR_SET_NO_NEW_PRIVS, PR_SET_SECCOMP, SECCOMP_MODE_FILTER = 38, 22, 2
AUDIT_ARCH_X86_64 = 0xC000003E
X32_SYSCALL_BIT = 0x40000000
RET_KILL_PROCESS, RET_ALLOW, RET_ERRNO = 0x80000000, 0x7FFF0000, 0x00050000
LD_W_ABS, JEQ_K, JGE_K, RET_K = 0x20, 0x15, 0x35, 0x06
SECCOMP_DATA_NR, SECCOMP_DATA_ARCH = 0, 4

# K3 deny-list: debugging other processes, loading kernel code, changing
# mounts, kernel keyrings. Values are the x86_64 syscall numbers.
DENIED = {"ptrace": 101, "mount": 165, "keyctl": 250, "bpf": 321}
PTRACE_TRACEME = 0

_libc = ctypes.CDLL(None, use_errno=True)
_libc.syscall.restype = ctypes.c_long


class SockFilter(ctypes.Structure):
    _fields_ = [("code", ctypes.c_uint16), ("jt", ctypes.c_uint8), ("jf", ctypes.c_uint8), ("k", ctypes.c_uint32)]


class SockFprog(ctypes.Structure):
    _fields_ = [("len", ctypes.c_uint16), ("filter", ctypes.POINTER(SockFilter))]


def build_filter(denied: list[int]) -> list[SockFilter]:
    n = len(denied)
    program = [
        SockFilter(LD_W_ABS, 0, 0, SECCOMP_DATA_ARCH),
        SockFilter(JEQ_K, 1, 0, AUDIT_ARCH_X86_64),
        SockFilter(RET_K, 0, 0, RET_KILL_PROCESS),
        SockFilter(LD_W_ABS, 0, 0, SECCOMP_DATA_NR),
        # x32 syscalls reuse numbers with this bit set; refuse them outright.
        SockFilter(JGE_K, 0, 1, X32_SYSCALL_BIT),
        SockFilter(RET_K, 0, 0, RET_KILL_PROCESS),
    ]
    first_check = len(program)
    errno_index = first_check + n + 1
    for i, number in enumerate(denied):
        program.append(SockFilter(JEQ_K, errno_index - (first_check + i) - 1, 0, number))
    program.append(SockFilter(RET_K, 0, 0, RET_ALLOW))
    program.append(SockFilter(RET_K, 0, 0, RET_ERRNO | errno.EPERM))
    return program


def load_filter(denied: list[int]) -> None:
    program = build_filter(denied)
    array = (SockFilter * len(program))(*program)
    fprog = SockFprog(len(program), ctypes.cast(array, ctypes.POINTER(SockFilter)))
    if _libc.prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0 or _libc.prctl(PR_SET_SECCOMP, SECCOMP_MODE_FILTER, ctypes.byref(fprog), 0, 0) != 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code))


def ptrace_traceme() -> str:
    result = _libc.syscall(DENIED["ptrace"], PTRACE_TRACEME, 0, 0, 0)
    return "ok" if result == 0 else errno.errorcode.get(ctypes.get_errno(), "?")


def in_child(check: Callable[[], dict[str, str]]) -> dict[str, str]:
    read_fd, write_fd = os.pipe()
    pid = os.fork()
    if pid == 0:
        os.close(read_fd)
        try:
            payload = check()
        except Exception as exc:
            payload = {"error": repr(exc)}
        os.write(write_fd, json.dumps(payload).encode())
        os._exit(0)
    os.close(write_fd)
    with os.fdopen(read_fd) as reader:
        data = reader.read()
    os.waitpid(pid, 0)
    return json.loads(data)


EXEC_PTRACE = (
    "import ctypes;l=ctypes.CDLL(None,use_errno=True);"
    "print('ok' if l.syscall(101,0,0,0,0)==0 else ctypes.get_errno())"
)


def filtered() -> dict[str, str]:
    load_filter(sorted(DENIED.values()))
    with tempfile.NamedTemporaryFile() as scratch:
        scratch.write(b"ordinary work")
        scratch.flush()
        ordinary = "ok" if os.path.getsize(scratch.name) == 13 else "failed"
    after_exec = subprocess.run([sys.executable, "-I", "-c", EXEC_PTRACE], capture_output=True, text=True)
    errno_after_exec = after_exec.stdout.strip()
    return {
        "ptrace_traceme": ptrace_traceme(),
        "ordinary_file_io": ordinary,
        "ptrace_after_exec": errno.errorcode.get(int(errno_after_exec), errno_after_exec) if errno_after_exec.isdigit() else errno_after_exec,
    }


def main() -> int:
    report = {
        "denied_syscalls": DENIED,
        "control_no_filter": in_child(lambda: {"ptrace_traceme": ptrace_traceme()}),
        "with_filter": in_child(filtered),
    }
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
