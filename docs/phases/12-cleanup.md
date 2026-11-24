# P12: Audit, minimal layout, layered src

Added after P11 at the author's request, before the demo rehearsal. The
decision is ADR-0019. This phase has no design part: the author approved the
plan in chat on 2026-11-24, and this file records what was done and checked.

## Results

### 1. The rule

A file stays in the tree only if one of these holds:
1. it runs the product;
2. a report number is reproduced from it;
3. the dissertation cites it.

Tracked files that fail the rule were deleted and remain at tag `v0.11.0`.
Untracked files in `data/` that fail it were deleted for good, after the
author's yes. `data/README.md` lists what stayed and under which rule.

### 2. Audit findings and outcome [RUN unless tagged]

| # | Finding | Method | Outcome |
|---|---|---|---|
| 1 | `README.md` said "Status: P3 complete" | read | fixed |
| 2 | `docs/plan.md` build order had no P10-lite note and no row after P11 | read | P10 note and P12 row added |
| 3 | ADR index: 0003, 0004, 0009 and 0011 shown as plain "Accepted" though later records change them; 0009 put the plugin in `plugins/opencode/` (it is in `enforcement/hooks/`) | read | statuses fixed; 0009 superseded by 0019 |
| 4 | Results 00, 05, 06 and 08 cited git-ignored files (`out/schema.json`, `data/gold/*.txt`, `data/p8/*`) | path-check script | P6 printouts copied into phases/06; P5 printouts confirmed line by line in report-tables §3–4; P0 `out/` marked "never committed"; others cited at `v0.11.0:` |
| 5 | `research/old-audit/` imported `rootsh.model.infer`, which does not exist | read | removed (at v0.11.0; ADR-0005 evidence) |
| 6 | `ablation.py` imported 4 private policy functions | grep | made public: `defense_check`, `untrusted_check`, `read_only_check`, `project_edit_check` |
| 7 | Two Landlock implementations: `collect/confine.py` was a network-only copy of `kernel/landlock.py` | read | merged: `Rules(filesystem=False)`; 167 → 101 lines; sandbox tests pass |
| 8 | Runtime model code imported research packages (`labels`, `collect`, training `data`) | import map | split: `bundle.py` keeps read/verify, `research/training/fit.py` fits/writes; `analysis/paths.py`, `decision/model/tokens.py`; type hints under `TYPE_CHECKING`; enforced by `tests/test_layers.py` |
| 9 | Dead code: `labels.session.main_effect`, `run.sandbox.describe`, `None if False else …` in `variants.py` | vulture 2.14 | removed; the variants ternary always took the `Verdict` branch, so the frozen variants do not change |
| 10 | 21 broad `except` blocks in `src`, 5 swallowing | grep + read | all on the decision path fail closed (block, refuse, or error reply). One soft spot, **not fixed**: `hoststate/fs.py` skips unreadable subfolders when it counts what a delete would remove, so the impact features can undercount there |
| 11 | CLI flags in docs vs the parser | checker script | no drift |
| 12 | Policy order in ADR-0016 vs `decide()` | read | matches. The daemon additionally turns a model `allow` into `ask` (P9); `decide()` and the ablation's C3 do not. No number is affected, because the model never allows (0 allows) |
| 13 | NL2Bash robustness on current code differs from results 02 | rerun vs `v0.11.0:docs/results/data/p2/nl2bash_run2.json` | expected: the effect catalog grew after P2 (e.g. `mount`). Opaque was 14.7% in P2 and is 13.95% now (12,607 commands, 0 crashes). Report-tables §8 shows the current run, labelled as such |

### 3. Old → new paths (from `git diff -M v0.11.0`)

