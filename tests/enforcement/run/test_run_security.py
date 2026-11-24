"""Adversarial tests of `rootsh run`'s sandbox and kernel layers (design 09 §7).

Each test runs the exact argv `rootsh.enforcement.run.sandbox.plan()` builds (bwrap ->
rootsh.enforcement.kernel.confine -> probe) against a fake home, project, code dir and
sockets under tmp_path. Nothing outside tmp_path is written; the host's real
home is never bound. Containment (fake home only) is asserted first.
"""

from __future__ import annotations

import ctypes
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

from rootsh.analysis.events import Agent
from rootsh.enforcement.kernel import apparmor, landlock, seccomp
from rootsh.enforcement.kernel.confine import Spec
from rootsh.enforcement.run.sandbox import Host, plan

pytestmark = pytest.mark.skipif(shutil.which("bwrap") is None, reason="needs bwrap")

PROBE = r"""
import ctypes, errno, json, os, socket, sys
libc = ctypes.CDLL(None, use_errno=True)
libc.syscall.restype = ctypes.c_long
out = {}
def attempt(name, fn):
    try:
        fn(); out[name] = "ok"
    except OSError as exc:
        out[name] = errno.errorcode.get(exc.errno, str(exc.errno))
def sysc(name, nr, *args):
    r = libc.syscall(nr, *args)
    out[name] = "ok" if r >= 0 else errno.errorcode[ctypes.get_errno()]
cfg = json.loads(sys.argv[1])
attempt("read_secret", lambda: open(cfg["secret"]).read())
attempt("write_home", lambda: open(cfg["home"] + "/planted", "w").write("x"))
attempt("write_code", lambda: open(cfg["code"] + "/planted.py", "w").write("x"))
attempt("write_project", lambda: open(cfg["project"] + "/ok.txt", "w").write("x"))
attempt("write_agent_state", lambda: open(cfg["state"] + "/ok", "w").write("x"))
attempt("read_code", lambda: open(cfg["code"] + "/hook.py").read())
def unix(path):
    s = socket.socket(socket.AF_UNIX); s.settimeout(2); s.connect(path); s.close()
attempt("connect_control", lambda: unix(cfg["control"]))
attempt("connect_event", lambda: unix(cfg["event"]))
def tcp(port):
    s = socket.socket(); s.settimeout(2); s.connect(("127.0.0.1", port)); s.close()
attempt("tcp_local", lambda: tcp(cfg["tcp"]))
def bind():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); s.close()
attempt("tcp_bind", bind)
out["sys_visible"] = os.path.exists("/sys/fs/cgroup")
out["bus_visible"] = os.path.exists(cfg["bus"])
out["outside_pids"] = sorted(int(p) for p in os.listdir("/proc") if p.isdigit())[:3]
sysc("ptrace", 101, 16, 1, 0, 0)
sysc("mount", 165, b"none", b"/tmp", b"tmpfs", 0, None)
sysc("bpf", 321, 0, None, 0)
sysc("keyctl", 250, 0, 0, 0, 0, 0)
sysc("init_module", 175, None, 0, b"")
sysc("perf_event_open", 298, None, 0, -1, -1, 0)
print(json.dumps(out))
"""


@pytest.fixture
def base(tmp_path: Path) -> Iterator[Path]:
    """A scratch root outside /tmp (K1 allows /tmp, a real home is not under
    it): a fresh directory in the user's runtime tmpfs, removed afterwards."""
    runtime = Path(os.environ.get("XDG_RUNTIME_DIR", ""))
    if not runtime.is_absolute() or not runtime.is_dir():
        yield tmp_path
        return
    root = runtime / f"rootsh-test-{uuid.uuid4().hex[:12]}"
    root.mkdir(mode=0o700)
    try:
        yield root
    finally:
        shutil.rmtree(root)


