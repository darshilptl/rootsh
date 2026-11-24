"""The sandbox `rootsh run` starts a real agent in (design 09 §4-§5).

Pure: `plan()` reads only the filesystem's shape (which paths exist) and
returns argv plus the confine spec; it starts nothing.
"""

from __future__ import annotations

import os
import shlex
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from rootsh.events import Agent
from rootsh.kernel import apparmor
from rootsh.kernel.confine import Spec

_USR_LINKS = (
    ("usr/bin", "/bin"),
    ("usr/sbin", "/sbin"),
    ("usr/lib", "/lib"),
    ("usr/lib64", "/lib64"),
)
_SYSTEM_RO = ("/usr", "/etc", "/run/systemd/resolve")
ENV_ALLOWED = ("TERM", "COLORTERM", "LANG", "USER", "LOGNAME", "SHELL", "EDITOR", "VISUAL", "PATH")
XDG_RUNTIME = "/tmp/rootsh-xdg"  # noqa: S108 - inside the sandbox's private tmpfs

# Home paths, relative to $HOME. rw: the agent's own state. ro: code and config.
AGENT_RW: Mapping[Agent, tuple[str, ...]] = {
    Agent.CLAUDE_CODE: (".claude", ".claude.json"),
    Agent.OPENCODE: (".local/share/opencode", ".local/state/opencode", ".cache/opencode"),
}
AGENT_RO: Mapping[Agent, tuple[str, ...]] = {
    Agent.CLAUDE_CODE: (".local/share/claude", ".claude/settings.json"),
    Agent.OPENCODE: (".opencode", ".config/opencode"),
}
COMMON_RO = (".local/bin", ".nvm", ".gitconfig")

# OpenCode's default TUI attaches to a host background service, which would
# run tools outside the sandbox. Start a private server inside instead (P4
# pattern); $1 port, $2 the TUI's directory, then the user's arguments.
_OPENCODE_SCRIPT = """
port="$1"; dir="$2"; shift 2
opencode serve --hostname 127.0.0.1 --port "$port" > "$XDG_RUNTIME_DIR/opencode-server.log" 2>&1 &
i=0
until curl -s -o /dev/null "http://127.0.0.1:$port/"; do
  i=$((i + 1)); [ "$i" -ge 150 ] && { echo "rootsh: opencode server not ready" >&2; exit 70; }
  sleep 0.2
done
exec opencode --server "http://127.0.0.1:$port" "$@" "$dir"
"""


@dataclass(frozen=True, slots=True)
class Host:
    home: Path
    uid: int
    runtime_dir: Path
    event_socket: Path
    control_socket: Path
    python: Path  # the interpreter the installed hooks run
    code: tuple[Path, ...]  # rootsh's venv, base Python, package source

    @classmethod
    def current(cls, event_socket: Path, control_socket: Path) -> Host:
        return cls(
            Path.home(),
            os.getuid(),
            Path(f"/run/user/{os.getuid()}"),
            event_socket,
            control_socket,
            Path(sys.executable),
            code_paths(),
        )


def code_paths() -> tuple[Path, ...]:
    """rootsh's interpreter and code: the venv, its base Python, and the
    package's parent (`src/` for a checkout, never the checkout itself)."""
    import rootsh

    package_root = Path(rootsh.__file__).resolve().parent.parent
    return tuple(sorted({Path(sys.prefix), Path(sys.base_prefix), package_root}))


@dataclass(frozen=True, slots=True)
class Plan:
    argv: list[str]
    spec: Spec
    layers: tuple[str, ...]


def symlink_hops(path: Path, limit: int = 40) -> list[Path]:
    """Every symlink met while resolving `path`, component by component, so
    binding each one at its own path makes `path` resolve in the sandbox."""
    hops: list[Path] = []
    pending = list(path.parts[1:]) if path.is_absolute() else []
    current = Path("/")
    while pending:
        if len(hops) > limit:
            raise ValueError(f"too many symlinks resolving {path}")
        candidate = current / pending.pop(0)
        if candidate.is_symlink():
            hops.append(candidate)
            target = Path(os.readlink(candidate))
            target = target if target.is_absolute() else candidate.parent / target
            pending = list(target.parts[1:]) + pending
            current = Path("/")
        else:
            current = candidate
    return hops


