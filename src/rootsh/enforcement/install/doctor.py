"""`rootsh doctor`: is every installed layer present and actually blocking?
(docs/phases/08-hooks-install.md §5)

"Actually blocking" is checked by running the *installed* commands, as the
agents would, with synthetic events: a write to Claude Code's own settings
(the I1 defense invariant, so the expected answer does not depend on the
model) must be refused, and a harmless read must not be.
"""

from __future__ import annotations

import contextlib
import json
import os
import shlex
import subprocess
import uuid
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

from rootsh.enforcement.daemon import client
from rootsh.enforcement.install import Layout, Systemctl, ping, render
from rootsh.enforcement.install.files import Changes, InstallError, read_regular, sha256
from rootsh.enforcement.kernel import apparmor

RUN_TIMEOUT_S = 40
Runner = Callable[[Sequence[str], str], subprocess.CompletedProcess[str]]


@dataclass(frozen=True, slots=True)
class Check:
    name: str
    ok: bool | None  # None: skipped
    detail: str

    def line(self) -> str:
        mark = "SKIP" if self.ok is None else "PASS" if self.ok else "FAIL"
        return f"{mark}  {self.name}: {self.detail}"


def run_command(argv: Sequence[str], stdin: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - argv read from rootsh's own installed files
        list(argv), input=stdin, capture_output=True, text=True, timeout=RUN_TIMEOUT_S, check=False
    )


def _claude_payload(session: str, home: Path, hook: str, **extra: object) -> str:
    base = {
        "session_id": session,
        "prompt_id": f"{session}-p1",
        "transcript_path": "/dev/null",
        "cwd": str(home),
        "permission_mode": "default",
        "hook_event_name": hook,
    }
    return json.dumps(base | extra)


def managed_checks(layout: Layout) -> tuple[list[Check], str | None]:
    path = layout.at(layout.managed)
    try:
        data = read_regular(path)
    except InstallError as exc:
        return [Check("managed settings", False, str(exc))], None
    if data is None:
        return [
            Check(
                "managed settings",
                False,
                f"{layout.managed} missing (sudo rootsh install --system)",
            )
        ], None
    checks = []
    info = path.stat()
    if layout.root == Path("/"):
        owner_ok = info.st_uid == 0 and info.st_mode & 0o022 == 0
        checks.append(
            Check(
                "managed settings owner",
                owner_ok,
                f"uid {info.st_uid}, mode {info.st_mode & 0o7777:o}",
            )
        )
    else:
        checks.append(Check("managed settings owner", None, "not checked under --root"))
    try:
        ours = render.rootsh_hook_entries(data)
    except InstallError as exc:
        return [*checks, Check("managed settings hooks", False, str(exc))], None
    commands: set[str] = set()
    missing = []
    for event in render.CLAUDE_HOOK_EVENTS:
        hooks = [h for e in ours.get(event, []) for h in e.get("hooks", []) if isinstance(h, dict)]
        good = [
            h
            for h in hooks
            if h.get("onFailure") == "block" and render.HOOK_MODULE in str(h.get("command"))
        ]
        if not good:
            missing.append(event)
        commands.update(str(h["command"]) for h in good)
    if missing or len(commands) != 1:
        detail = (
            f"missing or not fail-closed: {missing}"
            if missing
            else f"{len(commands)} different commands"
        )
        return [*checks, Check("managed settings hooks", False, detail)], None
    command = commands.pop()
    checks.append(
        Check(
            "managed settings hooks",
            True,
            f"{len(render.CLAUDE_HOOK_EVENTS)} events, onFailure block",
        )
    )
    python = Path(shlex.split(command)[0])
    checks.append(Check("hook interpreter", os.access(python, os.X_OK), str(python)))
    return checks, command


def claude_blocking(command: str, home: Path, run: Runner) -> list[Check]:
    argv = shlex.split(command)
    session = f"rootsh-doctor-{uuid.uuid4().hex}"
    prompt = _claude_payload(session, home, "UserPromptSubmit", prompt="rootsh doctor self-test")
    done = run(argv, prompt)
    checks = [
        Check(
            "claude hook records prompt",
            done.returncode == 0,
            f"exit {done.returncode} {done.stderr.strip()[:200]}",
        )
    ]
    target = str(home / ".claude" / "settings.json")
    block = _claude_payload(
        session, home, "PreToolUse", tool_name="Write", tool_use_id=f"{session}-t1",
        tool_input={"file_path": target, "content": "{}"},
    )  # fmt: skip
    done = run(argv, block)
    try:
        output = json.loads(done.stdout)["hookSpecificOutput"]
        denied = (
            done.returncode == 0
            and output["permissionDecision"] == "deny"
            and "(defense)" in output["permissionDecisionReason"]
        )
    except (ValueError, KeyError, TypeError):
        denied = False
    checks.append(
        Check(
            "claude hook blocks defense write", denied, (done.stdout or done.stderr).strip()[:200]
        )
    )
    allow = _claude_payload(
        session, home, "PreToolUse", tool_name="Bash", tool_use_id=f"{session}-t2",
        tool_input={"command": "pwd"},
    )  # fmt: skip
    done = run(argv, allow)
    quiet = done.returncode == 0 and not done.stdout.strip()
    checks.append(
        Check(
            "claude hook stays silent on a read",
            quiet,
            f"exit {done.returncode} {done.stdout.strip()[:200]}",
        )
    )
    return checks


