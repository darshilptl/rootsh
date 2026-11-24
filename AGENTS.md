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

1. **Docs first.** Write the Design part of `docs/phases/NN-name.md` (flows,
   interfaces, edge cases, acceptance criteria) and commit it before code.
   Self-audit against it, then add the Results part, every number from a run.
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

## 6. Layout (ADR-0019)

```
rootsh/
├── AGENTS.md  README.md  pyproject.toml  uv.lock
├── src/rootsh/
│   ├── analysis/     what an action does: events, normalize, effects, provenance, hoststate, paths
│   ├── decision/     allow/ask/block: policy.py, model/ (read + score a bundle), notify/
│   ├── enforcement/  making it stick: daemon/, hooks/, run/, kernel/, install/
│   ├── research/     offline only: collect/, labels/, training/
│   ├── storage/      the runtime and research SQLite databases
│   └── cli.py        the `rootsh` command
├── tests/            mirrors src/rootsh/ groups; support/, fixtures/, test_layers.py
├── docs/             README.md is the index: phases/, decisions/, report-tables.md, plan.md, rubric.md
├── research/         flat scripts behind every report table + scenarios/
└── data/             git-ignored research data; data/README.md says what each item is
```

Layer rule: `analysis`, `decision`, `enforcement` and `storage` never import
`research` at run time (`tests/test_layers.py`). A file stays in the tree
only if it runs the product, reproduces a report number, or is cited.

Checks (all must pass before a merge): `uv run ruff check`,
`uv run ruff format --check`, `uv run mypy`, `uv run pytest`, and for a
change that can touch numbers, `uv run python research/report_tables.py`
(all scripts exit 0, §1–7 unchanged unless the change is meant to move them).

## 7. Session handoff (read this first in a new session)

Work runs **one session per phase**; a new session starts from a short
prompt ("start P13") and orients itself from the repository.

**Current state:** P0–P12 done, squash-merged on `main`, tagged
`v0.0.0`–`v0.12.0`. Pushed up to v0.11.0; P12 not pushed yet. Each phase's
result is the Results part of `docs/phases/NN-name.md`; every number is in
`docs/report-tables.md`.

**Next:** the demo-rehearsal phase (agreed 2026-11-24):
- a scripted, repeatable viva demo in a throwaway project:
  - a benign edit is allowed;
  - a content-only delete is blocked by I2;
  - a `~/.claude/settings.json` edit is blocked by I1;
  - a `~/.ssh` read is hidden by the sandbox;
- rehearsed live under `rootsh run claude`; the interactive TUI path was
  never run (docs/phases/09 Results §6);
- a recorded fallback;
- the report appendix: repo + tag, install/usage, demo walkthrough,
  reproduce-each-table (= report-tables.md), test evidence, dataset and
  rubric.

Then the dissertation, in the university format the author uploads.

**To start a phase, read:**
1. this file;
2. `docs/README.md`, the index;
3. the ADRs it links;
4. the previous phase's `docs/phases/` file, for its limits;
5. `git log --oneline`.

**Open items carried forward (docs/phases/09 Results §6):**
- a prompt recorded from a hook is still trusted for model features;
- the sockets are same-UID, so an agent launched outside `rootsh run` can
  reach the control socket, though its tool calls are blocked;
- there is no kernel-denial reader;
- third-party OpenCode plugins and MCP servers are not disabled;
- rootsh's code is readable (not secret) inside the sandbox.

Moved to Part 2:
- more labels and a second annotator;
- the full ASR benchmark;
- cross-agent transfer;
- the TUI;
- K4.

**Collection** (author only, with `NVIDIA_API_KEY` set in their own shell,
never in chat):
1. `research/make_toolenv.sh data/toolenv`.
2. `uv run rootsh collect run --catalog research/scenarios --db
   data/research.db --data data --toolenv data/toolenv --agent opencode`,
   then the same with `--agent claude_code`. It resumes, and stops on a
   provider error.
3. After a normalizer change: `rootsh collect rebuild`, then `verify`.

**Finishing a phase:**
1. Gate passed, with results in `docs/phases/NN-name.md`.
2. `version = "0.N.0"`.
3. Squash-merge and tag.
4. Update **Current state** above.
5. Push only when the author says so.
