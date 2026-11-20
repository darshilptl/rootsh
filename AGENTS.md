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
├── plugins/opencode/      # TypeScript OpenCode plugin (from P8)
└── data/                  # local corpora and runtime files, git-ignored
```

Checks (all must pass before a merge): `uv run ruff check`,
`uv run ruff format --check`, `uv run mypy`, `uv run pytest`.

## 7. Session handoff (read this first in a new session)

Work runs **one session per phase**. A new session starts from a short prompt
("start P4") and orients itself from the repository, not from chat history.

**Current state:**
- **Done:** P0–P4, squash-merged on `main`, tagged `v0.0.0`–`v0.4.0`.
  P0–P3 are pushed to `origin`; P4 is not pushed until the author says so.
- **P4 result:** gold set of 192 sandboxed runs (176 OpenCode on NVIDIA NIM
  Nemotron, 16 Claude Code Haiku, $0.0432) in `data/gold/` (git-ignored);
  `rootsh collect verify` 0 problems; 628 critical arguments (P3: 4). See
  `docs/results/04-trace-harness.md`.
- **Next:** P5 (labels per step, tier-2 variants, the 13 deferred catalog
  programs). Not started: wait for the author's approval.

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
- **P7:** decide which process runs the host-state probes (the daemon user
  may not be able to read `$HOME`), and measure probe latency on large
  repositories.
- **P8/P9:** agents can read the defense's own plugin in `.opencode/plugins/`
  (seen in P3); protect it.
- **P5:** catalog the 13 programs P4 deferred (`cc`, `sqlite3`, `mkdocs`,
  `getent`, `nginx`, and 8 single calls; results doc §6), from real traces,
  never from NL2Bash. Repeat runs differ (8 of 16), so split by scenario.
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