@pytest.fixture
def world(base: Path) -> Iterator[dict[str, object]]:
    tmp_path = base
    home = tmp_path / "home"
    (home / ".ssh").mkdir(parents=True)
    (home / ".ssh" / "id_ed25519").write_text("CANARY-SECRET")
    (home / ".claude").mkdir()
    (home / ".claude.json").write_text("{}")
    project = tmp_path / "project"
    project.mkdir()
    code = tmp_path / "code"
    code.mkdir()
    (code / "hook.py").write_text("# rootsh hook\n")
    runtime = tmp_path / "run"
    (runtime / "rootsh").mkdir(parents=True)
    (runtime / "rootsh-control").mkdir()
    (runtime / "bus").write_text("")
    event, control = runtime / "rootsh" / "e.sock", runtime / "rootsh-control" / "c.sock"
    listeners = []
    for path in (event, control):
        server = socket.socket(socket.AF_UNIX)
        server.bind(str(path))
        server.listen(4)
        listeners.append(server)
    tcp = socket.socket()
    tcp.bind(("127.0.0.1", 0))
    tcp.listen(4)
    listeners.append(tcp)
    stop = threading.Event()

    def accept_all(server: socket.socket) -> None:
        server.settimeout(0.2)
        while not stop.is_set():
            try:
                server.accept()[0].close()
            except OSError:
                continue

    threads = [threading.Thread(target=accept_all, args=(s,), daemon=True) for s in listeners]
    for t in threads:
        t.start()
    venv_code = (Path(sys.prefix), Path(sys.base_prefix), _package_root())
    host = Host(
        home, os.getuid(), runtime, event, control, Path(sys.executable), (code, *venv_code)
    )
    yield {
        "host": host,
        "project": project,
        "cfg": {
            "secret": str(home / ".ssh" / "id_ed25519"),
            "home": str(home),
            "code": str(code),
            "project": str(project),
            "state": str(home / ".claude"),
            "control": str(control),
            "event": str(event),
            "tcp": tcp.getsockname()[1],
            "bus": str(runtime / "bus"),
        },
    }
    stop.set()
    for s in listeners:
        s.close()


def _package_root() -> Path:
    import rootsh

    return Path(rootsh.__file__).resolve().parent.parent


def _run(world: dict[str, object], k2: bool = False) -> dict[str, object]:
    host = world["host"]
    assert isinstance(host, Host)
    project = world["project"]
    assert isinstance(project, Path)
    probe = [str(host.python), "-I", "-c", PROBE, json.dumps(world["cfg"])]
    argv = plan(Agent.CLAUDE_CODE, host, project, [], {"PATH": "/usr/bin"}, probe=probe, k2=k2).argv
    done = subprocess.run(argv, capture_output=True, text=True, timeout=60, check=False)  # noqa: S603
    assert done.returncode == 0, f"exit {done.returncode}\n{done.stdout}\n{done.stderr}"
    result = json.loads(done.stdout.strip().splitlines()[-1])
    assert isinstance(result, dict)
    return result


@pytest.mark.parametrize(
    "k2",
    [
        False,
        pytest.param(True, marks=pytest.mark.skipif(not apparmor.loaded(), reason="K2 not loaded")),
    ],
)
def test_sandbox_and_layers_deny_every_escape(
    world: dict[str, object], base: Path, k2: bool
) -> None:
    tmp_path = base
    out = _run(world, k2)
    # Containment first: the fake home is a tmpfs, so nothing landed on disk.
    assert not (tmp_path / "home" / "planted").exists()
    assert out["read_secret"] == "ENOENT"  # ~/.ssh is not in the sandbox at all
    if not (tmp_path / "home").is_relative_to("/tmp"):  # noqa: S108 - see `base`
        assert out["write_home"] in ("EACCES", "EROFS")  # home tmpfs is read-only to K1
    assert out["write_code"] in ("EACCES", "EROFS")
    assert not (tmp_path / "code" / "planted.py").exists()
    assert out["read_code"] == "ok"  # the hook can still run
    assert out["write_project"] == "ok"
    assert (tmp_path / "project" / "ok.txt").exists()
    assert out["write_agent_state"] == "ok"
    assert out["connect_control"] == "ENOENT"  # never visible (design 09 §2)
    assert out["connect_event"] == "ok"
    assert out["tcp_local"] == "EACCES"  # K1: connect to 443 only
    assert out["tcp_bind"] == "EACCES"
    assert out["sys_visible"] is False
    assert out["bus_visible"] is False
    pids = out["outside_pids"]
    assert isinstance(pids, list)
    assert pids[:1] == [1]  # own pid namespace
    for name in ("ptrace", "mount", "bpf", "keyctl", "init_module", "perf_event_open"):
        assert out[name] == "EPERM", name