def opencode_blocking(layout: Layout, run: Runner) -> list[Check]:
    base = [
        str(layout.python),
        "-I",
        "-m",
        "rootsh.enforcement.hooks.opencode",
        "--socket",
        str(layout.socket),
        "--hook",
    ]
    session = f"rootsh-doctor-{uuid.uuid4().hex}"
    target = str(layout.home / ".claude" / "settings.json")
    write = {
        "sessionID": session,
        "id": f"{session}-t1",
        "tool": "write",
        "input": {"path": target, "content": "{}"},
    }
    done = run([*base, "tool.execute.before"], json.dumps(write))
    checks = [
        Check(
            "opencode bridge blocks defense write",
            done.returncode == 3 and "(defense)" in done.stdout,
            done.stdout.strip()[:200],
        )
    ]
    read = {
        "sessionID": session,
        "id": f"{session}-t2",
        "tool": "shell",
        "input": {"command": "pwd", "workdir": str(layout.home)},
    }
    done = run([*base, "tool.execute.before"], json.dumps(read))
    checks.append(
        Check("opencode bridge allows a read", done.returncode == 0, done.stdout.strip()[:200])
    )
    return checks


def user_checks(layout: Layout, systemctl: Systemctl | None) -> list[Check]:
    try:
        data = read_regular(layout.at(layout.user_manifest))
    except InstallError as exc:
        return [Check("user install", False, str(exc))]
    if data is None:
        return [Check("user install", False, "no manifest (rootsh install)")]
    changes = Changes.load(data)
    checks = []
    for name, path in (("daemon unit", layout.unit), ("opencode plugin", layout.plugin)):
        entry = next((f for f in changes.files if f["path"] == str(layout.at(path))), None)
        current = read_regular(layout.at(path))
        ok = entry is not None and current is not None and sha256(current) == entry["sha256"]
        checks.append(
            Check(name, ok, f"{path} {'matches' if ok else 'missing or changed since'} install")
        )
    errors = read_regular(layout.at(layout.plugin_errors))
    last = errors.decode(errors="replace").strip().splitlines()[-1][:200] if errors else ""
    checks.append(Check("opencode plugin errors", not errors, last or "none logged"))
    if systemctl is None:
        checks.append(Check("daemon service", None, "not checked under --root"))
        return checks
    active = systemctl(["--user", "is-active", "--quiet", render.UNIT_NAME]) == 0
    checks.append(Check("daemon service", active, render.UNIT_NAME))
    reply = ping(layout.socket)
    model = reply.get("model")
    bundles = {
        Path(f["path"]).parent.name for f in changes.files if f["path"].endswith("bundle.json")
    }
    ok = isinstance(model, str) and model[:12] in bundles
    checks.append(
        Check("daemon answers with installed model", ok, str(model or reply.get("error")))
    )
    return checks


def landlock_check() -> Check:
    from rootsh.enforcement.kernel.landlock import abi_version

    try:
        abi = abi_version()
    except OSError as exc:
        return Check("landlock ABI (P9)", False, str(exc))
    return Check("landlock ABI (P9)", abi >= 6, f"ABI {abi}")


@contextlib.contextmanager
def in_launch(layout: Layout, agent: str, run: Runner) -> Iterator[Runner]:
    """A runner whose commands run inside a registered `rootsh-agent-*.scope`,
    the only place the P9 daemon accepts hook events from (design 09 §3)."""
    launch_id = uuid.uuid4().hex
    body = {
        "op": "register",
        "launch": launch_id,
        "agent": agent,
        "project": str(layout.home),
        "home": str(layout.home),
        "layers": ["doctor"],
    }
    reply = client.request(layout.control, body, timeout_s=5.0)
    if reply.get("ok") is not True:
        raise OSError(f"daemon refused the doctor launch: {reply.get('error')}")
    scope = ["systemd-run", "--user", "--scope", "--quiet", "--collect"]
    scope.append(f"--unit=rootsh-agent-{launch_id}")
    try:
        yield lambda argv, stdin: run([*scope, "--", *argv], stdin)
    finally:
        with contextlib.suppress(OSError, ValueError):
            client.request(layout.control, {"op": "unregister", "launch": launch_id})


def k2_check() -> Check:
    loaded = apparmor.loaded()
    installed = apparmor.PROFILE_PATH.exists()
    if loaded:
        return Check("apparmor K2 (P9)", True, f"{apparmor.OUTER} loaded")
    if installed:
        return Check("apparmor K2 (P9)", False, f"{apparmor.PROFILE_PATH} installed, not loaded")
    return Check("apparmor K2 (P9)", None, "not installed (sudo rootsh install --system)")


def doctor(layout: Layout, systemctl: Systemctl | None, run: Runner = run_command) -> list[Check]:
    checks = user_checks(layout, systemctl)
    managed, command = managed_checks(layout)
    checks += managed
    live = systemctl is not None
    if live:
        reply = ping(layout.control)
        checks.append(Check("control socket (P9)", reply.get("ok") is True, str(layout.control)))
    try:
        if command is not None and live:
            with in_launch(layout, "claude_code", run) as scoped:
                checks += claude_blocking(command, layout.home, scoped)
        elif command is not None:
            checks.append(Check("claude hook blocking", None, "not run under --root"))
        if live:
            with in_launch(layout, "opencode", run) as scoped:
                checks += opencode_blocking(layout, scoped)
    except (OSError, ValueError) as exc:
        checks.append(Check("hook checks inside a launch", False, str(exc)))
    checks.append(landlock_check())
    checks.append(k2_check())
    return checks
