# 06 — Model: monotone LightGBM, calibration, conformal decisions, baselines

**Phase:** P6 · **Branch:** `feature/p6-model`
**Gate (plan.md):** heldout evaluated once and logged; ablations stored.
**Governing decisions:** ADR-0014 (human-only training), ADR-0015 (this
phase's pre-registered choices).

Everything in §2–§6 is fixed **before** any model is fitted; nothing is tuned
on validation or heldout. The training set is tiny (74 rows, 14 refusals), so
the design favours few, explainable features and fixed hyper-parameters over
search.

## 1. Data

- **Input:** the frozen manifest `a357bba6…` and its sealed heldout file.
  `rootsh model` rebuilds the dataset from the research DB with the P5 code
  and refuses to run unless the rebuilt manifest hashes to the frozen hash
  and the rebuilt heldout bytes equal the sealed file.
- **Target:** `y = 1` when the step is **not authorized** (risk).
- **Train:** train split, sources in `TRAINING_SOURCES` (human, adjudicated,
  G4): 74 rows. **Validation:** the same sources in the validation split
  (dotfiles): 70 rows. R1 rows never train (ADR-0014); they are reported.
- **Heldout:** service + docsite, 435 rows: 162 human/adjudicated (both
  classes) and 273 R1 (all authorized), reported separately.

## 2. Features (one vector per step; deterministic, no LLM)

Path facts use the P2 sensitivity classes on the action's own targets; no
filesystem or git probes (ADR-0015 item 1). Every feature has a declared
monotone direction: `+` means risk may only rise with it.

| feature | dir | definition |
|---|---|---|
| `impact` | + | max over effects: read 0; write create/append/modify 1; write truncate/discard-changes/config, move, permission, exec, process 2; delete, privilege, network other than fetch 3 |
| `destructive` | + | count of delete, move, truncate and discard-changes effects (cap 10) |
| `recursive` | + | 1 if a delete or move is recursive or globbed |
| `opaque` | + | count of opaque reasons (cap 3) |
| `sensitive_write` | + | distinct sensitivity classes among non-read targets |
| `sensitive_read` | + | distinct sensitivity classes among read targets |
| `outside` | + | distinct path targets outside `/work` and `/tmp` |
| `fabricated` | + | non-read arguments with no provenance |
| `content_only` | + | non-read arguments whose only origin is tool/file content |
| `user_backed` | − | share of non-read arguments with a user origin (0 if none) |

Groups for ablation: **impact** = the first seven; **provenance** = the last
three.

## 3. Models (all fixed, seeded, single-threaded)

| name | what |
|---|---|
| `full` | LightGBM, all features, monotone constraints (`advanced`) |
| `impact` | LightGBM, impact group only, monotone |
| `provenance` | LightGBM, provenance group only, monotone |
| `action` | logistic regression on hashed action tokens (the P5 action-only view) |
| `rules` | no training: `impact + min(sensitive_write + sensitive_read, 1) + min(opaque, 1) + content_only` |

LightGBM: binary objective, 100 trees, learning rate 0.05, 4 leaves,
min 3 rows per leaf, no bagging, `deterministic`, seed 20261120. Stored as
LightGBM's native text model plus SHA-256 (no pickle; plan B3).

## 4. Calibration

Isotonic regression (increasing, clipped) fitted on **out-of-fold** train
scores, leaving one repository out; the final model is refitted on all train
rows. The fit is stored as breakpoints and applied by linear interpolation,
so runtime needs no scikit-learn. `rules` and `action` are calibrated the
same way.

## 5. Conformal decisions

Class-conditional (Mondrian) split conformal on the **validation** set, with
calibrated `p = P(unauthorized)` and nonconformity `1 − p` for class 1,
`p` for class 0. **α = 0.10** per class, chosen here, not on data. For a
class with n calibration rows the threshold is the
⌈(n+1)(1−α)⌉-th smallest score (∞ if that exceeds n). Prediction set →
decision: {authorized} allow, {unauthorized} block, both or empty ask.
Guarantee (exchangeability assumed): P(allow | unauthorized) ≤ α.

## 6. Evaluation and logging

- `rootsh model train` fits, calibrates and conformalizes all five models,
  writes the bundle (`bundle.json` with every file's SHA-256), and logs
  validation metrics to `model_runs`.
- `rootsh model evaluate` verifies the bundle hashes, refuses if any of its
  models already has a heldout row (UNIQUE on model hash and split), reads
  the sealed file, and logs heldout metrics. It runs **once**.
- **Metrics** per model and subset (human heldout; R1 heldout):
  AUC with a 95% trace-cluster bootstrap interval (2,000 resamples, seeded);
  allow/ask/block counts per true class; false-allow rate on unauthorized
  steps (Wilson 95%); class-conditional coverage; asks and blocks per 1,000
  authorized steps.

## 7. Edge cases

- A subset with one class: AUC is `null`, never a number.
- Conformal class with too few rows for α: threshold ∞, so that class is
  always in the set (more asks, never a false allow).
- Bundle file changed after training: evaluate refuses (hash mismatch).
- Dataset drift (manifest hash differs): train and evaluate refuse.

## 8. Acceptance

1. Manifest and sealed file verified before any fit [RUN].
2. Unit tests: features on hand-built steps, monotonicity of the fitted
   model (raising a `+` feature never lowers the score), isotonic and
   conformal functions, the once-only heldout guard, bundle hash check.
3. One heldout evaluation row per model in the real research DB.
4. Results doc with every number from `model_runs` via a script.
5. ruff, format, mypy strict, pytest green.
