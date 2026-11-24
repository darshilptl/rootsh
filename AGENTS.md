# Rootsh — Agent Instructions

Rootsh is an M.Tech research project: a defense layer that decides, for every
action a host-level coding agent proposes, whether that action stays within the
authority the user granted, and stops it before irreversible harm when it does
not. The full design is `docs/plan.md`; recorded decisions are
`docs/decisions/`.

## 1. Scope

- Host: Ubuntu 26.04, Linux kernel 7.x.
- Agents: **Claude Code** and **OpenCode** only. Anything else is future work.
- Wording: "defense layer", never "tool" or "product". Rootsh measures a
  defense; it never claims to solve prompt injection.

## 2. Non-negotiables

1. **Every number comes from the DB or a test run.** Nothing estimated,
   partial, or hand-typed. Targets are labelled as targets.
2. **Tag every claim by how it was verified:** `[RUN]` measured here,
   `[READ]` from code, `[DOC]` from official documentation.
3. **No LLM judges an action.** Scoring is deterministic analysis plus a
   learned model over explicit features.
4. **Fail closed on system failure** (daemon down, timeout, missing or
   tampered model). Unknown commands are scored, never blanket-blocked.
5. **Attack payloads run only inside disposable VMs or sandboxes**, never on
   the host.
6. **Heldout data is evaluated once per frozen model**, and every evaluation
   is logged. Splits are by task/repo, never by row.
7. **Tests never touch a non-temporary database.**
8. **Verify before use.** Versions, flags, APIs, and paths are checked against
   the installed software or official docs and recorded; never guessed.

## 3. Code standard

- Small, pure, typed functions. No global mutable state. Explicit data types
  at module boundaries.
- No commented-out code, unused exports, TODO orphans, dead files, duplicated
  constants, or speculative abstraction.
- Comments explain *why*, only where the why is not obvious.
- Errors are handled where they can be handled, and surfaced otherwise —
  never swallowed.
- Every module ships with tests. Lint, type-check, and tests are green before
  a phase merges.

## 4. Phase process

Phases and gates are in `docs/plan.md` (build order table).

1. **Docs first.** Write `docs/design/NN-name.md` (flows, interfaces, edge
   cases, acceptance criteria) before code. Self-audit against it, and record
   results in `docs/results/NN-name.md`, every number from a run.
2. **Branch per phase.** Never work on `main`.
3. **Complete before next.** Gate passes with real output before merging.
4. **Squash merge, delete branch.** The phase's last commit sets
   `version = "0.N.0"` in `pyproject.toml` (the only place the version
   lives); tag `v0.N.0` after phase N merges.

## 5. Git workflow (trunk-based)

- `main` is always green. No dev or staging branch.
- Branch names: `prefix/short-description` — `feature/`, `fix/`,
  `refactor/`, `setup/`, `chore/`, `hotfix/`. One branch = one concern.
- Conventional Commits: `type(scope): short description`. Types: `feat`,
  `fix`, `chore`, `refactor`, `test`, `docs`, `build`, `ci`. The body lists
  what was built and how it was verified.
- Commit small and often, after each verified unit of work, never broken.
- Squash-merge into `main` when the phase gate passes; delete the branch.
- Never commit secrets, `.env*`, databases, model artifacts, or traces with
  personal data.

## 6. Layout

```
rootsh/
├── AGENTS.md  README.md  pyproject.toml  uv.lock
├── src/rootsh/            # the package: one folder per subsystem
│   ├── events/            #   normalized AgentEvent
│   ├── normalize/         #   hook payloads -> AgentEvent (Claude Code, OpenCode)
│   ├── storage/           #   runtime and research SQLite databases, migrations
│   ├── effects/           #   what an action would do (tree-sitter-bash)
│   ├── hoststate/         #   read-only facts about the paths it touches
│   ├── provenance/        #   where each critical argument came from
│   ├── policy/            #   invariants, fast paths, model -> allow/ask/block
│   ├── daemon/            #   Unix-socket service, peer checks, fail-closed client
│   ├── notify/            #   desktop pop-up content and D-Bus helper
│   ├── hooks/             #   Claude Code hook, OpenCode bridge + plugin (.ts)
│   ├── install/           #   install/uninstall manifests, unit/settings rendering, doctor
│   └── cli/               #   `rootsh` command line
├── tests/                 # mirrors src/rootsh/; shared helpers in tests/support/
│   └── fixtures/          #   real (sanitized) payloads, hand-labelled corpora
├── docs/
│   ├── plan.md            # audit + design + build order
│   ├── decisions/         # numbered decision records (ADR-NNNN)
│   ├── design/            # NN-name.md: one design doc per phase, written before code
│   └── results/           # NN-name.md: one results doc per phase; raw data in data/
├── research/              # experiments and the scripts behind reported numbers
│   ├── p0-verification/   #   P0 probes (kept as run)
│   ├── robustness/        #   NL2Bash robustness gate
│   ├── p3-transcripts/    #   safe recorders for real agent transcripts
│   └── old-audit/         #   scripts reproducing the researchwork audit (kept as run)
└── data/                  # local corpora and runtime files, git-ignored
```

