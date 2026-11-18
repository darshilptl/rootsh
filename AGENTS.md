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
