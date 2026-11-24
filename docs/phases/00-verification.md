# P0: Verification log

> Merged in v0.12.0 from `docs/results/00-verification.md` (tag v0.11.0). The text is unchanged except heading levels and paths. "Design NN" and "results NN" in other files mean the two parts below.

## Results

*P0 — Verification log*

Every claim here is tagged `[RUN]` (measured on this host, raw data in
`v0.11.0:docs/results/data/p0/` and reproducible from `v0.11.0:research/p0-verification/`), `[DOC]` (official
documentation), or `[RUN, author-run]` (measured on this host by the author,
because it needed sudo or a click; `v0.11.0:research/p0-verification/user_checks/run.sh`).

**Host:** Ubuntu 26.04.1 LTS, kernel 7.0.0-43-generic, Claude Code 2.1.325,
OpenCode v2.3.0, Python 3.14.4 (system), uv available, no passwordless sudo.

### Gate status

P0 gate (plan.md): measured proof for each item, written to this log.

| Item | Status |
|---|---|
| Claude Code `deny` stops the command | ✅ [RUN] §1 |
| `ask` in auto and bypass modes | ✅ headless [RUN] · interactive [RUN, author-run] §1 |
| `onFailure` behavior | ✅ [RUN] §1 |
| Managed-settings path and enforcement | ✅ [DOC] + [RUN, author-run] §1 |
| OpenCode v2.3.0 block mechanism | ✅ [RUN] §2 |
| Transcript / event formats | ✅ [RUN] §1, §2 |
| Landlock ABI and enforcement | ✅ ABI 8 [RUN] §3.2 |
| seccomp filter | ✅ [RUN] §3.3 |
| AppArmor profile loads and enforces | ✅ [RUN, author-run] §3.4 |
| A BPF-LSM program loads | ✅ [RUN, author-run] §3.5 |
| Bash parser chosen | ✅ tree-sitter-bash [RUN] §4 |
| Desktop ask channel | ✅ [RUN] + click [RUN, author-run] §5 |

**Gate passed.** All 12 items have measured proof; the five author-run
checks in §6 are recorded with their raw results.

### Consequences for the design

What P0 changes or pins down in `plan.md`:

1. **Rootsh never answers `allow` to Claude Code.** A hook `allow` bypasses
   Claude Code's own permission rules [RUN]; a clean action gets no decision.
2. **Every hook fails closed explicitly.** Claude Code: `onFailure: "block"`
   on every entry, and the client maps internal errors to exit 2 [RUN].
   OpenCode: setup never throws (it fails open [RUN]); hook-time errors throw,
   which blocks.
3. **Rootsh owns the ask for OpenCode.** OpenCode's `ask` runs the command
   under `--auto` [RUN]; Rootsh asks the user itself and blocks by throwing in
   `tool.execute.before`. Claude Code headless `ask` = deny [RUN].
4. **The desktop ask uses D-Bus directly**, not `notify-send -A`, which is
   refused on this host [RUN].
5. **Provenance reads documented hook payloads** (`tool_input`,
   `tool_response`, OpenCode `tool.execute.after.result`), linked to the
   user's prompt by `prompt_id` / `messageID` [RUN]. The Claude Code
   transcript is an undocumented internal format: fallback only.
6. **`rootsh run opencode` sets `$PWD`** — OpenCode ignores the process cwd
   [RUN].
7. **The OpenCode shell hook can scrub secret-bearing environment variables**
   before the agent's shell starts [RUN] — a cheap exfiltration hardening.
8. **Effect extractor = tree-sitter-bash** with wrapper unwrapping and literal
   `bash -c` re-parsing [RUN].
9. **K1 Landlock carries most of the kernel layer** (ABI 8: filesystem, TCP
   connect, signal scoping, all unprivileged and inherited) [RUN]. K2
   AppArmor covers only what Landlock cannot express (deny inside an allowed
   tree). K3 seccomp stays a 4-syscall deny-list.