| Old | New |
|---|---|
| `docs/design/NN-x.md` + `docs/results/NN-x.md` | `docs/phases/NN-x.md` (Design, then Results) |
| `docs/results/report-tables.md`, `docs/labelling-rubric.md` | `docs/report-tables.md`, `docs/rubric.md` |
| `docs/results/data/p0,p2,p7,p8/*` | removed (at `v0.11.0:`) |
| `research/gold/{scenarios,make_toolenv.sh,report.py}` | `research/{scenarios,make_toolenv.sh,gold_report.py}` |
| `research/labels/{plan,agreement,audits}.py` | `research/label_{plan,agreement,audits}.py` |
| `research/model/report.py` | `research/model_report.py` |
| `research/policy/{burden,latency}.py` | `research/policy_{burden,latency}.py` |
| `research/ablation/ablation.py`, `research/report/tables.py` | `research/ablation.py`, `research/report_tables.py` |
| `research/p8-gate/{run_gate,report}.py` | `research/hook_gate{,_report}.py` |
| `research/p9/k2_profile.py`, `research/robustness/effects_robustness.py` | `research/k2_profile.py`, `research/effects_robustness.py` |
| `research/{p0-verification,p3-transcripts,old-audit}/`, `research/labels/assistant_round.py` | removed (at `v0.11.0:`) |
| `src/rootsh/{events,normalize,effects,provenance,hoststate}` | `src/rootsh/analysis/…` (`events` became `analysis/events.py`) |
| `src/rootsh/{policy,notify,model}` | `src/rootsh/decision/…` (`policy` became `decision/policy.py`) |
| `src/rootsh/model/{cli,data,evaluate,metrics}.py` | `src/rootsh/research/training/…` (+ `fit.py`) |
| `src/rootsh/{daemon,hooks,run,kernel,install}` | `src/rootsh/enforcement/…` |
| `src/rootsh/{collect,labels}`, `collect/policy.py` | `src/rootsh/research/…`, `research/collect/net_guard.py` |
| `src/rootsh/cli/main.py` | `src/rootsh/cli.py` |
| `tests/<package>/` | `tests/<group>/<package>/` |
| `data/gold/{research.db,freeze,traces,outputs}` | `data/{research.db,freeze,traces,outputs}` |
| `data/models/p6-a357bba6c02c/`, `data/external/nl2bash/`, `research/p8-gate/out/` | `data/model/`, `data/nl2bash/`, `data/gate/` |

Module paths change the same way: `rootsh.hooks.claude_code` became
`rootsh.enforcement.hooks.claude_code`. ADR and phase texts were edited for
paths and module names only.

### 4. Verification [RUN]

- **Hashes unchanged** (SHA-256, before vs after, `sha256sum -c`):
  - `research.db`;
  - both freeze files (manifest `a357bba6c02c…`);
  - the 6 bundle files (`bundle.json` `ca33283a3dde…`).
- **Report tables:** §1–7 of `docs/report-tables.md` are byte-identical to
  v0.11.0's (287 lines, diff empty), including "192 runs verified, 0
  problems". All 10 scripts and the kernel tests exit 0.
- **Scenarios:** all 8 `.toml` files are byte-identical to v0.11.0.
- **Checks:** ruff, ruff format, mypy strict (138 files); pytest 618
  passed, 2 skipped. That is 616 before, plus the 2 layer tests; the 2
  skips are the K2 live tests, which need the profile loaded.

### 5. Mistakes made during the work, with their fixes

1. **The path rewriter edited a scenario.** The rule for `tests/run` also
   matched `tests/run.sh` inside the C project scenario (`cproj.toml`,
   6 lines).
   - pytest did not notice. The P4 verifier did, through the report-tables
     run: 48 problems (24 cproj runs: "scenario changed", "no longer
     rebuilds").
   - Fix: `cproj.toml` restored byte for byte from v0.11.0, and all string
     literals the rewriter touched were re-read. Verify is back to
     0 problems.
2. **The same rewriter produced `v0.11.0:docs/phases/data/…`** for evidence
   that lives at `v0.11.0:docs/results/data/…`. It also prefixed two
   exclude paths in `pyproject.toml`.
   - Fix: corrected, then every `v0.11.0:` path in the docs was checked
     with `git cat-file -e`.
3. **A research/data commit went in with 1 failing test and 4 errors**
   (the scenario catalog path in `tests/research/collect/test_collect.py`).
   The pipe into `tail` hid pytest's exit code.
   - Fix: the next commit on the branch; later runs check the exit code.
4. **A token rename in `labels/audits.py` (`_tokens(` → `tokens(`) also
   renamed `source_tokens` and three other functions.** ruff and mypy caught
   it before any test ran.

### 6. Limits

- A changed scenario file is caught only by `rootsh collect verify` (run
  inside report-tables §1), not by pytest. Run the tables after any change
  near `research/scenarios/`.
- The `hoststate/fs.py` undercount (finding 10) is documented, not fixed:
  fixing it would change runtime features after the model was frozen.
- The collection sandbox now passes Landlock's "log new exec" flag on ABI ≥ 7,
  as the kernel layer already did. This only affects audit logging, and audit
  is off on this host.
- `research/hook_gate.py` runs live agents and was not rerun. Its stored
  results in `data/gate/` regenerate report-tables §9.
