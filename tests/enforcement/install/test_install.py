"""Install, uninstall and doctor on a temporary root (design 08 §4-§6).

Gate criterion 5: uninstall is clean, meaning every path, mode and byte under
the root is as it was before install.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import rootsh.enforcement.install as install
from rootsh.enforcement.daemon import load_model
from rootsh.enforcement.install import Layout, render
from rootsh.enforcement.install.doctor import Check, claude_blocking, doctor
from rootsh.enforcement.install.files import InstallError
from rootsh.enforcement.kernel import apparmor

HOME = Path("/home/u")
REAL_BUNDLE = Path(__file__).resolve().parents[3] / "data" / "model"


def snapshot(root: Path) -> dict[str, tuple[str, int, bytes]]:
    found = {}
    for path in sorted(root.rglob("*")):
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode):
            body = os.readlink(path).encode()
        elif stat.S_ISREG(info.st_mode):
            body = path.read_bytes()
        else:
            body = b""
        kind = "l" if stat.S_ISLNK(info.st_mode) else "d" if stat.S_ISDIR(info.st_mode) else "f"
        found[str(path.relative_to(root))] = (kind, info.st_mode & 0o7777, body)
    return found


def _fake_load(path: Path) -> tuple[Any, str | None]:
    manifest = path / "bundle.json"
    if not manifest.is_file():
        return None, "model bundle unusable (missing)"
    return SimpleNamespace(sha256=hashlib.sha256(manifest.read_bytes()).hexdigest()), None


@pytest.fixture
def fake_models(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(install, "load_model", _fake_load)


@pytest.fixture
def bundle(tmp_path: Path) -> Path:
    src = tmp_path / "bundle"
    src.mkdir()
    (src / "bundle.json").write_text('{"model": 1}\n')
    (src / "full.model").write_text("tree\n")
    return src


@pytest.fixture
def root(tmp_path: Path) -> Path:
    base = tmp_path / "root"
    (base / "home" / "u" / ".config" / "systemd" / "user").mkdir(parents=True)
    (base / "etc").mkdir()
    (base / "var" / "lib").mkdir(parents=True)
    (base / "home" / "u" / ".bashrc").write_text("# mine\n")
    return base


def _layout(root: Path) -> Layout:
    return Layout(HOME, Path("/run/user/1000"), Path(sys.executable), root)


def _managed(root: Path) -> Path:
    return root / "etc" / "claude-code" / "managed-settings.json"


# --- user step ----------------------------------------------------------------------


@pytest.mark.usefixtures("fake_models")
def test_user_install_then_uninstall_is_byte_identical(root: Path, bundle: Path) -> None:
    before = snapshot(root)
    layout = _layout(root)
    lines = install.install_user(layout, bundle, None)
    assert lines[-1] == "systemctl skipped (--root)"
    unit = (root / "home/u/.config/systemd/user/rootsh-daemon.service").read_text()
    assert f'ExecStart={render.systemd_quote(sys.executable)} "-I" "-m" "rootsh" "daemon"' in unit
    assert "RuntimeDirectory=rootsh" in unit
    plugin = (root / "home/u/.config/opencode/plugins/rootsh/index.ts").read_text()
    assert render.PLUGIN_PLACEHOLDER not in plugin
    assert json.dumps(str(layout.socket)) in plugin
    install.uninstall_user(layout, None)
    assert snapshot(root) == before


@pytest.mark.usefixtures("fake_models")
def test_user_files_that_existed_are_restored_exactly(root: Path, bundle: Path) -> None:
    unit = root / "home/u/.config/systemd/user/rootsh-daemon.service"
    unit.write_text("[Unit]\n# the author's own\n")
    unit.chmod(0o600)
    plugin_dir = root / "home/u/.config/opencode/plugins/rootsh"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "other.ts").write_text("// not ours\n")
    before = snapshot(root)
    layout = _layout(root)
    install.install_user(layout, bundle, None)
    install.install_user(layout, bundle, None)  # re-install keeps the first prior bytes
    install.uninstall_user(layout, None)
    assert snapshot(root) == before


@pytest.mark.usefixtures("fake_models")
def test_a_file_changed_after_install_is_left_alone(root: Path, bundle: Path) -> None:
    layout = _layout(root)
    install.install_user(layout, bundle, None)
    plugin = root / "home/u/.config/opencode/plugins/rootsh/index.ts"
    plugin.write_text("// edited by the author\n")
    lines = install.uninstall_user(layout, None)
    assert any(line.startswith("NOT REMOVED") and "index.ts" in line for line in lines)
    assert plugin.read_text() == "// edited by the author\n"
    assert (root / "home/u/.local/state/rootsh/install.json").exists()  # can retry
    install.uninstall_user(layout, None, force=True)
    assert not plugin.exists()


@pytest.mark.usefixtures("fake_models")
def test_symlinked_target_is_refused_and_recorded(root: Path, bundle: Path) -> None:
    secret = root / "home/u/.bashrc"
    unit = root / "home/u/.config/systemd/user/rootsh-daemon.service"
    unit.symlink_to(secret)
    layout = _layout(root)
    with pytest.raises(InstallError, match="not a plain file"):
        install.install_user(layout, bundle, None)
    assert secret.read_text() == "# mine\n"
    # what was written before the refusal is recorded, so uninstall can undo it
    install.uninstall_user(layout, None)
    assert not (root / "home/u/.local/share/rootsh").exists()


def test_a_bundle_that_does_not_load_is_refused(root: Path, tmp_path: Path) -> None:
    before = snapshot(root)
    with pytest.raises(InstallError, match="refusing to install"):
        install.install_user(_layout(root), tmp_path / "missing", None)
    assert snapshot(root) == before


def test_too_long_socket_path_is_refused(root: Path, bundle: Path) -> None:
    layout = Layout(HOME, Path("/run/" + "x" * 120), Path(sys.executable), root)
    with pytest.raises(InstallError, match="too long"):
        install.install_user(layout, bundle, None)


@pytest.mark.usefixtures("fake_models")
def test_install_starts_and_uninstall_stops_the_unit(
    root: Path, bundle: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []
    sha = hashlib.sha256((bundle / "bundle.json").read_bytes()).hexdigest()
    monkeypatch.setattr(install, "wait_for_daemon", lambda _p: {"ok": True, "model": sha})
    layout = _layout(root)
    install.install_user(layout, bundle, _recorder(calls))
    assert calls == [
        ["--user", "daemon-reload"],
        ["--user", "enable", "rootsh-daemon.service"],
        ["--user", "restart", "rootsh-daemon.service"],
    ]
    install.uninstall_user(layout, _recorder(calls))
    assert calls[3] == ["--user", "disable", "--now", "rootsh-daemon.service"]


@pytest.mark.usefixtures("fake_models")
def test_a_daemon_with_the_wrong_model_fails_install(
    root: Path, bundle: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(install, "wait_for_daemon", lambda _p: {"ok": True, "model": "0" * 64})
    with pytest.raises(InstallError, match="not answering with the installed model"):
        install.install_user(_layout(root), bundle, lambda _a: 0)


@pytest.mark.skipif(not REAL_BUNDLE.is_dir(), reason="P6 bundle not present")
def test_the_real_bundle_copy_verifies(root: Path) -> None:
    before = snapshot(root)
    layout = _layout(root)
    model, _ = load_model(REAL_BUNDLE)
    assert model is not None
    install.install_user(layout, REAL_BUNDLE, None)
    copy = root / "home/u/.local/share/rootsh/models" / model.sha256[:12]
    assert load_model(copy)[0] is not None  # hash-checked load of the copy
    install.uninstall_user(layout, None)
    assert snapshot(root) == before


def _recorder(calls: list[list[str]]) -> install.Systemctl:
    def run(args: Sequence[str]) -> int:
        calls.append(list(args))
        return 0

    return run


# --- system step --------------------------------------------------------------------


def test_system_install_then_uninstall_with_no_prior_settings(root: Path) -> None:
    before = snapshot(root)
    layout = _layout(root)
    install.install_system(layout)
    settings = json.loads(_managed(root).read_bytes())
    assert set(settings["hooks"]) == set(render.CLAUDE_HOOK_EVENTS)
    for entries in settings["hooks"].values():
        (hook,) = entries[0]["hooks"]
        assert hook["onFailure"] == "block"
        assert hook["command"] == render.hook_command(layout.python, layout.socket)
        assert (
            " -I -m rootsh.enforcement.hooks.claude_code --socket /run/user/1000/rootsh/rootsh.sock"
            in hook["command"]
        )
    assert stat.S_IMODE(_managed(root).stat().st_mode) == 0o644
    install.uninstall_system(layout)
    assert snapshot(root) == before


def test_system_step_writes_loads_and_removes_the_k2_profile(root: Path) -> None:
    before = snapshot(root)
    layout = _layout(root)
    calls: list[list[str]] = []

    def loader(argv: Sequence[str]) -> int:
        calls.append(list(argv))
        return 0

    code = (Path("/opt/x/.venv"),)
    lines = install.install_system(layout, code, loader)
    profile = (root / "etc" / "apparmor.d" / "rootsh").read_text()
    assert 'audit deny "/opt/x/.venv/**" wl,' in profile
    assert f'audit deny "{layout.control.parent}/**" rwlk,' in profile
    assert f'audit deny "{layout.state_dir}/**" rwlk,' in profile
    assert calls == [[apparmor.PARSER, "-r", str(root / "etc" / "apparmor.d" / "rootsh")]]
    assert lines[-1].startswith("K2 loaded")
    install.uninstall_system(layout, loader)
    assert calls[-1][:2] == [apparmor.PARSER, "-R"]
    assert snapshot(root) == before


def test_system_step_fails_when_the_profile_does_not_load(root: Path) -> None:
    with pytest.raises(InstallError, match="K2 is not loaded"):
        install.install_system(_layout(root), (), lambda _argv: 1)


PRIOR: dict[str, Any] = {
    "permissions": {"deny": ["Bash(curl:*)"]},
    "hooks": {
        "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "audit"}]}]
    },
}


def test_system_install_keeps_other_settings_and_reverts_exactly(root: Path) -> None:
    _managed(root).parent.mkdir()
    _managed(root).write_text(json.dumps(PRIOR) + "\n")
    before = snapshot(root)
    layout = _layout(root)
    install.install_system(layout)
    install.install_system(layout)  # idempotent
    merged = json.loads(_managed(root).read_bytes())
    assert merged["permissions"] == PRIOR["permissions"]
    assert merged["hooks"]["PreToolUse"][0] == PRIOR["hooks"]["PreToolUse"][0]
    assert len(merged["hooks"]["PreToolUse"]) == 2
    install.uninstall_system(layout)
    assert snapshot(root) == before


def test_system_uninstall_after_the_author_edited_keeps_their_edit(root: Path) -> None:
    layout = _layout(root)
    install.install_system(layout)
    edited = json.loads(_managed(root).read_bytes())
    edited["env"] = {"X": "1"}
    _managed(root).write_text(json.dumps(edited))
    lines = install.uninstall_system(layout)
    assert "removed rootsh's hooks only" in lines[0]
    assert json.loads(_managed(root).read_bytes()) == {"env": {"X": "1"}}


@pytest.mark.parametrize("content", ["{not json", "[]", '{"hooks": []}'])
def test_system_install_refuses_unreadable_settings(root: Path, content: str) -> None:
    _managed(root).parent.mkdir()
    _managed(root).write_text(content)
    before = snapshot(root)
    with pytest.raises(InstallError, match="fix"):
        install.install_system(_layout(root))
    assert {k: v for k, v in snapshot(root).items() if "var" not in k} == {
        k: v for k, v in before.items() if "var" not in k
    }


def test_system_install_refuses_a_symlinked_settings_file(root: Path) -> None:
    _managed(root).parent.mkdir()
    _managed(root).symlink_to(root / "home/u/.bashrc")
    with pytest.raises(InstallError, match="not a plain file"):
        install.install_system(_layout(root))
    assert (root / "home/u/.bashrc").read_text() == "# mine\n"


def test_system_step_needs_root_on_the_real_root() -> None:
    if os.geteuid() == 0:
        pytest.skip("running as root")
    layout = Layout(HOME, Path("/run/user/1000"), Path(sys.executable))
    with pytest.raises(InstallError, match="sudo"):
        install.install_system(layout)


def test_strip_is_the_inverse_of_merge() -> None:
    python, sock = Path("/v/bin/python"), Path("/run/user/1/rootsh/rootsh.sock")
    prior = (json.dumps(PRIOR, indent=2) + "\n").encode()
    assert render.strip_managed(render.merge_managed(prior, python, sock)) == prior
    assert render.strip_managed(render.merge_managed(None, python, sock)) is None


# --- doctor -------------------------------------------------------------------------


@pytest.mark.usefixtures("fake_models")
def test_doctor_on_a_complete_root_install(root: Path, bundle: Path) -> None:
    layout = _layout(root)
    install.install_user(layout, bundle, None)
    install.install_system(layout)
    checks = doctor(layout, None)
    failed = [c.line() for c in checks if c.ok is False and not c.name.startswith("landlock")]
    assert failed == []
    (root / "home/u/.config/systemd/user/rootsh-daemon.service").write_text("tampered")
    assert any(c.name == "daemon unit" and c.ok is False for c in doctor(layout, None))


def test_doctor_without_install_fails(root: Path) -> None:
    checks = doctor(_layout(root), None)
    names = {c.name for c in checks if c.ok is False}
    assert {"user install", "managed settings"} <= names


def _reply(stdout: str, code: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], code, stdout, "")


DENY_DEFENSE = json.dumps(
    {
        "hookSpecificOutput": {
            "permissionDecision": "deny",
            "permissionDecisionReason": "rootsh blocked this (defense): x",
        }
    }
)
DENY_FAILURE = DENY_DEFENSE.replace("(defense)", "(failure)")


@pytest.mark.parametrize(
    ("answers", "ok"),
    [
        ([_reply(""), _reply(DENY_DEFENSE), _reply("")], [True, True, True]),
        # daemon down: everything denied for the wrong reason -> not "actually blocking"
        ([_reply("", 2), _reply(DENY_FAILURE), _reply(DENY_FAILURE)], [False, False, False]),
        ([_reply(""), _reply(""), _reply("")], [True, False, True]),  # never blocks
    ],
)
def test_doctor_blocking_check_needs_the_defense_layer(
    answers: list[subprocess.CompletedProcess[str]], ok: list[bool]
) -> None:
    replies = iter(answers)
    checks: list[Check] = claude_blocking(
        "/v/bin/python -I -m rootsh.enforcement.hooks.claude_code",
        HOME,
        lambda _a, _s: next(replies),
    )
    assert [c.ok for c in checks] == ok


@pytest.mark.skipif(shutil.which("systemd-analyze") is None, reason="no systemd-analyze")
def test_unit_passes_systemd_analyze(tmp_path: Path) -> None:
    """Quoting survives systemd's parser: the interpreter path has a space
    and a %, and verify fails if ExecStart's binary is not found."""
    odd = tmp_path / "a b%n"
    odd.mkdir()
    python = odd / "python"
    python.symlink_to(sys.executable)
    unit = tmp_path / render.UNIT_NAME
    sock = Path("/run/user/1000/rootsh/rootsh.sock")
    unit.write_bytes(render.unit(python, sock, odd / "c.sock", odd / "r.db", odd, odd))
    done = subprocess.run(  # noqa: S603
        ["systemd-analyze", "--user", "verify", str(unit)],  # noqa: S607
        capture_output=True, text=True, check=False,
    )  # fmt: skip
    assert done.returncode == 0, done.stderr
    assert "%%n" in unit.read_text()


def test_systemd_quote_escapes_expansions() -> None:
    assert render.systemd_quote('/a b/$X/%h/"q"') == '"/a b/$$X/%%h/\\"q\\""'
    with pytest.raises(InstallError):
        render.systemd_quote("/a\nb")