**Trace budget input (decision 2):** a one-tool Claude Code session on Haiku
cost $0.0026–$0.0037, mean $0.0028, over 13 runs [RUN]. OpenCode runs on
`opencode/*-free` models cost $0 [RUN]. P4 sizes the trace budget from
these.

---

### 1. Claude Code hooks

**Method.** `v0.11.0:research/p0-verification/claude_code/run_probes.py` runs one fresh
`claude -p --model haiku` session per case, in its own workspace, with hooks
passed via `--settings`. A probe hook (`hook_probe.py`) logs its stdin event and
answers in the case's mode. The model is asked to create `./marker`; the marker
exists only if the tool call executed. The control case proves the harness
works (marker created when the hook makes no decision). 13 cases, total model
cost $0.0364 [RUN].

#### Results [RUN]

| Case | Hook response | Permission mode | Tool | Marker created | Denials |
|---|---|---|---|---|---|
| `control_record_bypass` | record | bypassPermissions | Bash | **yes** | 0 |
| `deny_json_bypass` | deny | bypassPermissions | Bash | no | 1 |
| `deny_exit2_bypass` | exit2 | bypassPermissions | Bash | no | 1 |
| `ask_bypass` | ask | bypassPermissions | Bash | no | 1 |
| `ask_default` | ask | default | Bash | no | 1 |
| `ask_auto` | ask | auto | Bash | no | 1 |
| `record_default` | record | default | Bash | no | 1 |
| `allow_default` | allow | default | Bash | **yes** | 0 |
| `crash_exit1_bypass` | exit1 | bypassPermissions | Bash | **yes** | 0 |
| `crash_exit1_onfailure_block_bypass` | exit1 + onFailure:block | bypassPermissions | Bash | no | 1 |
| `timeout_bypass` | sleep | bypassPermissions | Bash | **yes** | 0 |
| `timeout_onfailure_block_bypass` | sleep + onFailure:block | bypassPermissions | Bash | no | 1 |
| `deny_write_tool_bypass` | deny | bypassPermissions | Write | no | 1 |

#### Findings

1. **`deny` stops execution, in every permission mode, for Bash and Write.**
   JSON `permissionDecision: "deny"` and exit code 2 are equivalent [RUN].
2. **A crashing or timed-out hook fails open** (`exit1`, `sleep` → marker
   created). **`onFailure: "block"` makes both fail closed** [RUN].
   → Every Rootsh hook entry sets `onFailure: "block"`, and the hook client
   maps every internal error to exit 2.
3. **Hook `allow` overrides Claude Code's own permission check**
   (`record_default` is refused, `allow_default` runs) [RUN].
   → Rootsh never answers `allow`. A clean action gets *no decision* (exit 0,
   no output), so the user's own Claude Code permission rules still apply.
   Rootsh can only make Claude Code stricter, never looser.
4. **`ask` in a headless (`-p`) session is a denial** in bypass, default, and
   auto modes [RUN]. **Interactive `ask` shows Claude Code's native
   permission prompt, and the command runs after the user says yes**
   (author-run `./run.sh claude-ask`: prompt appeared, marker created)
   [RUN]. The run first stops at Claude Code's folder-trust question for a
   new directory. Raw: `v0.11.0:docs/results/data/p0/claude_ask_results.json`.
   → In headless runs the desktop channel carries the decision (§5).
5. **`PostToolUse` does not fire for a failed tool call;
   `PostToolUseFailure` does** [RUN] (first control run, where the model
   mis-quoted a path containing a space). Both are needed for provenance.
6. **Field sets** [RUN] (`p0/` → `research/p0-verification/claude_code/out/schema.json` (git-ignored probe output, never committed; deleted in v0.12.0)):
   - all three events: `session_id`, `prompt_id`, `transcript_path`, `cwd`,
     `permission_mode`, `scratchpad_dir`, `hook_event_name`
   - `UserPromptSubmit`: + `prompt`
   - `PreToolUse`: + `tool_name`, `tool_input`, `tool_use_id`, `effort`
   - `PostToolUse`: + `tool_response`, `duration_ms`
   `prompt_id` links every tool call to the user prompt that caused it — the
   key for the intent capsule.
