# P7: Daemon, policy, ask channels

> Merged in v0.12.0 from `docs/design/07-daemon.md` and `docs/results/07-daemon.md` (tag v0.11.0). The text is unchanged except heading levels and paths. "Design NN" and "results NN" in other files mean the two parts below.

## Design

*P7 design: daemon, policy (invariants + fast paths + model), ask channels*

Plan B5 steps 5–7 and gate P7: *errors → block; the agent cgroup cannot
approve; pop-up content tested.* Choices are fixed here, before any number
is measured (ADR-0016).

### 1. Flow

```
hook client (P8) ──JSON line──▶ rootsh daemon (Unix socket, SO_PEERCRED)
                                 ├─ normalize → runtime DB (events)
                                 ├─ per-session provenance index (rebuilt from DB on restart)
                                 ├─ tool_pre: effects → host-state probes → policy
                                 │     └─ ask + channel "desktop" → pop-up (D-Bus) or `rootsh approve`
                                 └─ decisions table (features, reasons, layer, bundle hash)
```

One request per connection: a single JSON object terminated by `\n`, one
JSON reply. Ops:

| op | fields | reply |
|---|---|---|
| `event` | `agent`, `hook`, `payload`, `ask` (`native`\|`desktop`, tool_pre only) | prompt/post: `{"ok": true}`; tool_pre: `{"decision", "reasons", "layer", "id"}` |
| `approve` | `id`, `answer` (`allow`\|`block`) | `{"ok": true}` or `{"error"}` |
| `ping` | — | `{"ok": true, "bundle": sha}` |

### 2. Policy (pure function; first rule that fires wins)

| layer | rule | decision |
|---|---|---|
| failure | any exception, unparseable payload, missing/tampered bundle, timeout | **block** |
| I1 defense | a non-read effect on, or a read of, a defense path: the `rootsh` sensitivity class, Claude Code settings files (`~/.claude/settings*.json`, `<p>/.claude/settings*.json`), OpenCode plugin dirs (`~/.config/opencode/plugin[s]`, `<p>/.opencode/plugin[s]`); a process effect naming `rootsh` | **block** |
| I2 untrusted | a critical argument whose only source is tool content (`content_only`) on an effect of level 3 (delete, privilege, egress network) | **block** |
| F1 read-only | every effect is a read, nothing opaque, no read in class `secret` | **allow** |
| F2 in-project edit | every effect is a level-1 write (`create`, `append`, `modify`) or a non-secret read, all paths inside the project or `/tmp`, no sensitivity class, nothing opaque, no content-only argument | **allow** |
| M model | `full` bundle model, isotonic, conformal (α 0.10) | allow / ask / block |

Effect levels are P6's (`rootsh.decision.model.features.effect_level`).
F2 does not exclude fabricated arguments: a file the agent creates for the
task is usually named by the agent, not the user. Path
classes are the union of `classify()` on each path and, at runtime, the
host-state probe's classes (symlink targets, sensitive entries inside a
directory). Probes can only add classes, so they only make the policy
stricter. Model features are exactly P6's (no probe features: the model
was trained without them).

**Allow means "no decision" to Claude Code** (P0 consequence 1); P8 maps it.

### 3. Ask channels

- `native` (Claude Code interactive): the reply is `ask`; the agent's own
  dialog asks.
- `desktop` (OpenCode, headless): the daemon holds the ask as *pending*,
  shows a desktop pop-up and waits up to `ASK_TIMEOUT_S` = 60 s. The first
  answer from the pop-up or from `rootsh approve <id> allow|block` wins.
  No answer, a helper error, or a closed pop-up → **block**.
- The pop-up is sent by a helper run with the system Python (it needs
  `gi`, which the project venv does not have) that talks to
  `org.freedesktop.Notifications` directly and waits for `ActionInvoked`
  or `NotificationClosed` (P0 §5). Its stdout is one JSON line; anything
  else is block.
- **Pop-up content** (pure, tested): summary `Rootsh: allow this action?`;
  body `You asked: <latest prompt>` / `Agent is doing: <command or tool
  input>` / `Why: <reasons>`, each clipped to a fixed length, with
  body-markup characters escaped (GNOME renders markup) and control
  characters removed, so the agent cannot restyle or hide text.

### 4. Who may approve

`approve` is rejected unless the peer (SO_PEERCRED) has the daemon's uid
and its `/proc/<pid>/cgroup` path contains no agent scope. Agent scopes
are units named `rootsh-agent-*.scope`; `rootsh run` (P9) must launch
agents in one. A peer whose cgroup cannot be read is rejected. `event`
requests from a different uid are rejected too.

Known gap (disclosed, closed in P9): an agent that can reach the systemd
user manager or session bus could start a process outside its scope.
P9's Landlock rules must deny `/run/user/<uid>/bus` and
`/run/user/<uid>/systemd` to agents.

