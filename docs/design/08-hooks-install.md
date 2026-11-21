# P8 design: agent hooks, installer, doctor, uninstall

Plan B3/B4 and gate P8: *a blocked action provably doesn't run in both
agents; uninstall is clean.* Choices are fixed here, before any live run
(ADR-0017). The author chose (2026-11-21): prove the gate inside the P4
sandbox and on a temporary install root; the real `sudo` install on the
host is the author's own step afterwards.

## 1. Flow

```
Claude Code ──hook (stdin JSON)──▶ python -m rootsh.hooks.claude_code --socket S ─┐
OpenCode plugin ──spawn, stdin──▶ python -m rootsh.hooks.opencode --socket S ─────┤
                                                                                   ▼
                                                   rootsh.daemon.client.decide() ─▶ daemon (P7)
```

Both agents reach the daemon through **one** fail-closed function,
`rootsh.daemon.client.decide()` (plan B4); neither hook re-implements
transport, parsing or timeouts. Paths (Python, socket) are fixed absolute
arguments written by the installer, never read from the environment, which
the agent's own process tree can change.

## 2. Claude Code mapping (P0 §1 consequences 1–2)

| hook | rootsh | Claude Code sees |
|---|---|---|
| `UserPromptSubmit` | record | exit 0; daemon unreachable → exit 2 (prompt blocked) |
| `PreToolUse` `*` | `decide(…, ask="native")` | allow → **exit 0, no output** (never `allow`); ask → `permissionDecision: "ask"`; block → `deny` + reasons |
| `PostToolUse` `*` | record | exit 0; failure → exit 2 (reason shown to Claude; the tool already ran) |

- Matcher `*`: every tool, MCP and unknown tools included. A tool rootsh
  cannot read is blocked by the daemon (P7 failure layer).
- Every entry: `onFailure: "block"`, `timeout` 30 s (client 5 s). Any
  exception in the hook → exit 2 with a one-line reason; nothing is
  silently swallowed.
- Headless `claude -p`: `ask` is a denial (P0 §1); this fails closed and is
  reported, not changed.
- Oversized payloads: string fields longer than the normalizer's text cap
  are cut before sending, so a large `tool_response` can never exceed the
  daemon's 4 MiB request limit and turn a post-record into a failure.

## 3. OpenCode mapping (P0 §2–§3)

- The plugin spawns the Python bridge **asynchronously** (no `spawnSync`:
  a 60 s desktop ask must not freeze OpenCode) and awaits it, so a prompt or
  tool output is recorded before the next tool call is decided.
- `tool.execute.before`: `decide(…, ask="desktop")`. The bridge prints one
  JSON line; the plugin lets the call run **only** if the process exited 0
  **and** the line parses **and** `decision === "allow"`. Everything else
  (spawn error, timeout, non-zero exit, garbage) throws, which blocks.
- `session.prompt` / `tool.execute.after`: recorded; errors are written to
  the plugin's error log, never thrown from setup (setup errors fail open in
  OpenCode, P0 §2).
- Config (Python path, socket) is a JSON literal baked into the installed
  file by the installer.

## 4. Install layout (user part + system part)

ADR-0016 keeps the daemon a **user** service until P9; only Claude Code's
managed settings need root. So install is two explicit steps, each atomic
and recorded in a manifest:

| step | runs as | writes |
|---|---|---|
| `rootsh install` | author | model bundle copy (hash-verified) → `~/.local/share/rootsh/models/<sha>/`; runtime DB dir `~/.local/state/rootsh/`; unit `~/.config/systemd/user/rootsh-daemon.service`; plugin `~/.config/opencode/plugins/rootsh/index.ts`; manifest `~/.local/state/rootsh/install.json`; `systemctl --user enable --now` |
| `sudo rootsh install --system` | root | `/etc/claude-code/managed-settings.json` (rootsh hooks merged into any existing content; 0644 root); manifest `/etc/claude-code/rootsh-install.json` (0600 root) |