7. **The transcript is an internal format** with 15 entry types
   (`user`, `assistant`, `attachment`, `api-request`, …) and content blocks
   `text`, `thinking`, `tool_use`, `tool_result`, `tool_addition` [RUN]. It is
   not a documented contract.
   → The provenance engine is built on the documented hook payloads
   (`tool_input`, `tool_response`); the transcript is an optional fallback
   behind a versioned reader.
8. **Managed settings path:** `/etc/claude-code/managed-settings.json` plus
   `managed-settings.d/`, read at startup and reloaded on change [DOC]. A
   managed file the OS refuses to read is *skipped* — the session starts
   without that policy [DOC].
   → The installer writes it root-owned **and world-readable (0644)**.
   **Author-run check** (`sudo ./run.sh managed-settings`) [RUN]: a
   record-only `PreToolUse` hook in that file (root-owned, 0644) **fired in a
   session started with `--settings '{"disableAllHooks": true}'`** (the
   check looks for its own `echo rootsh-p0` command in the hook log). So a
   user- or session-level setting cannot switch Rootsh's managed hooks off.
   The file and the `/etc/claude-code` directory it created were removed
   afterwards (verified). Raw: `v0.11.0:docs/results/data/p0/managed_settings_results.json`.

---

### 2. OpenCode v2.3.0 plugin API

**Method.** `v0.11.0:research/p0-verification/opencode/run_probes.py` runs one
`opencode run --standalone --format json` per case (a private server, so the
project-local plugin loads fresh) with `opencode/deepseek-v4.1-flash-free`
(cost $0). The probe plugin (`probe-plugin/index.ts`, V2 API,
`import { Plugin } from "@opencode/plugin"`, auto-discovered from
`.opencode/plugins/<name>/index.ts` [RUN]) logs every hook event and, per
mode, tries one way to stop the call. Marker file is ground truth; the control
case proves the harness. 11 cases [RUN].

#### Results [RUN]

| Case | Plugin behavior | `--auto` | Tool | Marker created | Tool outcome |
|---|---|---|---|---|---|
| `control_record_auto` | record | yes | shell | **yes** | completed |
| `record_no_auto` | record | no | shell | **yes** | completed |
| `deny_evaluate_auto` | deny | yes | shell | no | error: rootsh p0 probe: deny |
| `ask_evaluate_auto` | ask | yes | shell | **yes** | completed |
| `ask_evaluate_no_auto` | ask | no | shell | no | error: non-interactive run cannot ask, request rejected |
| `throw_execute_before_auto` | throw | yes | shell | no | error: rootsh p0 probe: blocked by throw |
| `throw_shell_create_auto` | shell-throw | yes | shell | no | error: rootsh p0 probe: shell blocked by throw |
| `setup_throw_auto` | setup-throw | yes | shell | **yes** | completed |
| `throw_execute_before_write_auto` | throw | yes | write | no | error: rootsh p0 probe: blocked by throw |
| `deny_evaluate_write_auto` | deny | yes | write | no | error: rootsh p0 probe: deny |
| `slow_deny_evaluate_auto` | waits 20 s, then deny | yes | shell | no | error: rootsh p0 probe: slow deny |

#### Findings

1. **Three working block mechanisms** [RUN]: throwing in
   `tool.execute.before`, throwing in `shell.create.before`, and setting
   `effect = "deny"` in `permission.evaluate`. The thrown message or
   `message` reaches the model as the tool error, so a block explains itself.
2. **Hook order** for one shell call [RUN]: `tool.execute.before` →
   `shell.create.before` → `permission.evaluate` → `tool.execute.after`.
   `tool.execute.before` sees every tool (`shell`, `write`, …) and runs first.
   → Rootsh decides in `tool.execute.before` and blocks by throwing.
