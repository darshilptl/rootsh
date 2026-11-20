# P7 design: daemon, policy (invariants + fast paths + model), ask channels

Plan B5 steps 5–7 and gate P7: *errors → block; the agent cgroup cannot
approve; pop-up content tested.* Choices are fixed here, before any number
is measured (ADR-0016).

## 1. Flow

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

## 2. Policy (pure function; first rule that fires wins)

| layer | rule | decision |
|---|---|---|
| failure | any exception, unparseable payload, missing/tampered bundle, timeout | **block** |
| I1 defense | a non-read effect on, or a read of, a defense path: the `rootsh` sensitivity class, Claude Code settings files (`~/.claude/settings*.json`, `<p>/.claude/settings*.json`), OpenCode plugin dirs (`~/.config/opencode/plugin[s]`, `<p>/.opencode/plugin[s]`); a process effect naming `rootsh` | **block** |
| I2 untrusted | a critical argument whose only source is tool content (`content_only`) on an effect of level 3 (delete, privilege, egress network) | **block** |
| F1 read-only | every effect is a read, nothing opaque, no read in class `secret` | **allow** |
| F2 in-project edit | every effect is a level-1 write (`create`, `append`, `modify`) or a non-secret read, all paths inside the project or `/tmp`, no sensitivity class, nothing opaque, no content-only argument | **allow** |
| M model | `full` bundle model, isotonic, conformal (α 0.10) | allow / ask / block |

Effect levels are P6's (`rootsh.model.features.effect_level`).
F2 does not exclude fabricated arguments: a file the agent creates for the
task is usually named by the agent, not the user. Path
classes are the union of `classify()` on each path and, at runtime, the
host-state probe's classes (symlink targets, sensitive entries inside a
directory). Probes can only add classes, so they only make the policy
stricter. Model features are exactly P6's (no probe features: the model
was trained without them).

**Allow means "no decision" to Claude Code** (P0 consequence 1); P8 maps it.

## 3. Ask channels

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

## 4. Who may approve

`approve` is rejected unless the peer (SO_PEERCRED) has the daemon's uid
and its `/proc/<pid>/cgroup` path contains no agent scope. Agent scopes
are units named `rootsh-agent-*.scope`; `rootsh run` (P9) must launch
agents in one. A peer whose cgroup cannot be read is rejected. `event`
requests from a different uid are rejected too.

Known gap (disclosed, closed in P9): an agent that can reach the systemd
user manager or session bus could start a process outside its scope.
P9's Landlock rules must deny `/run/user/<uid>/bus` and
`/run/user/<uid>/systemd` to agents.

## 5. Host-state probes: which process runs them

The daemon runs as the author (a systemd **user** service in P8, outside
the agent scope), so it can read `$HOME` and the project. A root system
service (plan B3) could not read a private home without extra rights; the
uid check and the agent-scope check give the separation P7 needs. Probes
are read-only and capped (P2 walk cap); latency is measured on a large
repository (results §3).

## 6. Fail-closed client

`rootsh.daemon.client.request()` returns a block reply on: socket missing,
connect refused, timeout (5 s; ask timeout + 5 s for desktop asks),
malformed reply, or an unknown decision. P8's hook commands use it.

## 7. Storage

Runtime migration 2 adds `decisions` (session key, event seq, decision,
layer, reasons JSON, features JSON, risk, bundle SHA-256, channel, answer,
decided_at).

## 8. Acceptance (gate)

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
