"""The exact bytes rootsh installs (design 08 §2-§4). Pure functions.

The P8 gate passes these same outputs to the sandboxed agents, so what is
verified there is what the installer writes.
"""

from __future__ import annotations

import json
import shlex
from pathlib import Path
from typing import Any

from rootsh.install.files import InstallError

CLAUDE_HOOK_EVENTS = ("UserPromptSubmit", "PreToolUse", "PostToolUse", "PostToolUseFailure")
HOOK_TIMEOUT_S = 30  # client gives up after 5 s; this only bounds a hung interpreter
HOOK_MODULE = "rootsh.hooks.claude_code"
PLUGIN_SOURCE = Path(__file__).resolve().parents[1] / "hooks" / "opencode_plugin.ts"
PLUGIN_PLACEHOLDER = "__ROOTSH_CONFIG__"
UNIT_NAME = "rootsh-daemon.service"


def hook_command(python: Path, socket: Path) -> str:
    # -I: the agent's environment (PYTHONPATH, user site) cannot change what runs.
    return shlex.join([str(python), "-I", "-m", HOOK_MODULE, "--socket", str(socket)])


def _is_rootsh(entry: object) -> bool:
    if not isinstance(entry, dict):
        return False
    hooks = entry.get("hooks")
    return isinstance(hooks, list) and any(
        isinstance(h, dict) and HOOK_MODULE in str(h.get("command", "")) for h in hooks
    )


def claude_hooks(python: Path, socket: Path) -> dict[str, list[dict[str, Any]]]:
    hook = {
        "type": "command",
        "command": hook_command(python, socket),
        "timeout": HOOK_TIMEOUT_S,
        "onFailure": "block",
    }
    return {event: [{"matcher": "*", "hooks": [hook]}] for event in CLAUDE_HOOK_EVENTS}


def _settings(existing: bytes | None) -> dict[str, Any]:
    if existing is None or not existing.strip():
        return {}
    try:
        settings = json.loads(existing)
    except ValueError as exc:
        raise InstallError(f"managed settings are not valid JSON ({exc}); fix them first") from exc
    if not isinstance(settings, dict):
        raise InstallError("managed settings are not a JSON object; fix them first")
    hooks = settings.get("hooks", {})
    if not isinstance(hooks, dict) or not all(isinstance(v, list) for v in hooks.values()):
        raise InstallError("managed settings have a malformed 'hooks' key; fix it first")
    return settings


def _dump(settings: dict[str, Any]) -> bytes:
    return json.dumps(settings, indent=2).encode() + b"\n"


def merge_managed(existing: bytes | None, python: Path, socket: Path) -> bytes:
    """Managed settings with rootsh's hooks, everything else kept as it was."""
    settings = _settings(existing)
    hooks = {k: [e for e in v if not _is_rootsh(e)] for k, v in settings.get("hooks", {}).items()}
    for event, entries in claude_hooks(python, socket).items():
        hooks.setdefault(event, []).extend(entries)
    settings["hooks"] = hooks
    return _dump(settings)


def strip_managed(existing: bytes) -> bytes | None:
    """Managed settings without rootsh's hooks; None if nothing else is left."""
    settings = _settings(existing)
    hooks = {
        k: kept
        for k, v in settings.get("hooks", {}).items()
        if (kept := [e for e in v if not _is_rootsh(e)])
    }
    if hooks:
        settings["hooks"] = hooks
    else:
        settings.pop("hooks", None)
    return _dump(settings) if settings else None


def rootsh_hook_entries(existing: bytes) -> dict[str, list[dict[str, Any]]]:
    """Only rootsh's entries, per event (for doctor)."""
    hooks = _settings(existing).get("hooks", {})
    return {k: [e for e in v if _is_rootsh(e)] for k, v in hooks.items()}


def plugin(python: Path, socket: Path, errors: Path) -> bytes:
    source = PLUGIN_SOURCE.read_text(encoding="utf-8")
    if source.count(PLUGIN_PLACEHOLDER) != 1:
        raise InstallError("plugin source must contain the config placeholder exactly once")
    config = json.dumps({"python": str(python), "socket": str(socket), "errors": str(errors)})
    return source.replace(PLUGIN_PLACEHOLDER, config).encode()


def systemd_quote(word: str) -> str:
    """One word for a unit file: double-quoted, with systemd's own `%`
    specifiers and `$` variables escaped so a path is never expanded."""
    if any(ord(c) < 32 for c in word):
        raise InstallError(f"control character in unit path {word!r}")
    escaped = word.replace("\\", "\\\\").replace('"', '\\"')
    return '"' + escaped.replace("%", "%%").replace("$", "$$") + '"'


def unit(
    python: Path, socket: Path, control: Path, db: Path, bundle: Path, state_dir: Path
) -> bytes:
    """User unit for the daemon. Hardening a user manager can apply without
    privileges; `rootsh doctor` checks the daemon actually runs under it."""
    argv = [
        python,
        "-I",
        "-m",
        "rootsh",
        "daemon",
        "--socket",
        socket,
        "--control",
        control,
        "--db",
        db,
        "--bundle",
        bundle,
    ]
    command = " ".join(systemd_quote(str(arg)) for arg in argv)
    text = f"""\
[Unit]
Description=rootsh decision daemon (hooks for Claude Code and OpenCode)

[Service]
Type=simple
ExecStart={command}
Restart=on-failure
RestartSec=1
UMask=0077
NoNewPrivileges=yes
LockPersonality=yes
RestrictRealtime=yes
RestrictSUIDSGID=yes
ProtectSystem=strict
ReadWritePaths={systemd_quote(str(state_dir))}
RuntimeDirectory={socket.parent.name} {control.parent.name}
RuntimeDirectoryMode=0700

[Install]
WantedBy=default.target
"""
    return text.encode()