Checks (all must pass before a merge): `uv run ruff check`,
`uv run ruff format --check`, `uv run mypy`, `uv run pytest`.

## 7. Session handoff (read this first in a new session)

Work runs **one session per phase**. A new session starts from a short prompt
("start P4") and orients itself from the repository, not from chat history.

**Current state:**
- **Done:** P0–P11, squash-merged on `main`, tagged `v0.0.0`–`v0.11.0`.
  All pushed to `origin` (P11 pushed 2026-11-24).
- **P4 result:** gold set of 192 sandboxed runs (176 OpenCode on NVIDIA NIM
  Nemotron, 16 Claude Code Haiku, $0.0432) in `data/gold/` (git-ignored).
  See `docs/results/04-trace-harness.md`.
- **P5 result:** labelled, audited and frozen dataset; manifest SHA-256
  `a357bba6c02cdb9f27c9e42bf93eb055a717c75df4506e59595018e56cbc7931` in
  `data/gold/freeze/` (heldout labels only in the sealed file). See
  `docs/results/05-labelling.md` and ADR-0014: training uses human labels
  only (70 train labels, 14 refusals; 4 G4 variants), R1 v2 labels are for
  coverage and separate reporting, assistant labels dropped (kappa 0.347).
  Commands: `rootsh label [--adjudicate|--variants]`, `rootsh variants`,
  `rootsh freeze`; reports `research/labels/{agreement,audits,plan}.py`.
- **P6 result:** bundle `ca33283a3dde` in `data/models/p6-a357bba6c02c/`
  (git-ignored), heldout evaluated once (5 rows in `model_runs`). Human
  heldout AUC: full 0.631, impact 0.648, rules 0.612, action 0.592,
  provenance 0.500; conformal (alpha 0.10) never allows, so 0 false allows
  but ~935 asks per 1k authorized steps. See `docs/results/06-model.md`
  and ADR-0015. Commands: `rootsh model train|evaluate --db --manifest
  --out`; tables `research/model/report.py <db> <bundle>`.
- **P7 result:** `rootsh daemon --socket --db --bundle` and `rootsh
  approve <id> allow|block`; policy order failure → defense → untrusted →
  read-only → in-project edit → model (ADR-0016). On validation: asks 333
  per 1k authorized (model alone 944), 9 of 34 refused steps falsely
  allowed by the fast paths (semantic edits in project dotfiles,
  reconnaissance reads). Decision p99 ≤ 17 ms on files, ~316 ms on large
  directories. See `docs/results/07-daemon.md`; scripts
  `research/policy/{burden,latency}.py`.
- **P8 result:** `rootsh install` (user unit, OpenCode plugin, bundle
  copy) and `sudo rootsh install --system` (Claude Code managed settings),
  `rootsh uninstall [--system]` from manifests, `rootsh doctor` (runs the
  installed hooks). Hooks: `rootsh.hooks.claude_code`, `rootsh.hooks.opencode`
  + `hooks/opencode_plugin.ts`, both via `client.decide()` (ADR-0017). Live
  gate in the P4 sandbox: 10/10 cases pass across both agents (run 1 of Claude
  Code reported unchanged, 2/5 under the original checks); async throw in
  OpenCode blocks. Fixed a quadratic URL regex in provenance mentions. See
  `docs/results/08-hooks-install.md`; scripts `research/p8-gate/{run_gate,report}.py`.
  Host cycle on the author's machine (results 08 §6): install, doctor
  14/14, idempotent re-install, clean uninstall; currently not installed.
