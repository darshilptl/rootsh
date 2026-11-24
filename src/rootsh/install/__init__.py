"""`rootsh install` / `uninstall` (docs/design/08-hooks-install.md §4, ADR-0017).

Two steps, each recorded in its own manifest:

- user step (the author): model bundle copy, daemon user unit, OpenCode plugin;
- system step (root): rootsh's hooks merged into Claude Code's managed settings.

`Layout.root` re-roots every path for tests and dry runs; the bytes written
always name the real paths. `systemctl` is injected; None skips it.
"""

from __future__ import annotations

import contextlib
import os
import pwd
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from rootsh.daemon import client, load_model
from rootsh.install import render
from rootsh.install.files import Changes, InstallError, atomic_write, read_regular, revert, sha256
from rootsh.kernel import apparmor

Systemctl = Callable[[Sequence[str]], int]
Loader = Callable[[Sequence[str]], int]  # runs apparmor_parser; None skips it
MANAGED_SETTINGS = Path("/etc/claude-code/managed-settings.json")
SYSTEM_STATE = Path("/var/lib/rootsh")
DB_FILES = ("runtime.db", "runtime.db-wal", "runtime.db-shm", "opencode-plugin-errors.log")
START_WAIT_S = 20.0
SOCKET_PATH_MAX = 108  # sun_path, including the terminating NUL

__all__ = [
    "InstallError",
    "Layout",
    "install_system",
    "install_user",
    "uninstall_system",
    "uninstall_user",
]


@dataclass(frozen=True, slots=True)
class Layout:
    home: Path
    runtime_dir: Path
    python: Path
    root: Path = Path("/")

    @classmethod
    def for_user(cls, name: str, python: Path, root: Path = Path("/")) -> Layout:
        entry = pwd.getpwnam(name)
        return cls(Path(entry.pw_dir), Path(f"/run/user/{entry.pw_uid}"), python, root)

    def at(self, path: Path) -> Path:
        """Where `path` lives under this layout's root."""
        return self.root / path.relative_to("/")

    @property
    def socket(self) -> Path:
        return self.runtime_dir / "rootsh" / "rootsh.sock"

    @property
    def control(self) -> Path:
        """Approvals and launch registration; never bound into a sandbox."""
        return self.runtime_dir / "rootsh-control" / "control.sock"

    @property
    def state_dir(self) -> Path:
        return self.home / ".local" / "state" / "rootsh"

    @property
    def db(self) -> Path:
        return self.state_dir / "runtime.db"

    @property
    def models(self) -> Path:
        return self.home / ".local" / "share" / "rootsh" / "models"

    @property
    def unit(self) -> Path:
        return self.home / ".config" / "systemd" / "user" / render.UNIT_NAME

    @property
    def plugin(self) -> Path:
        return self.home / ".config" / "opencode" / "plugins" / "rootsh" / "index.ts"

    @property
    def plugin_errors(self) -> Path:
        return self.state_dir / "opencode-plugin-errors.log"

    @property
    def user_manifest(self) -> Path:
        return self.state_dir / "install.json"

    @property
    def system_manifest(self) -> Path:
        return SYSTEM_STATE / "install-system.json"

    @property
    def managed(self) -> Path:
        return MANAGED_SETTINGS


def _changes(manifest: Path) -> Changes:
    data = read_regular(manifest)
    return Changes() if data is None else Changes.load(data)


def _save(changes: Changes, manifest: Path, mode: int) -> None:
    changes.mkdir(manifest.parent, 0o700)
    atomic_write(manifest, changes.dump(), mode)


# --- user step ------------------------------------------------------------------


