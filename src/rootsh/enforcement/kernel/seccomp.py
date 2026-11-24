"""K3: a small seccomp denylist of escape-relevant syscalls (design 09 §5).

Native x86-64 only: another architecture or the x32 ABI kills the process,
so a denied syscall cannot be reached under a different number. Denied
calls fail with EPERM; nothing common (unlink, socket, clone) is touched.
"""

from __future__ import annotations

import ctypes
import errno
import os
import platform
import struct

SYS_SECCOMP = 317
SECCOMP_SET_MODE_FILTER = 1
SECCOMP_FILTER_FLAG_LOG = 1 << 1
PR_SET_NO_NEW_PRIVS = 38
AUDIT_ARCH_X86_64 = 0xC000003E
X32_SYSCALL_BIT = 0x40000000

RET_KILL_PROCESS = 0x80000000
RET_ERRNO = 0x00050000
RET_ALLOW = 0x7FFF0000
LD_W_ABS, JEQ_K, JGE_K, RET_K = 0x20, 0x15, 0x35, 0x06
OFF_NR, OFF_ARCH, OFF_ARG1 = 0, 4, 24  # struct seccomp_data, little-endian low word

IOCTL = 16
TIOCSTI, TIOCLINUX = 0x5412, 0x541C

DENIED: dict[str, int] = {
    "ptrace": 101,
    "process_vm_readv": 310,
    "process_vm_writev": 311,
    "bpf": 321,
    "perf_event_open": 298,
    "userfaultfd": 323,
    "mount": 165,
    "umount2": 166,
    "pivot_root": 155,
    "open_tree": 428,
    "move_mount": 429,
    "fsopen": 430,
    "fsconfig": 431,
    "fsmount": 432,
    "fspick": 433,
    "mount_setattr": 442,
    "open_tree_attr": 467,
    "keyctl": 250,
    "add_key": 248,
    "request_key": 249,
    "init_module": 175,
    "finit_module": 313,
    "delete_module": 176,
    "kexec_load": 246,
    "kexec_file_load": 320,
}


class SeccompError(OSError):
    """The filter could not be installed; the agent must not start."""


def _insn(code: int, jt: int, jf: int, k: int) -> bytes:
    return struct.pack("<HBBI", code, jt, jf, k)


def program() -> bytes:
    """The classic-BPF filter, as the kernel reads it."""
    deny = RET_ERRNO | errno.EPERM
    code = [
        _insn(LD_W_ABS, 0, 0, OFF_ARCH),
        _insn(JEQ_K, 1, 0, AUDIT_ARCH_X86_64),
        _insn(RET_K, 0, 0, RET_KILL_PROCESS),
        _insn(LD_W_ABS, 0, 0, OFF_NR),
        _insn(JGE_K, 0, 1, X32_SYSCALL_BIT),
        _insn(RET_K, 0, 0, RET_KILL_PROCESS),
    ]
    for number in sorted(DENIED.values()):
        code += [_insn(JEQ_K, 0, 1, number), _insn(RET_K, 0, 0, deny)]
    code += [
        _insn(JEQ_K, 0, 4, IOCTL),
        _insn(LD_W_ABS, 0, 0, OFF_ARG1),  # cmd is an unsigned int: low word only
        _insn(JEQ_K, 1, 0, TIOCSTI),
        _insn(JEQ_K, 0, 1, TIOCLINUX),
        _insn(RET_K, 0, 0, deny),
        _insn(RET_K, 0, 0, RET_ALLOW),
    ]
    return b"".join(code)


class _Fprog(ctypes.Structure):
    _fields_ = (("len", ctypes.c_ushort), ("filter", ctypes.c_void_p))


def apply() -> None:
    """Install the filter on this thread and every future child. Irreversible."""
    if platform.machine() != "x86_64":
        raise SeccompError(0, f"seccomp filter is x86-64 only, not {platform.machine()}")
    raw = program()
    buffer = ctypes.create_string_buffer(raw, len(raw))
    prog = _Fprog(len(raw) // 8, ctypes.cast(buffer, ctypes.c_void_p).value)
    libc = ctypes.CDLL(None, use_errno=True)
    libc.syscall.restype = ctypes.c_long
    if libc.prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
        err = ctypes.get_errno()
        raise SeccompError(err, f"no_new_privs: {os.strerror(err)}")
    result = libc.syscall(
        SYS_SECCOMP, SECCOMP_SET_MODE_FILTER, SECCOMP_FILTER_FLAG_LOG, ctypes.byref(prog)
    )
    if result != 0:
        err = ctypes.get_errno()
        raise SeccompError(err, f"install filter: {os.strerror(err)}")