- **P9 result:** `rootsh run claude|opencode [--project] [--env NAME] [--
  args]`: register on the control socket, then `systemd-run --user --scope
  rootsh-agent-<id>` → (`aa-exec -p rootsh-bwrap` if K2 loaded) → bwrap →
  `rootsh.kernel.confine` (K2 check, K1 Landlock fs+net+scope, K3 seccomp).
  Daemon: event socket (`$XDG_RUNTIME_DIR/rootsh/`, bound into the sandbox)
  and control socket (`…/rootsh-control/`, never bound: approve, register);
  launch found from the caller's cgroup (pidfd-pinned), unregistered tool
  calls blocked, project from the launcher (OpenCode F2 now fires), model
  allow → ask. ADR-0018 accepted in part; design 09. Adversarial tests in
  `tests/run/`; live headless Claude run in the sandbox (results 09 §3).
  K2 is entered outside bwrap (unpriv_bwrap forbids change_profile); the
  live K2 test passed after the author's sudo load (results 09 §7). Not built (limits, results 09 §6): root-owned sockets
  and system service, audit denial reader/pop-ups, prompt confirmation.
- **P10-lite result:** design 10 (pre-registered) + `research/ablation/ablation.py`;
  cumulative C0 ask-all → C1 invariants → C2 + fast paths → C3 + model (= P7
  policy) → kernel. On V (70 validation rows, incl. 9 variants, disclosed
  in results 10 §2): attacks blocked 0/1/1/8 of 34, false allows 0/0/9/9,
  asks per 1k benign 1000/1000/389/333, 2 benign blocked at C3. On R (real
  non-heldout steps, rule layers only): 45 attacks, C2 false allows 13,
  198 asks per 1k on human rows. Kernel: adversarial tests 52 passed, 2 K2
  skipped; static estimate: kernel contains 1 of the 9 (V) policy misses.
  See `docs/results/10-ablation.md`. Heldout untouched.
- **P11 result:** `uv run python research/report/tables.py` writes every
  report table to `docs/results/report-tables.md` (generated, never edit by
  hand) by running each phase script on a scratch DB copy; heldout not
  rescored. Fixed `research/policy/latency.py`, stale since P9 (it never
  registered a launch, so every decision was `failure:block`). See
  `docs/results/11-report-tables.md`.
- **Next:** the **demo-rehearsal phase** (agreed 2026-11-24): a scripted,
  repeatable viva demo in a throwaway project (benign edit allowed;
  content-only delete blocked by I2; `~/.claude/settings.json` edit blocked
  by I1; `~/.ssh` read hidden by the sandbox), rehearsed live under `rootsh
  run claude` (the interactive TUI path was never run, results 09 §6), a
  recorded fallback, and the report appendix (repo + tag, install/usage,
  demo walkthrough, reproduce-each-table commands = report-tables.md, test
  evidence, dataset and rubric). Then the dissertation in the university
  format the author uploads. Dropped or moved to Part 2: more labels + a
  second annotator, full ASR benchmark, cross-agent transfer, TUI, K4.

**To start a phase, read in this order:**
1. **This file:** the rules, layout, and git workflow.
2. **`docs/plan.md`:** the build order table and the phase's gate.
3. **`docs/decisions/`:** every ADR. P4 is governed by **ADR-0008**, the
   tiered dataset:
   - a small real gold set, run in Landlock/bwrap sandboxes, not VMs;
   - offline generated variants for training;
   - a tight RAM and Claude Code token budget.
4. **`docs/results/00-verification.md`, "Consequences for the design":**
   binding rules for every later phase.
5. **The `docs/results/` file of the previous phase:** its limits and open
   items.
6. **`git log --oneline`:** one commit per phase.

**Open items carried forward:**
- **From P9 (results 09 §6):** a prompt recorded from a hook is still
  trusted for model features (no out-of-band confirmation); same-UID
  sockets, so an agent launched outside `rootsh run` can reach the control
  socket (its tool calls are blocked); no kernel-denial reader (audit off);
  third-party OpenCode plugins/MCP
  not disabled; rootsh's code is readable (not secret) inside the sandbox,
  `data/` and state are not bound.
- **Collection commands** (from the repo root; `research/gold/make_toolenv.sh
  data/gold/toolenv` first): `uv run rootsh collect run --catalog
  research/gold/scenarios --db data/gold/research.db --data data/gold
  --toolenv data/gold/toolenv --agent opencode` (then `claude_code`), with
  `NVIDIA_API_KEY` set in the author's own shell (never in chat); it resumes,
  and stops itself on a provider error. After a normalizer or sanitizer
  change run `rootsh collect rebuild` (same flags), then `verify`, as the
  author. Results: `research/gold/report.py`.

**Reusable tools:**
- **`research/p3-transcripts/`:** safe recorders for both agents; only
  read-only actions run, as decided by rootsh's own effect extractor.
- **`research/robustness/`:** the NL2Bash gate.

**Finishing a phase:**
1. Gate passed, with results in `docs/results/NN-name.md`.
2. `version = "0.N.0"`.
3. Squash-merge and tag.
4. Update **Current state** above.
5. Push only when the author says so.