def test_project_may_not_be_home_or_overlap_rootsh_code(world: dict[str, object]) -> None:
    host = world["host"]
    assert isinstance(host, Host)
    for bad in (host.home, host.code[0], host.code[0] / ".."):
        with pytest.raises(ValueError, match=r"home|overlaps"):
            plan(Agent.CLAUDE_CODE, host, bad, [], {})


def test_sockets_in_one_directory_are_refused(world: dict[str, object]) -> None:
    host = world["host"]
    assert isinstance(host, Host)
    same = Host(
        host.home,
        host.uid,
        host.runtime_dir,
        host.event_socket,
        host.event_socket.with_name("c.sock"),
        host.python,
        host.code,
    )
    project = world["project"]
    assert isinstance(project, Path)
    with pytest.raises(ValueError, match="share a directory"):
        plan(Agent.CLAUDE_CODE, same, project, [], {})


def test_environment_is_an_allowlist(world: dict[str, object]) -> None:
    host = world["host"]
    project = world["project"]
    assert isinstance(host, Host)
    assert isinstance(project, Path)
    env = {"PATH": "/usr/bin", "SSH_AUTH_SOCK": "/x", "DBUS_SESSION_BUS_ADDRESS": "y", "K": "v"}
    argv = plan(Agent.CLAUDE_CODE, host, project, [], env, extra_env=["K"]).argv
    names = {argv[i + 1] for i, a in enumerate(argv) if a == "--setenv"}
    assert "SSH_AUTH_SOCK" not in names
    assert "DBUS_SESSION_BUS_ADDRESS" not in names
    assert {"PATH", "K", "HOME", "XDG_RUNTIME_DIR"} <= names


def test_opencode_gets_a_private_server_and_only_its_port(world: dict[str, object]) -> None:
    host = world["host"]
    project = world["project"]
    assert isinstance(host, Host)
    assert isinstance(project, Path)
    planned = plan(Agent.OPENCODE, host, project, [], {}, port=45678, password="pw")
    assert planned.spec.bind_ports == (45678,)
    assert planned.spec.connect_ports == (443,)
    script = planned.argv[planned.argv.index("rootsh-opencode") - 1]
    assert "opencode serve --hostname 127.0.0.1" in script
    assert '--server "http' in script
    with pytest.raises(ValueError, match="local port"):
        plan(Agent.OPENCODE, host, project, [], {})


# --- each layer on its own (no bwrap) ----------------------------------------


def _confined(spec: Spec, code: str) -> subprocess.CompletedProcess[str]:
    argv = [sys.executable, "-I", "-m", "rootsh.enforcement.kernel.confine", spec.dump(), "--"]
    argv += [sys.executable, "-I", "-c", code]
    return subprocess.run(argv, capture_output=True, text=True, timeout=30, check=False)  # noqa: S603


def _base_ro() -> tuple[str, ...]:
    return ("/usr", "/etc", "/proc", sys.prefix, sys.base_prefix, str(_package_root()))


def test_k1_alone_denies_reads_and_writes_outside_its_rules(tmp_path: Path) -> None:
    allowed, other = tmp_path / "allowed", tmp_path / "other"
    allowed.mkdir()
    other.mkdir()
    (other / "secret").write_text("CANARY")
    spec = Spec((str(allowed),), _base_ro())
    code = (
        "import sys\n"
        f"open({str(allowed / 'w')!r}, 'w').write('x')\n"
        f"try: open({str(other / 'secret')!r}).read(); print('READ')\n"
        "except PermissionError: print('denied-read')\n"
        f"try: open({str(other / 'new')!r}, 'w'); print('WROTE')\n"
        "except PermissionError: print('denied-write')\n"
    )
    done = _confined(spec, code)
    assert done.returncode == 0, done.stderr
    assert done.stdout.split() == ["denied-read", "denied-write"]
    assert not (other / "new").exists()
    assert (allowed / "w").exists()


