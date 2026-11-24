# P9 design: `rootsh run`, kernel layers K1–K3, launch trust, endpoints

Plan B2 and gate P9: *denials verified end to end; the agent can't tamper
with rootsh.* Governed by ADR-0018. On 2026-11-24 the author chose the core
split: everything below is built and adversarially tested without root.
ADR-0018's root system service, audit-based denial attribution and
interactive prompt confirmation are **not** built and are reported as
limits (§8).

## 1. Flow

```
rootsh run claude|opencode [--project DIR] [--env NAME]... [-- agent args]
  │ 1. control socket (outside any scope): register {launch, agent, project, home}
  │ 2. systemd-run --user --scope --unit rootsh-agent-<launch>
  │      └ bwrap (visibility)  └ python -I -m rootsh.kernel.confine (K2 attach, K1, K3)  └ agent
  │ 3. on exit: unregister
  ▼
hooks (inside the scope) ──event socket──▶ daemon: peer pid → /proc/pid/cgroup → launch
```

## 2. Two endpoints (ADR-0018 §2)

| socket | path | visible in sandbox | ops |
|---|---|---|---|
| event | `$XDG_RUNTIME_DIR/rootsh/rootsh.sock` (P8 path, unchanged) | yes, its directory only | `ping`, `event` |
| control | `$XDG_RUNTIME_DIR/rootsh-control/control.sock` | **no** | `ping`, `approve`, `register`, `unregister` |

`approve` on the event socket is an error. Control ops still refuse a peer
inside an agent scope (P7 check), so a direct launch that is not sandboxed
still cannot approve. Both sockets are 0600, same UID (root ownership is
§8).

## 3. Launch trust (ADR-0018 §1, core part)

- A launch is `{id, agent, project, home, unit, kernel layers}` registered
  by the launcher on the control socket. The project is the launcher's
  resolved real path; hook `cwd` never sets or changes it, which also gives
  OpenCode events (no `cwd`) a project, so F2 can fire for OpenCode.
- An event's launch is found from the **peer's cgroup** (SO_PEERCRED pid →
  `/proc/<pid>/cgroup` → `rootsh-agent-<id>.scope`), never from a field in
  the request. The pid is pinned with `SO_PEERPIDFD`: if the process has
  exited by the time its cgroup has been read, the event is refused.
- `require_launch` (default on): a tool call from a peer with no registered
  launch, or whose agent differs from the launch's, is **blocked**; other
  events from it are refused and not stored. With P9 installed, agents must
  be started with `rootsh run` (ADR-0018 consequence).
- **Model `allow` is clamped to `ask`** (layer `model`, reason recorded).
  F1/F2 fast paths are unchanged (intrinsic effect/probe paths).

## 4. Sandbox for a real project (`rootsh.run.sandbox`)

bwrap with `--unshare-all --share-net --die-with-parent`, `--clearenv`:

- read-only: `/usr`, `/etc`, `/run/systemd/resolve` (+ merged-/usr links);
  fresh `/dev`, `/proc`, tmpfs `/tmp`; **no** `/sys`, **no**
  `/run/user/<uid>` except the event socket directory, so no D-Bus,
  systemd user manager, cgroupfs, Wayland/X11 or ssh-agent;
