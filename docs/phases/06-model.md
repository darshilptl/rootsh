# P6: Monotone LightGBM, calibration, conformal decisions, baselines

> Merged in v0.12.0 from `docs/design/06-model.md` and `docs/results/06-model.md` (tag v0.11.0). The text is unchanged except heading levels and paths. "Design NN" and "results NN" in other files mean the two parts below.

## Design

*06 — Model: monotone LightGBM, calibration, conformal decisions, baselines*

**Phase:** P6 · **Branch:** `feature/p6-model`
**Gate (plan.md):** heldout evaluated once and logged; ablations stored.
**Governing decisions:** ADR-0014 (human-only training), ADR-0015 (this
phase's pre-registered choices).

Everything in §2–§6 is fixed **before** any model is fitted; nothing is tuned
on validation or heldout. The training set is tiny (74 rows, 14 refusals), so
the design favours few, explainable features and fixed hyper-parameters over
search.

### 1. Data

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

### 2. Features (one vector per step; deterministic, no LLM)

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

### 3. Models (all fixed, seeded, single-threaded)

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

### 4. Calibration

Isotonic regression (increasing, clipped) fitted on **out-of-fold** train
scores, leaving one repository out; the final model is refitted on all train
rows. The fit is stored as breakpoints and applied by linear interpolation,
so runtime needs no scikit-learn. `rules` and `action` are calibrated the
same way.

### 5. Conformal decisions

Class-conditional (Mondrian) split conformal on the **validation** set, with
calibrated `p = P(unauthorized)` and nonconformity `1 − p` for class 1,
`p` for class 0. **α = 0.10** per class, chosen here, not on data. For a
class with n calibration rows the threshold is the
⌈(n+1)(1−α)⌉-th smallest score (∞ if that exceeds n). Prediction set →
decision: {authorized} allow, {unauthorized} block, both or empty ask.
Guarantee (exchangeability assumed): P(allow | unauthorized) ≤ α.

### 6. Evaluation and logging

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

### 7. Edge cases

- A subset with one class: AUC is `null`, never a number.
- Conformal class with too few rows for α: threshold ∞, so that class is
  always in the set (more asks, never a false allow).
- Bundle file changed after training: evaluate refuses (hash mismatch).
- Dataset drift (manifest hash differs): train and evaluate refuse.

### 8. Acceptance

1. Manifest and sealed file verified before any fit [RUN].
2. Unit tests: features on hand-built steps, monotonicity of the fitted
   model (raising a `+` feature never lowers the score), isotonic and
   conformal functions, the once-only heldout guard, bundle hash check.
3. One heldout evaluation row per model in the real research DB.
4. Results doc with every number from `model_runs` via a script.
5. ruff, format, mypy strict, pytest green.

## Results

*P6 results: monotone LightGBM, calibration, conformal decisions, baselines*

Phase 6 of `docs/plan.md`. Design: `docs/phases/06-model.md` (committed
before any fit, f513e2b). Decision: ADR-0015. Every number below is from
`model_runs` in `data/research.db`, printed by
`research/model_report.py` on 2026-11-20 [RUN]; the raw first-run
outputs are kept verbatim in §"Raw first-run output" at the end of this file
(moved there in v0.12.0 from git-ignored `data/model-*-p6.txt`).

### 1. What ran

- `rootsh model train` rebuilt the dataset from the DB, checked it hashes to
  manifest `a357bba6…`, fitted the five models of design 06 §3, and wrote
  bundle `ca33283a3dde` (`data/model/`, git-ignored). A dry
  run on a copy of the DB gave the same bundle hash: training is
  deterministic.
- `rootsh model evaluate` checked the bundle hashes and the sealed heldout
  file, then evaluated heldout **once**. A second call was refused
  (`heldout already evaluated for action, full, impact, provenance, rules`,
  exit 1); the DB's UNIQUE (model_sha256, split) key also refuses it
  (test `test_heldout_is_evaluated_once`).
- Nothing was tuned. No change was made between the first validation run
  and the heldout run.

### 2. Tables (generated)


#### validation / validation

| model | n | auc | auc 95% (trace bootstrap) | allow ok/bad | ask ok/bad | block ok/bad | false-allow (Wilson 95%) | asks per 1k ok | blocks per 1k ok |
|---|---|---|---|---|---|---|---|---|---|
| full | 70 | 0.642 | 0.492 to 0.790 | 0/0 | 34/26 | 2/8 | 0.000 (0.000 to 0.102) | 944.444 | 55.556 |
| impact | 70 | 0.507 | 0.349 to 0.706 | 0/0 | 34/32 | 2/2 | 0.000 (0.000 to 0.102) | 944.444 | 55.556 |
| provenance | 70 | 0.500 | 0.500 to 0.500 | 0/0 | 36/34 | 0/0 | 0.000 (0.000 to 0.102) | 1000.000 | 0.000 |
| action | 70 | 0.415 | 0.259 to 0.641 | 0/0 | 36/34 | 0/0 | 0.000 (0.000 to 0.102) | 1000.000 | 0.000 |
| rules | 70 | 0.688 | 0.554 to 0.789 | 0/0 | 36/30 | 0/4 | 0.000 (0.000 to 0.102) | 1000.000 | 0.000 |

#### heldout / human

| model | n | auc | auc 95% (trace bootstrap) | allow ok/bad | ask ok/bad | block ok/bad | false-allow (Wilson 95%) | asks per 1k ok | blocks per 1k ok |
|---|---|---|---|---|---|---|---|---|---|
| full | 162 | 0.631 | 0.538 to 0.711 | 0/0 | 72/63 | 5/22 | 0.000 (0.000 to 0.043) | 935.065 | 64.935 |
| impact | 162 | 0.648 | 0.556 to 0.716 | 0/0 | 70/73 | 7/12 | 0.000 (0.000 to 0.043) | 909.091 | 90.909 |
| provenance | 162 | 0.500 | 0.500 to 0.500 | 0/0 | 77/85 | 0/0 | 0.000 (0.000 to 0.043) | 1000.000 | 0.000 |
| action | 162 | 0.592 | 0.501 to 0.676 | 0/0 | 77/85 | 0/0 | 0.000 (0.000 to 0.043) | 1000.000 | 0.000 |
| rules | 162 | 0.612 | 0.518 to 0.722 | 0/0 | 63/58 | 14/27 | 0.000 (0.000 to 0.043) | 818.182 | 181.818 |

#### heldout / rule

| model | n | auc | auc 95% (trace bootstrap) | allow ok/bad | ask ok/bad | block ok/bad | false-allow (Wilson 95%) | asks per 1k ok | blocks per 1k ok |
|---|---|---|---|---|---|---|---|---|---|
| full | 273 | n/a | n/a | 0/0 | 273/0 | 0/0 | n/a (n/a) | 1000.000 | 0.000 |
| impact | 273 | n/a | n/a | 0/0 | 273/0 | 0/0 | n/a (n/a) | 1000.000 | 0.000 |
| provenance | 273 | n/a | n/a | 0/0 | 273/0 | 0/0 | n/a (n/a) | 1000.000 | 0.000 |
| action | 273 | n/a | n/a | 0/0 | 273/0 | 0/0 | n/a (n/a) | 1000.000 | 0.000 |
| rules | 273 | n/a | n/a | 0/0 | 273/0 | 0/0 | n/a (n/a) | 1000.000 | 0.000 |

#### calibration range and conformal thresholds (bundle.json)

| model | calibrated p range | t_authorized (on p) | t_unauthorized (on 1-p) | allow needs p < | model sha256 |
|---|---|---|---|---|---|
| full | 0.134 to 1.000 | 0.667 | 0.866 | 0.134 | 9d317325fd7b |
| impact | 0.134 to 0.800 | 0.799 | 0.866 | 0.134 | 147ac687cd07 |
| provenance | 0.189 to 0.189 | 0.189 | 0.811 | 0.189 | 0859b86f467b |
| action | 0.125 to 0.500 | 0.500 | 0.875 | 0.125 | 4a1599dae864 |
| rules | 0.077 to 1.000 | 0.385 | 0.923 | 0.077 | 46f9fa35c372 |

### 3. Reading the results

- **Ranking is weak.** On the 162 human-labelled heldout steps the full
  monotone model reaches AUC 0.631 (trace-bootstrap 95% 0.538 to 0.711).
  The impact-only model (0.648) and the rules-only baseline (0.612) are
  within its interval; the action-only text baseline is 0.592. The
  provenance-only model is constant (AUC 0.500): the 74 training rows hold
  only 3 refusals with a content-only argument, so with 3 rows per leaf it
  never splits.
- **Provenance carries signal the training set cannot teach.** On
  validation, a content-only argument appears in 15 of 34 refusals and 3 of
  36 authorized steps (feature counts, `rootsh.decision.model.features` on the
  frozen rows) [RUN]. Training has 3 of 14 and 0 of 60. The bottleneck is
  the human-labelled training set (P5 §8), not the feature.
- **Conformal decisions never allow.** With alpha 0.10 per class, a step is
  allowed only if its calibrated risk is strictly below 1 − t_unauthorized
  (0.134 for `full`), and for all five models that cut-off equals the lowest
  value the calibration produces (to float precision) [RUN]: the
  lowest-score bin holds enough validation refusals that excluding
  "unauthorized" there would break the 10% false-allow bound. So every
  authorized step is asked or blocked: **0 false allows** on heldout
  (Wilson 95% upper bound 0.043, n = 85), at the cost of **935 asks and
  65 blocks per 1,000 authorized human-labelled steps** for `full`, and an
  ask on all 273 R1 read steps. That burden is not usable as a deployed
  policy; it is what the guarantee costs with this little data.
- **Blocks are where the models differ.** `full` blocks 22 of 85 refusals
  and 5 of 77 authorized steps; `rules` blocks 27 and 14; `impact` 12 and 7.

### 4. Gate (plan.md P6)

| # | criterion | status |
|---|---|---|
| 1 | manifest and sealed file verified before any fit | pass (`rootsh.research.training.data.load`) |
| 2 | unit tests: features, monotonicity, isotonic, conformal, once-only guard, bundle hashes | pass (13 tests in `tests/decision/model`) |
| 3 | one heldout row per model in the research DB | pass (5 rows, one per model) |
| 4 | results generated from `model_runs` by script | pass (`research/model_report.py`) |
| 5 | ruff, format, mypy strict, pytest | pass (454 tests) |

### 5. Limits

- **Data, not method, limits the result.** 74 training rows (14 refusals)
  and 70 validation rows from one repository. Intervals are wide and the
  provenance model could not learn.
- **Validation is one repository** (dotfiles) and is also the conformal set;
  exchangeability with the heldout repositories is assumed, not shown.
  Empirical false-allow on heldout (0 of 85) is consistent with the bound
  but is reached by never allowing.
- **No filesystem or git probes** in the features (ADR-0015 item 1); P7
  adds them at runtime.
- **R1 heldout steps are all authorized**, so only burden (asks, blocks) is
  measurable on them, not AUC.
- **For P7:** the policy needs more than the model: the plan's invariants
  (defense-tamper, untrusted provenance + severity 3 → block) and a
  read-only fast path are what make the ask rate usable; the model alone
  cannot, at this data size.

### Raw first-run output

Copied byte for byte in v0.12.0 from the git-ignored files written on 2026-11-20.

#### `model-train-p6.txt`

```text
bundle ca33283a3dde in data/model
full        validation n=70   auc=0.641748366013072 false_allow=0.0 authorized:allow=0 authorized:ask=34 authorized:block=2 unauthorized:allow=0 unauthorized:ask=26 unauthorized:block=8
impact      validation n=70   auc=0.5065359477124183 false_allow=0.0 authorized:allow=0 authorized:ask=34 authorized:block=2 unauthorized:allow=0 unauthorized:ask=32 unauthorized:block=2
provenance  validation n=70   auc=0.5 false_allow=0.0 authorized:allow=0 authorized:ask=36 authorized:block=0 unauthorized:allow=0 unauthorized:ask=34 unauthorized:block=0
action      validation n=70   auc=0.4150326797385621 false_allow=0.0 authorized:allow=0 authorized:ask=36 authorized:block=0 unauthorized:allow=0 unauthorized:ask=34 unauthorized:block=0
rules       validation n=70   auc=0.6875 false_allow=0.0 authorized:allow=0 authorized:ask=36 authorized:block=0 unauthorized:allow=0 unauthorized:ask=30 unauthorized:block=4
```

#### `model-heldout-p6.txt`

```text
heldout evaluated once for bundle ca33283a3dde
full        human      n=162  auc=0.6314744079449962 false_allow=0.0 authorized:allow=0 authorized:ask=72 authorized:block=5 unauthorized:allow=0 unauthorized:ask=63 unauthorized:block=22
full        rule       n=273  auc=None false_allow=None authorized:allow=0 authorized:ask=273 authorized:block=0 unauthorized:allow=0 unauthorized:ask=0 unauthorized:block=0
impact      human      n=162  auc=0.6481283422459893 false_allow=0.0 authorized:allow=0 authorized:ask=70 authorized:block=7 unauthorized:allow=0 unauthorized:ask=73 unauthorized:block=12
impact      rule       n=273  auc=None false_allow=None authorized:allow=0 authorized:ask=273 authorized:block=0 unauthorized:allow=0 unauthorized:ask=0 unauthorized:block=0
provenance  human      n=162  auc=0.5 false_allow=0.0 authorized:allow=0 authorized:ask=77 authorized:block=0 unauthorized:allow=0 unauthorized:ask=85 unauthorized:block=0
provenance  rule       n=273  auc=None false_allow=None authorized:allow=0 authorized:ask=273 authorized:block=0 unauthorized:allow=0 unauthorized:ask=0 unauthorized:block=0
action      human      n=162  auc=0.5922077922077922 false_allow=0.0 authorized:allow=0 authorized:ask=77 authorized:block=0 unauthorized:allow=0 unauthorized:ask=85 unauthorized:block=0
action      rule       n=273  auc=None false_allow=None authorized:allow=0 authorized:ask=273 authorized:block=0 unauthorized:allow=0 unauthorized:ask=0 unauthorized:block=0
rules       human      n=162  auc=0.6124522536287242 false_allow=0.0 authorized:allow=0 authorized:ask=63 authorized:block=14 unauthorized:allow=0 unauthorized:ask=58 unauthorized:block=27
rules       rule       n=273  auc=None false_allow=None authorized:allow=0 authorized:ask=273 authorized:block=0 unauthorized:allow=0 unauthorized:ask=0 unauthorized:block=0
```

#### `model-report-p6.txt`

```text
bundles: ca33283a3dde; manifests: a357bba6c02c; rows: 10
heldout rows per model: {'action': 1, 'full': 1, 'impact': 1, 'provenance': 1, 'rules': 1}

### validation / validation

| model | n | auc | auc 95% (trace bootstrap) | allow ok/bad | ask ok/bad | block ok/bad | false-allow (Wilson 95%) | asks per 1k ok | blocks per 1k ok |
|---|---|---|---|---|---|---|---|---|---|
| full | 70 | 0.642 | 0.492 to 0.790 | 0/0 | 34/26 | 2/8 | 0.000 (0.000 to 0.102) | 944.444 | 55.556 |
| impact | 70 | 0.507 | 0.349 to 0.706 | 0/0 | 34/32 | 2/2 | 0.000 (0.000 to 0.102) | 944.444 | 55.556 |
| provenance | 70 | 0.500 | 0.500 to 0.500 | 0/0 | 36/34 | 0/0 | 0.000 (0.000 to 0.102) | 1000.000 | 0.000 |
| action | 70 | 0.415 | 0.259 to 0.641 | 0/0 | 36/34 | 0/0 | 0.000 (0.000 to 0.102) | 1000.000 | 0.000 |
| rules | 70 | 0.688 | 0.554 to 0.789 | 0/0 | 36/30 | 0/4 | 0.000 (0.000 to 0.102) | 1000.000 | 0.000 |

### heldout / human

| model | n | auc | auc 95% (trace bootstrap) | allow ok/bad | ask ok/bad | block ok/bad | false-allow (Wilson 95%) | asks per 1k ok | blocks per 1k ok |
|---|---|---|---|---|---|---|---|---|---|
| full | 162 | 0.631 | 0.538 to 0.711 | 0/0 | 72/63 | 5/22 | 0.000 (0.000 to 0.043) | 935.065 | 64.935 |
| impact | 162 | 0.648 | 0.556 to 0.716 | 0/0 | 70/73 | 7/12 | 0.000 (0.000 to 0.043) | 909.091 | 90.909 |
| provenance | 162 | 0.500 | 0.500 to 0.500 | 0/0 | 77/85 | 0/0 | 0.000 (0.000 to 0.043) | 1000.000 | 0.000 |
| action | 162 | 0.592 | 0.501 to 0.676 | 0/0 | 77/85 | 0/0 | 0.000 (0.000 to 0.043) | 1000.000 | 0.000 |
| rules | 162 | 0.612 | 0.518 to 0.722 | 0/0 | 63/58 | 14/27 | 0.000 (0.000 to 0.043) | 818.182 | 181.818 |

### heldout / rule

| model | n | auc | auc 95% (trace bootstrap) | allow ok/bad | ask ok/bad | block ok/bad | false-allow (Wilson 95%) | asks per 1k ok | blocks per 1k ok |
|---|---|---|---|---|---|---|---|---|---|
| full | 273 | n/a | n/a | 0/0 | 273/0 | 0/0 | n/a (n/a) | 1000.000 | 0.000 |
| impact | 273 | n/a | n/a | 0/0 | 273/0 | 0/0 | n/a (n/a) | 1000.000 | 0.000 |
| provenance | 273 | n/a | n/a | 0/0 | 273/0 | 0/0 | n/a (n/a) | 1000.000 | 0.000 |
| action | 273 | n/a | n/a | 0/0 | 273/0 | 0/0 | n/a (n/a) | 1000.000 | 0.000 |
| rules | 273 | n/a | n/a | 0/0 | 273/0 | 0/0 | n/a (n/a) | 1000.000 | 0.000 |

### calibration range and conformal thresholds (bundle.json)

| model | calibrated p range | t_authorized (on p) | t_unauthorized (on 1-p) | allow needs p < | model sha256 |
|---|---|---|---|---|---|
| full | 0.134 to 1.000 | 0.667 | 0.866 | 0.134 | 9d317325fd7b |
| impact | 0.134 to 0.800 | 0.799 | 0.866 | 0.134 | 147ac687cd07 |
| provenance | 0.189 to 0.189 | 0.189 | 0.811 | 0.189 | 0859b86f467b |
| action | 0.125 to 0.500 | 0.500 | 0.875 | 0.125 | 4a1599dae864 |
| rules | 0.077 to 1.000 | 0.385 | 0.923 | 0.077 | 46f9fa35c372 |
```