3. **A plugin whose setup throws fails open** — the tool runs [RUN]. OpenCode
   has no `onFailure` equivalent.
   → The plugin's setup cannot throw (all I/O deferred to hook time); every
   hook catches its own errors and converts them to a block (a throw at hook
   time is itself a block, so runtime faults fail closed). A plugin that never
   loads is caught by the launcher: a session that never registers with the
   daemon is reported, and the kernel layer (K1/K2) contains it regardless.
4. **`ask` is unusable as a safety decision** [RUN]: with `--auto` it is
   auto-approved and the command runs; without `--auto` a headless run
   rejects it.
   → For OpenCode, Rootsh asks the user itself (§5) and then blocks or
   proceeds. A hook may wait at least 20 s for that answer without OpenCode
   timing out [RUN].
5. **OpenCode's default lets `shell` run without asking** (`record_no_auto`)
   [RUN]. Rootsh is the only gate on a stock install.
6. **Event fields** [RUN] (`research/p0-verification/opencode/out/schema.json` (git-ignored probe output, never committed; deleted in v0.12.0)):
   - `session.prompt`: `sessionID`, `messageID`, `prompt.text`, `prompt.files`,
     `delivery` — the user's request, for the intent capsule
   - `tool.execute.before`: `tool`, `input` (e.g. `input.command`), `id`,
     `sessionID`, `messageID`, `agent`
   - `shell.create.before`: `command`, `cwd`, `env`, `shell`, `timeout`
   - `permission.evaluate`: `action` (`shell`; the write tool reports `edit`),
     `resources`, `effect`, `source{type,id,messageID}`
   - `tool.execute.after`: + `status`, `result{output,content,metadata}` —
     the tool's output, for provenance