Order: user part first (daemon up), then system part (hooks), so hooks are
never live without a daemon. Uninstall runs in reverse.

- **Writes are atomic** (temp file in the same directory, `fsync`,
  `rename`), never follow symlinks at the target, and refuse a target that
  is a symlink or not a regular file.
- **The manifest** stores, per file: path, SHA-256 written, mode, and the
  prior bytes (base64) if the file existed. Uninstall:
  - file unchanged since install → restore prior bytes, or delete if it did
    not exist (byte-identical revert);
  - file changed since install → remove only rootsh's own entries
    (managed settings) or refuse that file and report it (`--force` to
    remove anyway); never a silent overwrite of the author's edits.
- `--root DIR` re-roots every path (tests, dry runs); `systemctl` is an
  injected runner, so tests never touch the real host.
- Re-running install is idempotent (same bytes → no change).
- The unit is hardened where a user unit allows it (`NoNewPrivileges`,
  `UMask=0077`, `ProtectSystem=strict` with the state dir writable,
  `PrivateTmp`, `Restart=on-failure`); doctor verifies it actually runs.

## 5. Doctor

`rootsh doctor [--root DIR]` prints one PASS/FAIL line per check and exits
non-zero on any FAIL:

1. managed settings: exists, regular file, root-owned, not group/world
   writable, has the three rootsh hook entries with `onFailure: "block"`;
2. hook command's Python exists;
3. daemon: unit active, socket answers `ping`, model hash = manifest's;
4. **actually blocking**: runs the *installed* Claude Code hook command with
   a synthetic `PreToolUse` that writes `~/.claude/settings.json` (I1
   defense, deterministic) → must print `deny`; and a synthetic `ls` →
   must print nothing, exit 0. Same for the OpenCode bridge (block → throws
   path, read → allow). Synthetic events use a `rootsh-doctor-<uuid>`
   session id;
5. OpenCode plugin present and hash = manifest's;
6. Landlock ABI (reported for P9).

## 6. Gate (fixed before running)

| # | criterion | evidence |
|---|---|---|
| 1 | Claude Code: a blocked action does not run | real `claude -p` (Haiku) in the P4 sandbox with the **installer's own** hook JSON via `--settings`, real daemon + P6 bundle; canary dir intact; daemon decision row = block |
| 2 | OpenCode: a blocked action does not run | real `opencode run` (NIM) in the P4 sandbox with the **installer's own** plugin file; canary intact; decision row = block |
| 3 | control: an allowed action does run, in both | same harness; marker created; decision row = allow |
| 4 | daemon down → blocked, in both | same harness without daemon; canary intact |
| 5 | uninstall is clean | temp root: tree snapshot (paths, bytes, modes) before install == after uninstall, for empty and pre-populated managed settings; changed-file and symlink cases refuse |
| 6 | fail-closed unit tests | every hook error path → block/exit 2; plugin bridge garbage → throw |
| 7 | ruff, format, mypy strict, pytest | all pass |

Blocked case: `rm -rf ./keep` on a directory holding a canary (P7 smoke:
model block). Defense case: write `~/.claude/settings.json` (I1). The
controls make the gate falsifiable: if the harness never ran tools, the
control would fail.

## 7. Known limits (disclosed, carried to P9)

- **Event forgery.** Hooks run inside the agent's process tree, so the
  agent can also connect to the socket and send a fake `UserPromptSubmit`
  (authority) or `tool_post`. `SO_PEERCRED` cannot tell a hook from the
  agent's shell. P9 must close this (e.g. Landlock denies the socket to the
  agent's tool processes; hooks reach it through a non-agent broker).
- **Self-protection** of the venv, bundle, DB, unit and plugin is P9
  (Landlock/AppArmor); P8 files are owned by the author.
- **OpenCode env scrubbing** (`shell.hook("create.before")`, P0 consequence
  7) is not in P8.