def install_user(layout: Layout, bundle: Path, systemctl: Systemctl | None) -> list[str]:
    """Install the daemon and plugin for the author. Returns progress lines."""
    if os.geteuid() == 0 and layout.root == Path("/"):
        raise InstallError(
            "run `rootsh install` as yourself, not root (the system step is --system)"
        )
    for path in (layout.socket, layout.control):
        if len(os.fsencode(path)) >= SOCKET_PATH_MAX:
            raise InstallError(f"socket path {path} is too long for AF_UNIX")
    model, error = load_model(bundle)
    if model is None:
        raise InstallError(f"refusing to install: {error}")
    changes = _changes(layout.at(layout.user_manifest))
    dest = layout.models / model.sha256[:12]
    try:  # whatever was written is recorded, even if a later step fails
        _write_user_files(layout, changes, bundle, dest, model.sha256)
    finally:
        _save(changes, layout.at(layout.user_manifest), 0o600)
    lines = [
        f"bundle {model.sha256[:12]} -> {dest}",
        f"unit {layout.unit}",
        f"plugin {layout.plugin}",
    ]
    if systemctl is None:
        return [*lines, "systemctl skipped (--root)"]
    for args in (["daemon-reload"], ["enable", render.UNIT_NAME], ["restart", render.UNIT_NAME]):
        if systemctl(["--user", *args]) != 0:
            raise InstallError(f"systemctl --user {' '.join(args)} failed; see `rootsh doctor`")
    reply = wait_for_daemon(layout.socket)
    if reply.get("model") != model.sha256:
        raise InstallError(f"daemon not answering with the installed model: {reply}")
    return [*lines, f"daemon up on {layout.socket}"]


def _write_user_files(layout: Layout, changes: Changes, bundle: Path, dest: Path, sha: str) -> None:
    for source in sorted(bundle.iterdir()):
        data = read_regular(source)  # refuses symlinks and special files
        if data is None:
            raise InstallError(f"bundle entry {source} vanished")
        changes.write(layout.at(dest / source.name), data, 0o644)
    copied, copy_error = load_model(layout.at(dest))
    if copied is None or copied.sha256 != sha:
        raise InstallError(f"bundle copy does not verify: {copy_error}")
    changes.mkdir(layout.at(layout.state_dir), 0o700)
    unit = render.unit(
        layout.python, layout.socket, layout.control, layout.db, dest, layout.state_dir
    )
    changes.write(layout.at(layout.unit), unit, 0o644)
    plugin = render.plugin(layout.python, layout.socket, layout.plugin_errors)
    changes.write(layout.at(layout.plugin), plugin, 0o644)


def wait_for_daemon(path: Path, wait_s: float = START_WAIT_S) -> dict[str, object]:
    deadline = time.monotonic() + wait_s
    last: dict[str, object] = {"error": "not started"}
    while time.monotonic() < deadline:
        try:
            return client.request(path, {"op": "ping"}, timeout_s=2.0)
        except (OSError, ValueError) as exc:
            last = {"error": f"{type(exc).__name__}: {exc}"}
            time.sleep(0.2)
    return last


def uninstall_user(
    layout: Layout, systemctl: Systemctl | None, force: bool = False, keep_db: bool = False
) -> list[str]:
    if os.geteuid() == 0 and layout.root == Path("/"):
        raise InstallError("run `rootsh uninstall` as yourself; the system step is --system")
    manifest = layout.at(layout.user_manifest)
    if read_regular(manifest) is None:
        raise InstallError(f"no install manifest at {layout.user_manifest}; nothing to uninstall")
    changes = _changes(manifest)
    lines = []
    if systemctl is not None:
        # Stop before removing files; a unit that is not loaded is fine.
        systemctl(["--user", "disable", "--now", render.UNIT_NAME])
        lines.append("daemon stopped and disabled")
    problems = revert(changes, force)
    if not keep_db:
        for name in DB_FILES:
            path = layout.at(layout.state_dir / name)
            with contextlib.suppress(FileNotFoundError):
                if read_regular(path) is not None:
                    path.unlink()
    if problems:
        return [*lines, *(f"NOT REMOVED: {p}" for p in problems)]
    manifest.unlink()
    for created in reversed(changes.dirs):  # now that the manifest is gone
        with contextlib.suppress(OSError):
            Path(created).rmdir()
    if systemctl is not None:
        systemctl(["--user", "daemon-reload"])
    kept = f"; runtime DB kept in {layout.state_dir}" if keep_db else ""
    return [*lines, f"removed {len(changes.files)} files{kept}"]


# --- system step ----------------------------------------------------------------


def _need_root(layout: Layout) -> None:
    if layout.root == Path("/") and os.geteuid() != 0:
        raise InstallError("the system step writes /etc/claude-code: run it with sudo")