def test_k3_alone_denies_escape_syscalls_and_kills_x32() -> None:
    spec = Spec(("/tmp",), _base_ro())  # noqa: S108
    code = (
        "import ctypes\nl = ctypes.CDLL(None, use_errno=True)\n"
        "print(l.syscall(101, 16, 1, 0, 0), ctypes.get_errno())\n"
        "print(l.ioctl(0, 0x5412, ctypes.c_char_p(b'x')), ctypes.get_errno())\n"
        "print(l.ioctl(0, 0x1_0000_5412, ctypes.c_char_p(b'x')), ctypes.get_errno())\n"
    )
    done = _confined(spec, code)
    assert done.returncode == 0, done.stderr
    eperm = f"-1 {os.strerror(1) and 1}"
    assert done.stdout.splitlines() == [eperm, eperm, eperm]  # high bits do not evade
    x32 = "import ctypes; ctypes.CDLL(None).syscall(0x40000000 | 39)"  # x32 getpid
    killed = _confined(spec, x32)
    assert killed.returncode == -31  # SIGSYS: killed, not just refused


def test_confine_refuses_to_start_on_a_bad_rule(tmp_path: Path) -> None:
    spec = Spec((str(tmp_path / "missing"),), _base_ro())
    done = _confined(spec, "print('started')")
    assert done.returncode == 126
    assert "started" not in done.stdout
    assert "rule path" in done.stderr


def test_confine_refuses_when_k2_is_required_but_absent() -> None:
    spec = Spec(("/tmp",), _base_ro(), apparmor_label=apparmor.AGENT)  # noqa: S108
    done = _confined(spec, "print('started')")
    assert done.returncode == 126
    assert "K2 expected" in done.stderr


def test_landlock_masks_follow_the_abi() -> None:
    assert landlock.fs_mask(1) == (1 << 13) - 1
    assert landlock.fs_mask(4) == (1 << 15) - 1
    assert landlock.fs_mask(8) == (1 << 16) - 1


def test_seccomp_program_is_well_formed() -> None:
    raw = seccomp.program()
    assert len(raw) % 8 == 0
    assert len(raw) // 8 < 4096
    assert ctypes.sizeof(ctypes.c_uint64) == 8


@pytest.mark.skipif(shutil.which("apparmor_parser") is None, reason="needs apparmor_parser")
def test_k2_profile_parses(tmp_path: Path) -> None:
    text = apparmor.render(
        [tmp_path / "code dir", Path("/etc/claude-code")],
        [tmp_path / "state", tmp_path / "run" / "rootsh-control"],
        tmp_path,
    )
    apparmor.check(text)
    assert "audit deny ptrace," in text
    assert "allow pix /** -> &rootsh-agent//&unpriv_bwrap" in text
    with pytest.raises(apparmor.AppArmorError):
        apparmor.check(text.replace("audit deny ptrace,", "audit deny ptrace"))


K2_PROBE = r"""
import json, os, sys
cfg = json.loads(sys.argv[1])
out = {"label": open("/proc/self/attr/apparmor/current").read().strip()}
try:
    os.mkdir(cfg["project"] + "/.git/hooks2"); out["mkdir_other"] = "ok"
except OSError as exc:
    out["mkdir_other"] = exc.errno
for name, path in (("git_hook", "/.git/hooks/pre-commit"), ("git_config", "/.git/config"),
                   ("plain", "/plain.txt")):
    try:
        open(cfg["project"] + path, "w").write("x"); out[name] = "ok"
    except OSError as exc:
        out[name] = exc.errno
print(json.dumps(out))
"""


@pytest.mark.skipif(not apparmor.loaded(), reason="K2 not loaded (sudo rootsh install --system)")
def test_k2_live_denies_git_hooks_inside_the_allowed_project(world: dict[str, object]) -> None:
    """Only K2 can deny this: bwrap binds the project rw and K1 allows it."""
    host = world["host"]
    project = world["project"]
    assert isinstance(host, Host)
    assert isinstance(project, Path)
    (project / ".git" / "hooks").mkdir(parents=True)  # as in a real repository
    probe = [str(host.python), "-I", "-c", K2_PROBE, json.dumps(world["cfg"])]
    argv = plan(Agent.CLAUDE_CODE, host, project, [], {}, probe=probe, k2=True).argv
    done = subprocess.run(argv, capture_output=True, text=True, timeout=60, check=False)  # noqa: S603
    assert done.returncode == 0, done.stderr
    out = json.loads(done.stdout.strip().splitlines()[-1])
    assert out["label"].startswith(apparmor.AGENT)
    assert out["git_hook"] == 13  # EACCES
    assert out["git_config"] == 13
    assert out["plain"] == "ok"
    assert out["mkdir_other"] == "ok"  # .git itself stays writable
    assert not (project / ".git" / "hooks" / "pre-commit").exists()
