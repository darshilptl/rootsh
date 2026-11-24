"""`rootsh run claude|opencode`: start an agent under every layer (design 09 §1).

1. pick a launch id; check K2 (AppArmor) state;
2. register {launch, agent, project, home, layers} on the control socket;
3. run `systemd-run --user --scope --unit rootsh-agent-<id>` around the
   sandbox, in the foreground, so the agent's whole tree is in that scope;
4. unregister, whatever happened.

The launcher itself stays outside the scope. Every failure before the agent
starts is reported and the agent is not started.
"""

from __future__ import annotations

import os
import secrets
import socket
import subprocess
import sys
import uuid
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from rootsh.analysis.events import Agent
from rootsh.enforcement.daemon import client
from rootsh.enforcement.kernel import apparmor, landlock
from rootsh.enforcement.run.sandbox import Host, plan

EXIT_REFUSED = 126
Runner = Callable[[Sequence[str]], int]


class LaunchError(RuntimeError):
    pass


def k2_state(
    profile_path: Path = apparmor.PROFILE_PATH, loaded: Callable[[], bool] = apparmor.loaded
) -> bool:
    """True: K2 is required and loaded. False: not installed. Raises when the
    profile is installed but not loaded (a required layer is missing)."""
    if loaded():
        return True
    if profile_path.exists():
        raise LaunchError(
            f"{profile_path} is installed but profile {apparmor.OUTER} is not loaded; "
            "run `sudo apparmor_parser -r` on it or `rootsh doctor`"
        )
    return False


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _foreground(argv: Sequence[str]) -> int:
    return subprocess.call(list(argv))  # noqa: S603 - argv built by plan()


def launch(
    agent: Agent,
    project: Path,
    agent_args: Sequence[str],
    *,
    host: Host,
    env: Mapping[str, str],
    extra_env: Sequence[str] = (),
    runner: Runner = _foreground,
    k2: bool | None = None,
    out: Callable[[str], None] = lambda line: print(line, file=sys.stderr),
) -> int:
    abi = landlock.abi_version()
    if abi < landlock.MIN_ABI:
        raise LaunchError(f"Landlock ABI {abi} < {landlock.MIN_ABI}")
    use_k2 = k2_state() if k2 is None else k2
    if not use_k2:
        out(f"rootsh run: WARNING K2 absent ({apparmor.PROFILE_PATH} not installed)")
    for name in extra_env:
        if name not in env:
            raise LaunchError(f"--env {name}: not set in this shell")
    launch_id = uuid.uuid4().hex
    port = free_port() if agent is Agent.OPENCODE else None
    password = secrets.token_urlsafe(24) if agent is Agent.OPENCODE else None
    planned = plan(
        agent,
        host,
        project,
        agent_args,
        env,
        extra_env=extra_env,
        port=port,
        password=password,
        k2=use_k2,
    )
    unit = f"rootsh-agent-{launch_id}"
    argv = ["systemd-run", "--user", "--scope", "--quiet", "--collect", f"--unit={unit}"]
    argv += ["--", *planned.argv]
    body = {
        "op": "register",
        "launch": launch_id,
        "agent": agent.value,
        "project": str(project.resolve(strict=True)),
        "home": str(host.home),
        "layers": list(planned.layers),
    }
    try:
        reply = client.request(host.control_socket, body)
    except (OSError, ValueError) as exc:
        raise LaunchError(
            f"daemon control socket unreachable ({exc}); is rootsh installed?"
        ) from exc
    if reply.get("ok") is not True:
        raise LaunchError(f"daemon refused the launch: {reply.get('error')}")
    out(f"rootsh run: launch {launch_id[:12]} ({', '.join(planned.layers)}) in {unit}.scope")
    try:
        return runner(argv)
    finally:
        try:
            client.request(host.control_socket, {"op": "unregister", "launch": launch_id})
        except (OSError, ValueError) as exc:
            out(f"rootsh run: could not unregister launch {launch_id[:12]}: {exc}")


def main(
    agent: str,
    project: Path | None,
    agent_args: Sequence[str],
    extra_env: Sequence[str],
    event_socket: Path,
    control_socket: Path,
) -> int:
    try:
        host = Host.current(event_socket, control_socket)
        return launch(
            Agent(agent),
            project or Path.cwd(),
            agent_args,
            host=host,
            env=os.environ,
            extra_env=extra_env,
        )
    except (LaunchError, ValueError, OSError) as exc:
        print(f"rootsh run: refusing to start: {exc}", file=sys.stderr)
        return EXIT_REFUSED