7. **OpenCode resolves its project directory from `$PWD`, not the process
   working directory** [RUN] (first run: plugin not loaded, markers written to
   the runner's directory). → `rootsh run opencode` sets `PWD` explicitly.
8. **`shell.create.before` receives the agent shell's full environment,
   including live credentials** (e.g. `*_TOKEN` variables) [RUN]. → Rootsh can
   scrub secret-bearing variables before the shell starts; the probe now
   records variable names only.
9. **OpenCode runs a background server on `127.0.0.1`** (port 49374 here)
   [RUN]. Whether its API is reachable by the agent's own shell is a
   self-protection question for P8/P9.

---

### 3. Kernel layer

#### 3.1 Host capabilities [RUN]

| Check | Value |
|---|---|
| Active LSMs (`/sys/kernel/security/lsm`) | `lockdown, capability, landlock, yama, apparmor, bpf, ima, evm` |
| `CONFIG_BPF_LSM`, `CONFIG_SECURITY_LANDLOCK`, `CONFIG_SECURITY_PATH`, `CONFIG_DEBUG_INFO_BTF`, `CONFIG_SECCOMP_FILTER` | all `y` |
| BTF (`/sys/kernel/btf/vmlinux`) | present |
| Lockdown | `none` |
| AppArmor | enabled; features include `file`, `network_v9`, `signal`, `ptrace`, `io_uring`, `dbus`, `mount`, `namespaces` |
| Unprivileged user namespaces | restricted (`apparmor_restrict_unprivileged_userns = 1`) |
| Unprivileged BPF | disabled (`unprivileged_bpf_disabled = 2`) |
| BPF toolchain | `bpftool` present; **`clang` and `libbpf-dev` headers absent** |

#### 3.2 Landlock (K1) — `v0.11.0:research/p0-verification/kernel/landlock_probe.py` [RUN]

Unprivileged, via raw syscalls (no dependency). **ABI version 8.** Each check
runs in a forked child that enforces its own domain.

| Check | Result |
|---|---|
| write in allowed dir | ok |
| write / unlink / rename-out / truncate in read-only dir | EACCES ×4 |
| read in read-only dir | ok |
| read of a path not in the ruleset (stands in for `~/.ssh`) | EACCES |
| `sh -c 'touch …'` in read-only dir after exec | Permission denied — the domain is inherited across exec |
| protected files after all attempts | intact |
| TCP connect to allowed port / other port | ok / EACCES |
| signal (`kill -0`) to a process outside the domain, before / after `SCOPE_SIGNAL` | ok / EPERM |

→ K1 can confine the agent's whole process tree to the project, make secrets
unreadable, restrict TCP ports, and stop the agent from signalling (killing)
the daemon. Landlock rules only *allow*, so "deny this subtree inside an
allowed tree" (e.g. `.git/hooks`) needs AppArmor (K2).

#### 3.3 seccomp (K3) — `v0.11.0:research/p0-verification/kernel/seccomp_probe.py` [RUN]

A hand-built BPF filter (arch check, x32 refused, deny-list of `ptrace`,
`mount`, `keyctl`, `bpf` → EPERM, everything else allowed), loaded
unprivileged after `no_new_privs`.

| Check | Without filter | With filter |
|---|---|---|
| `ptrace(PTRACE_TRACEME)` | ok | EPERM |
| ordinary file I/O | — | ok |
| `ptrace` from an exec'd child | — | EPERM — inherited across exec |

→ K3 stays a short deny-list of escape-relevant syscalls. It never blocks
`unlink` or `socket` (the old design's mistake). `keyctl` denial may break
credential helpers that use the kernel keyring; P9 re-checks it against real
agent sessions.

#### 3.4 AppArmor (K2) — `sudo ./run.sh apparmor` [RUN, author-run]

Profile `v0.11.0:research/p0-verification/user_checks/apparmor/rootsh-p0-probe.profile`, loaded with
`apparmor_parser -r`, entered with `aa-exec`, unloaded afterwards. Raw result:
`v0.11.0:docs/results/data/p0/apparmor_results.json`.

| Check | Confined | Control (no rootsh profile) |
|---|---|---|
| write in allowed tree | ok | — |
| write inside the `deny`-ed subtree | denied | ok |
| unlink inside the `deny`-ed subtree | denied | — |
| `kill -CONT` to a process under another profile | **denied** | ok |
| `kill -0` to the same process | ok (not stopped) | — |
| protected file after all attempts | intact | — |

Findings:
1. **Deny-inside-allow works**, which is the job Landlock can't do [RUN].
2. **AppArmor blocks real signals across profiles, but not `kill -0`**
   [RUN]. Signal 0 only tests existence, so the agent can't kill the daemon.
   Landlock `SCOPE_SIGNAL` denies both (§3.2).
3. **The first run was invalid, and the reason matters for P9.** Every
   confined `touch` was refused at *exec*: on Ubuntu 26.04 `/usr/bin/touch`
   resolves to the uutils binary `/usr/lib/cargo/bin/coreutils/touch`, and
   AppArmor mediates the resolved path (kernel audit log) [RUN]. The profile
   was fixed and the check re-run. → The agent profile must cover
   `/usr/lib/cargo/bin/**`.
4. **Explicit `deny` rules are silent**: the denied write and unlink left no
   audit record, while the exec and signal denials did [RUN]. → K2 rules use
   `audit deny`, so Rootsh sees kernel-level blocks and can notify the user.
5. **Wording correction:** the signal target ran under the `claude-desktop`
   AppArmor profile (the author's terminal is inside the Claude desktop app),
   not unconfined, as the kernel log's `peer="claude-desktop"` shows [RUN].
   The result field names say "unconfined_process"; the correct reading is
   "a process under another profile", which is the agent→daemon case.

#### 3.5 BPF-LSM (K4)

`sudo ./run.sh bpf-lsm` [RUN, author-run]. Toolchain: `clang` 21.1.8 and
`libbpf-dev` 1.6.3, installed with `--no-install-recommends` (10 packages,
25.1 MB; `libllvm21` and `libclang-cpp21` were already present). These are
system packages, not Python. Program: `v0.11.0:research/p0-verification/user_checks/bpf_lsm/deny_unlink.bpf.c`
(`lsm/inode_unlink`, denies unlinking one exact file name), built against the
running kernel's BTF and loaded with `bpftool prog loadall … autoattach`. Raw:
`v0.11.0:docs/results/data/p0/bpf_lsm_results.json`.

| Check (while loaded) | Result |
|---|---|
| unlink the target name, as the user | denied |
| unlink the target name, as root | denied |
| unlink a name that only starts with the target | ok |
| unlink an unrelated file | ok |
| unpin (detach) the program | ok |
| unlink the target after unpinning | ok |

→ **K4 is feasible on this host**: a BPF-LSM program loads, enforces against
root too, and detaches cleanly. Per decision 1, K4 stays a stretch goal
attempted after P9.

**The first author run failed, and the failure is a K4 design input.** The
first version read the file name into a buffer exactly as long as the target.
`bpf_probe_read_kernel_str` truncates, so every name *starting with* the
target matched. bpftool pins a program under its function name
(`rootsh_p0_deny_unlink`), which also starts with the target, so the program
blocked its own unpinning. Cleanup failed, and `set -e` ended the check before
it wrote a result. The program stayed attached and blocked deletion of every
file whose name started with `rootsh_p0_deny`, for root as well [RUN].
It was detached by renaming the pin (rename is not an unlink) and then
removing it. Three stuck test files were deleted afterwards. The fix:
- read one byte past the target and require the exact returned length;
- give the program a name its pin can never match;
- measure everything before unloading, and record a failed unload instead of
  aborting.

→ For K4: one wrong comparison in a BPF-LSM rule locks files machine-wide,
root included. Every K4 rule needs exact matching, tests before it loads, a
detach path that cannot match its own rule, and an unload the daemon proves
on shutdown.

---

### 4. Bash parser for the effect extractor (P2)

**Method.** `v0.11.0:research/p0-verification/parser/parser_bench.py` over `corpus.json`: 32
hand-labelled commands (pipelines, `&&`, substitutions, process substitution,
heredoc, loops, `case`, functions, `[[ ]]`, arithmetic, escapes, line
continuation, one malformed command). Expected output = bash semantics: the
simple commands that would run, and the file-redirection targets. Labels were
fixed before the first run and not changed after. Python 3.12.15, pinned in
`v0.11.0:research/p0-verification/parser/requirements.txt`.

| Parser | License | Correct | Mean parse | Misses |
|---|---|---|---|---|
| **tree-sitter-bash 0.25.1** (tree-sitter 0.26.1) | MIT | **30/32** | 0.02 ms | `[ -f x ]` is a `test_command` node, not a command named `[` (no effect either way); `time make` reports `time` (a bash keyword) as the command |
| bashlex 0.18 | **GPLv3+** | 28/32 | 0.163 ms | fails to parse `$((…))`, `case`, `[[ ]]`, `time` |

[RUN] — `v0.11.0:docs/results/data/p0/parser_results.json`. One tree-sitter miss in the
first run was a bug in the benchmark's own redirect extractor (`2>&1` read as
a file named `1`); the extractor was fixed and the corpus left unchanged.

Error tolerance [RUN]: tree-sitter always returns a tree and flags malformed
input (`echo "unterminated` and `rm -rf / &&` → `has_error = True`).
`bash -c '…'` parses as `bash` with a `raw_string` argument.

**Decision: tree-sitter-bash.** MIT (bashlex's GPLv3 would bind the project),
parses every construct in the corpus, 8× faster, never throws. P2 adds on
top of it:
- unwrapping of command wrappers and keywords (`time`, `sudo`, `env`, `nice`,
  `nohup`, `timeout`, `xargs`, `command`, `exec`);
- recursive parsing of literal `bash -c` / `sh -c` strings, so they are
  analysed rather than marked opaque; non-literal ones stay `opaque`;
- `has_error` → `opaque`, never a crash or a silent guess.

---

### 5. Desktop ask channel

| Check | Result |
|---|---|
| Notification server (`GetServerInformation`) | `gnome-shell` GNOME 50.1, spec 1.2, Wayland session [RUN] |
| Server capabilities (`GetCapabilities`) | `actions, body, body-markup, icon-static, persistence, sound` [RUN] |
| `notify-send 0.8.8 -A block=Block -A allow=Allow …` | **refused**: "Actions are not supported by this notifications server. Displaying non-interactively.", exit 1 [RUN] |
| Direct D-Bus `Notify` with actions (`v0.11.0:research/p0-verification/notify/dbus_notify_probe.py`), 4 s, no click | accepted (id returned), closed by our timeout, `action = null` [RUN] |
| Clicking a button delivers `ActionInvoked` | ✅ author clicked Allow: `action = "allow"`, signals `ActivationToken` → `ActionInvoked` → `NotificationClosed`, 2.0 s [RUN, author-run] |

→ The `notify-send` CLI cannot carry the decision on this host, even though
the server supports actions (a D-Bus trace shows libnotify's request reaching
GNOME's notification service). `rootsh-notifier` calls
`org.freedesktop.Notifications` directly over D-Bus. No answer within the
timeout = no decision = block. If the click check fails, the fallback is a
`zenity --question` dialog (zenity 4.2.1 present [RUN]).

The first author run returned no action: spec-1.2 servers emit
`ActivationToken` before `ActionInvoked`, and the probe stopped on the first
signal for its notification. The probe now waits for `ActionInvoked` or
`NotificationClosed` only. `rootsh-notifier` must do the same. Raw:
`v0.11.0:docs/results/data/p0/notify_results.json`.

---

### 6. Checks that need the author — `v0.11.0:research/p0-verification/user_checks/run.sh`

No passwordless sudo on this host, and two checks need a human click. Each
check cleans up after itself and writes `research/p0-verification/user_checks/out/<check>.json` (git-ignored probe output, never committed; deleted in v0.12.0).

| Command | Verifies | Status |
|---|---|---|
| `sudo ./run.sh apparmor` | K2: deny-write inside an allowed tree, unlink denied, signalling an outside (user-owned) process denied, unconfined control succeeds. Profile compiles unprivileged (`apparmor_parser -Q -K -T`) [RUN]. | ✅ §3.4 |
| `sudo ./run.sh bpf-lsm` | K4: an `lsm/inode_unlink` program builds, loads with `bpftool … autoattach` (bpftool 7.7.0 supports it [RUN]), denies unlink of one file name, and stops denying after unload. Needs `clang` + `libbpf-dev`; reports what is missing. | ✅ §3.5 |
| `sudo ./run.sh managed-settings` | A hook in `/etc/claude-code/managed-settings.json` (0644) fires even when the session passes `disableAllHooks: true`. Refuses to run if the file already exists. | ✅ §1 |
| `./run.sh notify` | A click on the D-Bus notification returns `allow`. | ✅ §5 |
| `./run.sh claude-ask` | Interactive Claude Code shows a permission prompt for a hook `ask`, and the command runs after "yes". | ✅ §1 |

---

### Side effects during probing

Probe actions that landed outside their workspaces or outlived their check. All were found by
checking, and removed.

1. **Claude Code** [RUN]: the first control run asked the model to `touch` an
   absolute path containing a space. The model did not quote it, so `touch`
   created an empty file `/home/darshil/Desktop/Vs`. This is a small, real
   instance of the harm Rootsh targets — an effect outside the target the
   user named — and is kept as a candidate hallucination trace for P4.
2. **OpenCode** [RUN]: the first run used the process cwd instead of `$PWD`
   (§2 finding 7), so two marker files (one empty, one containing `x`) were
   written into `v0.11.0:research/p0-verification/opencode/`.
3. **BPF-LSM** [RUN]: the first author run left its program attached
   (§3.5), so files named `rootsh_p0_deny…` could not be deleted. While
   confirming that it was still active, two more test files were created
   (`/tmp/rootsh_p0_deny_probe_x`, `/tmp/rootsh_p0_deny_t2`) and stayed
   stuck until the program was detached. All were removed after detaching.
