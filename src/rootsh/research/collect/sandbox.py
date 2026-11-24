"""The bubblewrap sandbox every collection run executes in (design 04 §5)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from rootsh.analysis.paths import WORKDIR as WORKDIR

RECORD_DIR = "/rec"
HOSTNAME = "sandbox"

# Merged-/usr layout of Ubuntu 26.04 [RUN]: these are symlinks into /usr.
_USR_LINKS = (
    ("usr/bin", "/bin"),
    ("usr/sbin", "/sbin"),
    ("usr/lib", "/lib"),
    ("usr/lib64", "/lib64"),
)
_SYSTEM_RO = ("/usr", "/etc", "/run/systemd/resolve")


@dataclass(frozen=True, slots=True)
class SandboxSpec:
    workspace: Path  # bound read-write at WORKDIR
    record_dir: Path  # bound read-write at RECORD_DIR: where the hooks write
    home: Path  # the real $HOME path inside the sandbox
    home_dir: Path  # a per-run directory mounted there
    read_only: Sequence[Path]  # extra paths bound read-only at the same location
    env: Mapping[str, str] = field(default_factory=dict)


def sandbox_argv(spec: SandboxSpec, command: Sequence[str]) -> list[str]:
    """argv that runs `command` inside the sandbox. Pure: touches nothing."""
    argv = [
        "bwrap",
        "--unshare-all",
        "--share-net",  # both agents call their model APIs
        "--die-with-parent",
        "--new-session",
        "--clearenv",
        "--hostname",
        HOSTNAME,  # the host's name stays out of traces (own UTS namespace)
    ]
    for path in _SYSTEM_RO:
        argv += ["--ro-bind", path, path]
    for target, link in _USR_LINKS:
        argv += ["--symlink", target, link]
    # A fresh, private tmpfs inside the sandbox, not a host temp path.
    argv += ["--dev", "/dev", "--proc", "/proc", "--tmpfs", "/tmp"]  # noqa: S108
    argv += ["--bind", str(spec.home_dir), str(spec.home)]
    for extra in spec.read_only:
        argv += ["--ro-bind", str(extra), str(extra)]
    argv += ["--bind", str(spec.record_dir), RECORD_DIR]
    argv += ["--bind", str(spec.workspace), WORKDIR, "--chdir", WORKDIR]
    env = {"HOME": str(spec.home), "PWD": WORKDIR, "LANG": "C.UTF-8", **spec.env}
    for key in sorted(env):
        argv += ["--setenv", key, env[key]]
    return [*argv, "--", *command]