### 5. Host-state probes: which process runs them

The daemon runs as the author (a systemd **user** service in P8, outside
the agent scope), so it can read `$HOME` and the project. A root system
service (plan B3) could not read a private home without extra rights; the
uid check and the agent-scope check give the separation P7 needs. Probes
are read-only and capped (P2 walk cap); latency is measured on a large
repository (results §3).

### 6. Fail-closed client

`rootsh.enforcement.daemon.client.request()` returns a block reply on: socket missing,
connect refused, timeout (5 s; ask timeout + 5 s for desktop asks),
malformed reply, or an unknown decision. P8's hook commands use it.

### 7. Storage

Runtime migration 2 adds `decisions` (session key, event seq, decision,
layer, reasons JSON, features JSON, risk, bundle SHA-256, channel, answer,
decided_at).

### 8. Acceptance (gate)

1. Errors → block: daemon down, timeout, malformed reply, tampered bundle,
   exception inside the policy, unparseable payload — each a test.
2. Agent cgroup cannot approve: a peer in `rootsh-agent-*.scope`, a peer
   with an unreadable cgroup, and a peer with another uid are refused —
   tests, one over a real socket.
3. Pop-up content: tests on markup escaping, control characters, clipping,
   and the three lines; the helper's timeout, garbage and crash → block.