def k2_profile(layout: Layout, code: Sequence[Path]) -> str:
    """The K2 profile for this install (design 09 §5-§6): rootsh's code,
    plugin, unit and Claude Code's managed settings read-only; its state,
    models and control socket hidden."""
    read_only = [*code, layout.plugin.parent.parent, layout.unit, layout.managed.parent]
    hidden = [
        layout.state_dir,
        layout.models.parent,
        layout.control.parent,
        SYSTEM_STATE,
    ]
    return apparmor.render(read_only, hidden, layout.home)


def install_system(
    layout: Layout, code: Sequence[Path] = (), loader: Loader | None = None
) -> list[str]:
    """Managed settings and, from P9, the K2 AppArmor profile (loaded with
    `loader`, i.e. apparmor_parser -r; None under --root)."""
    _need_root(layout)
    changes = _changes(layout.at(layout.system_manifest))
    path = layout.at(layout.managed)
    merged = render.merge_managed(read_regular(path), layout.python, layout.socket)
    profile = k2_profile(layout, code).encode()
    try:
        apparmor.check(profile.decode())
    except (apparmor.AppArmorError, OSError) as exc:
        raise InstallError(str(exc)) from exc
    profile_path = layout.at(apparmor.PROFILE_PATH)
    try:
        changes.mkdir(path.parent, 0o755)
        changed = changes.write(path, merged, 0o644)
        changes.mkdir(profile_path.parent, 0o755)
        changes.write(profile_path, profile, 0o644)
    finally:
        _save(changes, layout.at(layout.system_manifest), 0o600)
    state = "written" if changed else "already current"
    lines = [
        f"managed settings {layout.managed}: {state}",
        f"hook: {render.hook_command(layout.python, layout.socket)}",
        f"apparmor profile {apparmor.PROFILE_PATH}: written",
    ]
    if loader is None:
        return [*lines, "apparmor_parser skipped (--root)"]
    if loader([apparmor.PARSER, "-r", str(profile_path)]) != 0:
        raise InstallError(f"apparmor_parser -r {profile_path} failed; K2 is not loaded")
    return [*lines, f"K2 loaded: {apparmor.OUTER}, {apparmor.AGENT}"]


def uninstall_system(layout: Layout, loader: Loader | None = None) -> list[str]:
    """Remove rootsh's hooks and K2 profile. The managed settings file is
    shared with other tools, so a file changed since install is edited
    (rootsh's entries only), never restored over the author's changes."""
    _need_root(layout)
    manifest = layout.at(layout.system_manifest)
    changes = _changes(manifest)
    profile_path = layout.at(apparmor.PROFILE_PATH)
    if loader is not None and profile_path.exists():  # unload before the file goes
        loader([apparmor.PARSER, "-R", str(profile_path)])
    path = layout.at(layout.managed)
    entry = next((f for f in changes.files if f["path"] == str(path)), None)
    current = read_regular(path)
    lines = []
    ours = current is not None and any(render.rootsh_hook_entries(current).values())
    if current is not None and ours and (entry is None or sha256(current) != entry["sha256"]):
        stripped = render.strip_managed(current)
        if stripped is None:
            path.unlink()
        else:
            atomic_write(path, stripped, path.stat().st_mode & 0o7777)
        lines.append(f"{layout.managed} changed since install: removed rootsh's hooks only")
        changes.files = [f for f in changes.files if f["path"] != str(path)]
    problems = revert(changes)
    if problems:
        return [*lines, *(f"NOT REMOVED: {p}" for p in problems)]
    with contextlib.suppress(FileNotFoundError):
        manifest.unlink()
    for created in reversed(changes.dirs):
        with contextlib.suppress(OSError):
            Path(created).rmdir()
    return [*lines, f"rootsh hooks removed from {layout.managed}"]


def ping(path: Path) -> dict[str, object]:
    """One ping, never raises (for doctor)."""
    try:
        return client.request(path, {"op": "ping"}, timeout_s=2.0)
    except (OSError, ValueError) as exc:  # TimeoutError is an OSError
        return {"error": f"{type(exc).__name__}: {exc}"}