- home is a tmpfs; only these are bound: the project (rw, real path);
  agent state (rw: `~/.claude`, `~/.claude.json`, `~/.local/share|state`
  and `~/.cache/opencode`); agent code and config (ro: the resolved agent
  install dirs, `~/.local/bin`, `~/.nvm`, `~/.gitconfig`,
  `~/.config/opencode` incl. rootsh's plugin, `~/.claude/settings.json`);
  rootsh's interpreter and code (ro: the venv, its base Python, `src/`), so
  the hook can run but cannot be changed. A checkout's `data/`, the runtime
  DB, models, manifests and the control socket are not bound at all.
- `--new-session` is **not** used for interactive runs (a TUI needs its
  controlling terminal); TIOCSTI is already off on this host
  (`dev.tty.legacy_tiocsti = 0` [RUN]) and K3 also denies it.
- environment: an allowlist (`TERM`, `COLORTERM`, `LANG`, `LC_*`, `PATH`,
  `USER`, `LOGNAME`, `SHELL`, `EDITOR`, `VISUAL`) plus `--env NAME` per
  extra variable; `HOME` set; `XDG_RUNTIME_DIR` points at a private tmpfs.

OpenCode: its default TUI connects to a host background service, which
would run tools outside the sandbox. The launcher starts `opencode serve`
inside the sandbox on a free local port with a fresh password and attaches
the TUI with `--server` (P4 pattern).

## 5. Kernel layers (`rootsh.kernel`)

All are applied in `rootsh.kernel.confine` inside bwrap, then `execvp`.
Any failure → exit 126, the agent does not start.

- **K1 Landlock**: full filesystem allowlist for the running ABI
  (ABI 1–4: bits 0–12/13/14; ABI ≥ 5 adds IOCTL_DEV): rw on the project,
  `/tmp`, `/dev`, agent state; read+exec on every read-only bind and
  `/proc`. TCP bind denied, connect 443 only (+ OpenCode's own port);
  abstract-UNIX and signal scoping. ABI ≥ 7: `LOG_NEW_EXEC_ON` (records
  appear only when audit is on, §8). Pathname-socket connect is not a
  Landlock right below ABI 9, so §4 visibility is what hides the control
  socket.
- **K2 AppArmor**: `rootsh.kernel.apparmor` renders profile `rootsh-agent`:
  allow-all base plus `audit deny` for writes to `.git/hooks/**`,
  `.git/config`, the OpenCode plugin dirs, every rootsh path; `ptrace`;
  `dbus`; `userns`; signals to unconfined peers; `mount`. Checked with
  `apparmor_parser -Q` in tests. Loaded by `sudo rootsh install --system`
  (author step). confine writes `exec rootsh-agent` to
  `/proc/self/attr/apparmor/exec` when the launch requires K2.
  *Changed while building (2026-11-24, results 09 §1): inside bwrap the
  `unpriv_bwrap` stack forbids `change_profile`, so K2 is entered outside,
  `aa-exec -p rootsh-bwrap -- bwrap …`, whose exec rule stacks
  `rootsh-agent//&unpriv_bwrap`; confine only checks the label.* Policy: if
  the profile file is installed, K2 is required (missing load → refuse);
  if not installed, the launch records `k2=absent` and warns.
- **K3 seccomp**: native x86-64 only (other arch / x32 → kill); EPERM for
  `ptrace`, `process_vm_readv/writev`, `bpf`, `perf_event_open`,
  `userfaultfd`, mount family (`mount`, `umount2`, `pivot_root`,
  `open_tree`, `move_mount`, `fsopen`, `fsconfig`, `fsmount`, `fspick`,
  `mount_setattr`), keyring (`keyctl`, `add_key`, `request_key`), modules
  (`init_module`, `finit_module`, `delete_module`), `kexec_*`, and `ioctl`
  TIOCSTI/TIOCLINUX. `SECCOMP_FILTER_FLAG_LOG` set. No `unlink`/`socket`
  blocks (plan B2).

## 6. Self-protection map (ADR-0018 §6)

| artifact | agent sees | how |
|---|---|---|
| hook interpreter, rootsh `src/`, OpenCode plugin, managed settings | read/exec, no write | ro-bind + K1 ro + K2 deny |
| runtime DB/WAL/SHM, models, install manifests, control socket | nothing | not bound |
| research `data/`, `.venv` write | nothing / ro | not bound / ro-bind |
| daemon process | no signal, no ptrace | pid ns + K1 signal scope + K3 + K2 |
| project `.git/hooks`, `.git/config` | read; write denied only with K2 loaded | K2 |

## 7. Tests (adversarial, in tmp roots; no host state changes)

`tests/kernel/` and `tests/run/` run the real confine/bwrap argv against
canaries: read of a hidden home file, write outside project, write to the
ro hook code, connect to the control socket path, TCP connect to a local
listener, bind, signal/ptrace of an outside pid, mount, bpf, keyctl, unshare
of the scope (cgroupfs absent), D-Bus path absent; an unregistered peer's
tool call blocks; a forged `launch` field is ignored; approve on the event
socket refused; model allow → ask.

## 8. Not built (reported in results 09 as limits)

Root-owned sockets and a root-installed `User=` system service; the
journald/audit denial reader, runtime denial table and denial pop-ups
(audit is off on this host, `auditd` inactive [RUN]); interactive prompt
confirmation, so a prompt recorded from a hook is still trusted for model
features (its effect is bounded by the allow→ask clamp, but a forged prompt
can still move a block to an ask); third-party OpenCode plugins/MCP are not
disabled; seccomp is native-x86-64 only.