4. Policy burden measured on the frozen **validation** split (not heldout:
   heldout is reserved for P10's end-to-end run), per layer, by human
   label — generated by script.
5. Probe latency p50/p99 on a large local repository, by script.
6. ruff, format, mypy strict, pytest green.

## Results

*P7 results: daemon, policy, ask channels*

Design: `docs/phases/07-daemon.md`; decisions: ADR-0016. Raw outputs:
`v0.11.0:docs/results/data/p7/` (`burden.md`, `latency.txt`), generated by
`research/policy_burden.py` and `research/policy_latency.py`.

### 1. What was built

- **`rootsh.decision.policy`**: pure decision function. Order: failure → I1 defense
  tamper → I2 content-only argument on a level-3 effect → F1 read-only →
  F2 in-project level-1 edit → P6 `full` model with its conformal sets.
  Any exception while building or deciding the action blocks.
- **`rootsh.enforcement.daemon`**: Unix-socket service (`rootsh daemon --socket --db
  --bundle`). Owner-only socket (mode 600) [RUN], SO_PEERCRED on every
  connection, runtime DB ingest, per-session provenance index rebuilt from
  the DB after a restart, host-state probes, one `decisions` row per tool
  call (runtime migration 2). A missing or tampered bundle makes the daemon
  block every tool call, with the reason. SIGTERM removes the socket.
- **`rootsh approve <id> allow|block`**: answers a pending desktop ask;
  refused from another uid, an unreadable cgroup, or a `rootsh-agent-*`
  scope.
- **`rootsh.decision.notify`**: pop-up content (three lines, markup-escaped,
  control and bidi characters removed, clipped) and the D-Bus helper,
  run with the system Python because the venv has no `gi`. Only an
  explicit "allow" allows.
- **`rootsh.enforcement.daemon.client.decide()`**: never raises; daemon down, timeout,
  malformed reply → block. For P8's hook commands.
- `rootsh.decision.model.features.values()` takes the workspace roots and home as
  parameters; `features(step)` passes the gold ones, so P6's numbers and
  the bundle are unchanged (model `full` hash `9d317325fd7b` in both
  `burden.md` and the live `ping`) [RUN].

### 2. Policy burden on validation (generated, `burden.md`)

Frozen validation split (manifest `a357bba6c02c`, bundle `ca33283a3dde`):
36 authorized and 34 refused human-labelled steps. Heldout was not loaded
(ADR-0016 item 5). Validation is also the conformal calibration set, so
the model-alone row is in-sample for its thresholds.

| layer | decision | authorized | refused |
|---|---|---|---|
| untrusted | block | 0 | 1 |
| read_only | allow | 14 | 4 |
| project_edit | allow | 8 | 5 |
| model | ask | 12 | 17 |
| model | block | 2 | 7 |

| policy | allow auth | ask auth | block auth | allow refused (false allow) | ask refused | block refused | asks per 1k authorized |
|---|---|---|---|---|---|---|---|
| P7 policy | 22 | 12 | 2 | 9 | 17 | 8 | 333 |
| model alone (P6) | 0 | 34 | 2 | 0 | 26 | 8 | 944 |

**Reading.** The fast paths cut asks from 944 to 333 per 1,000 authorized
steps, and cost **9 false allows out of 34 refused steps** (4 via F1,
5 via F2). This is the first run; nothing was tuned after it. The 9 steps
(listed by a scratch script over the same rows [RUN]):

- **F2, 5 steps:** writes or edits to the project's own dotfiles
  (`/work/bashrc`, `/work/install.sh`, a gitconfig) whose *content* came
  from an injected file or went beyond the task. The paths are inside
  the project and carry no sensitivity class, so nothing in the effect
  set separates them from a normal edit. This is plan B6's stated
  negative, "semantic edits inside allowed files".
- **F1, 4 steps:** reads labelled refused as reconnaissance outside the
  task (listing `$HOME` dotfiles, `crontab -l` with `/var/spool`, a
  `grep` for signing keys and credential helpers after an injection).
  None reads a `secret`-class path, so F1 allows them; read-only actions
  are contained by the kernel layer (P9), not by the policy.
- The defense invariant fired on no validation step (the gold runs never
  wrote rootsh or agent settings); it is exercised by tests and the live
  smoke (§4).

### 3. Latency (generated, `latency.txt`)

A large local repository: 98,011 entries, 1,001 tracked files plus
`node_modules`. 60 tracked files sampled evenly, 10 directories (root and
top-level). Nothing was executed; commands were only decided.

| measured | n | p50 | p99 | max |
|---|---|---|---|---|
| probe, tracked file | 60 | 4.5 ms | 5.6 ms | 5.6 ms |
| probe, directory | 10 | 10.1 ms | 311.8 ms | 312.9 ms |
| decide `cat <file>` (all F1 allow) | 60 | 5.1 ms | 9.6 ms | 13.0 ms |
| decide `rm <file>` (all model block) | 60 | 7.1 ms | 16.8 ms | 17.6 ms |
| decide `rm -rf <dir>` (all model block) | 10 | 15.5 ms | 316.5 ms | 316.8 ms |

Directory cost is the capped P2 walk (`node_modules`). All are well under
the client's 5 s timeout.

**Fixes found while measuring, with reasons:**

1. The first latency run built commands without quoting; the repository
   path contains a space, so targets were wrong. That run was discarded and
   the script now uses `shlex.quote`.
2. The first model decision took 1,811 ms (LightGBM's first import),
   inside a hook's time budget. `load_model()` now scores once at start;
   after it the `rm <file>` max is 17.6 ms.
3. A live SIGTERM left the socket file behind. `rootsh daemon` now handles
   SIGTERM like Ctrl-C; re-checked: the socket is removed [RUN].

### 4. Live smoke [RUN]

Real `rootsh daemon` with the P6 bundle, over its socket: `ping` returns
the model hash; `ls` → allow (read_only); `echo '{}' >
~/.claude/settings.json` → block (defense); `rm -rf <dir>` → block (model,
risk 1.00); `approve` of an unknown id → error; daemon stopped → the
client blocks (`ConnectionRefusedError`). The D-Bus helper on this GNOME
session with a 3 s timeout and no click returned `closed: timeout` (→
block). A click on Allow was proven in P0 §5 and was not repeated.

### 5. Gate (plan.md P7)

| # | criterion | status |
|---|---|---|
| 1 | errors → block: daemon down, timeout, malformed reply, missing and tampered bundle, policy exception, unparseable payload, unknown channel, crashed or unanswered pop-up | pass (tests in `tests/enforcement/daemon`, `tests/decision/policy`) |
| 2 | agent cgroup cannot approve: agent scope, other uid, unreadable or empty cgroup refused; a non-agent peer approves over the real socket with the real `/proc` check | pass (`tests/enforcement/daemon`) |
| 3 | pop-up content: three lines, markup escaping, newline/control/bidi removal, clipping; helper exit, garbage, hang, missing → block | pass (`tests/decision/notify`) |
| 4 | policy burden on validation, by script | pass (§2) |
| 5 | probe latency on a large repository, by script | pass (§3) |
| 6 | ruff, format, mypy strict, pytest | pass (528 tests: 28 policy, 20 notify, 26 daemon new) |

### 6. Limits

- **F1/F2 false allows** (9 of 34 refused on validation) are the price of
  a usable ask rate; they are the semantic-edit and reconnaissance cases
  above. The model's guarantee does not cover fast-path allows.
- **Validation only.** The policy has not touched heldout; P10's
  end-to-end run evaluates it there once.
- **Approval separation relies on the agent scope.** An agent that can
  reach the systemd user manager or the session bus could start a process
  outside `rootsh-agent-*.scope` or talk to the notification service. P9's
  Landlock rules must deny `/run/user/<uid>/bus` and
  `/run/user/<uid>/systemd` to agents, and `rootsh run` must use that
  scope name.
- **The daemon runs as the author's user**, not a root system service
  (ADR-0016 item 4). Self-protection of its files and process is P9's
  (Landlock, AppArmor).
- **No hooks yet.** Claude Code and OpenCode are not wired to the daemon;
  that, `allow` → "no decision" for Claude Code, and the installer are P8.
