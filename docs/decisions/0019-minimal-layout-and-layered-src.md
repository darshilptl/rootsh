# ADR-0019: Minimal layout and a layered `src/`

**Status:** Accepted · 2026-11-24 (author) · Supersedes ADR-0009

## Context

By v0.11.0 the repository had 7 nested folders under `docs/`, 14 under
`research/`, a nested git-ignored `data/gold/`, and 19 flat packages under
`src/rootsh/`, so runtime and research code were mixed (the daemon's model
code imported the labelling package). The author asked for a layout they can
explore alone, where every file earns its place.

## Decision

1. **Keep a file only if** it runs the product, reproduces a report number, or
   the dissertation cites it. Everything removed from the tree stays readable
   at tag `v0.11.0` (`git show v0.11.0:<path>`); docs cite it that way.
2. **`docs/`**: `README.md` (index), `plan.md`, `rubric.md`,
   `report-tables.md` (generated), `decisions/`, `phases/` (one file per
   phase: the design part, then the results part, text unchanged).
3. **`research/`**: one flat folder of scripts, each producing a report table
   or the dataset, plus `scenarios/`; `report_tables.py` runs them all.
4. **`data/`** (git-ignored except `README.md`): `research.db`, `freeze/`,
   `traces/`, `outputs/`, `model/`, `gate/`, `nl2bash/`.
5. **`src/rootsh/`** in four groups by what the code does:
   - `analysis/`: events, normalize, effects, provenance, hoststate, paths
     (what an action does and where its arguments came from);
   - `decision/`: policy, model (runtime read and score), notify;
   - `enforcement/`: daemon, hooks, run, kernel, install;
   - `research/`: collect, labels, training (offline only);
   - plus `storage/` and `cli.py`.
   `tests/` mirrors the groups.
6. **Layer rule**: `analysis`, `decision`, `enforcement` and `storage` never
   import `research` at run time (type hints under `TYPE_CHECKING` are
   allowed). `tests/test_layers.py` enforces it.
7. **Naming**: a name says what the code does, not which phase made it
   (`p8-gate/run_gate.py` → `hook_gate.py`); one name per concept
   (`collect/policy.py` → `collect/net_guard.py`); one implementation per
   mechanism (the collection sandbox uses `enforcement/kernel/landlock.py`).

## Consequences

- Module paths changed (`rootsh.hooks.claude_code` →
  `rootsh.enforcement.hooks.claude_code`). An install made before v0.12.0
  must be reinstalled; none is installed now.
- Older ADR and phase texts were edited for paths only; the old → new map
  is `docs/phases/12-cleanup.md` §3.
- No behaviour change: report tables §1–7 are byte-identical, and the DB,
  manifest and bundle hashes are unchanged (docs/phases/12-cleanup.md §4).