def _existing(paths: Sequence[Path]) -> list[Path]:
    return [p for p in paths if p.exists() or p.is_symlink()]


def plan(
    agent: Agent,
    host: Host,
    project: Path,
    agent_args: Sequence[str],
    env: Mapping[str, str],
    *,
    extra_env: Sequence[str] = (),
    port: int | None = None,
    password: str | None = None,
    k2: bool = False,
    probe: Sequence[str] | None = None,
) -> Plan:
    """argv (outermost first, from aa-exec or bwrap) for one launch."""
    project = project.resolve(strict=True)
    if project == host.home or host.home.is_relative_to(project):
        raise ValueError(f"project {project} would expose the whole home directory")
    for code in host.code:
        if project.is_relative_to(code) or code.is_relative_to(project):
            raise ValueError(f"project {project} overlaps rootsh's own code {code}")
    rw = _existing([host.home / p for p in AGENT_RW[agent]])
    ro = _existing([host.home / p for p in (*AGENT_RO[agent], *COMMON_RO)])
    ro += [c for c in host.code if c.exists()]
    ro += [h for h in symlink_hops(host.python) if not any(h.is_relative_to(c) for c in ro)]
    socket_dir = host.event_socket.parent
    if host.control_socket.parent == socket_dir:
        raise ValueError("event and control sockets share a directory")

    argv = ["bwrap", "--unshare-all", "--share-net", "--die-with-parent", "--clearenv"]
    for system in _SYSTEM_RO:
        if Path(system).exists():
            argv += ["--ro-bind", system, system]
    for target, link in _USR_LINKS:
        argv += ["--symlink", target, link]
    argv += ["--dev", "/dev", "--proc", "/proc", "--tmpfs", "/tmp"]  # noqa: S108
    argv += ["--tmpfs", str(host.home), "--dir", XDG_RUNTIME, "--chmod", "0700", XDG_RUNTIME]
    for path in rw:  # binds are applied in order: rw state first, ro code/config over it
        argv += ["--bind", str(path), str(path)]
    for path in ro:
        argv += ["--ro-bind", str(path), str(path)]
    if socket_dir.exists():
        argv += ["--ro-bind", str(socket_dir), str(socket_dir)]
    argv += ["--bind", str(project), str(project), "--chdir", str(project)]

    environment = {k: env[k] for k in ENV_ALLOWED if k in env}
    environment |= {k: v for k, v in env.items() if k.startswith("LC_")}
    environment |= {k: env[k] for k in extra_env if k in env}  # named by the author
    environment |= {"HOME": str(host.home), "XDG_RUNTIME_DIR": XDG_RUNTIME, "PWD": str(project)}
    bind_ports: tuple[int, ...] = ()
    if agent is Agent.OPENCODE:
        if port is None or password is None:
            raise ValueError("OpenCode launches need a local port and a server password")
        environment |= {"OPENCODE_SERVER_PASSWORD": password, "OPENCODE_PASSWORD": password}
        bind_ports = (port,)
        command = ["sh", "-c", _OPENCODE_SCRIPT, "rootsh-opencode", str(port), str(project)]
        command += list(agent_args)
    else:
        command = ["claude", *agent_args]
    if probe is not None:  # tests: a probe instead of the agent, same wrapping
        command = list(probe)
    for key in sorted(environment):
        argv += ["--setenv", key, environment[key]]

    read_only = [*_SYSTEM_RO, "/proc", str(host.home), *map(str, ro)]
    if socket_dir.exists():
        read_only.append(str(socket_dir))
    spec = Spec(
        read_write=tuple(str(p) for p in (project, *rw, Path("/tmp"), Path("/dev"))),  # noqa: S108
        read_only=tuple(p for p in read_only if p == str(host.home) or Path(p).exists()),
        bind_ports=bind_ports,
        apparmor_label=apparmor.AGENT if k2 else None,
    )
    confined = [str(host.python), "-I", "-m", "rootsh.kernel.confine", spec.dump(), "--", *command]
    argv += ["--", *confined]
    if k2:
        argv = [apparmor.AA_EXEC, "-p", apparmor.OUTER, "--", *argv]
    layers = ("bwrap", "K1", "K3", *(("K2",) if k2 else ()))
    return Plan(argv, spec, layers)


def describe(argv: Sequence[str]) -> str:
    return shlex.join(argv)
