# P6 results: monotone LightGBM, calibration, conformal decisions, baselines

Phase 6 of `docs/plan.md`. Design: `docs/design/06-model.md` (committed
before any fit, f513e2b). Decision: ADR-0015. Every number below is from
`model_runs` in `data/gold/research.db`, printed by
`research/model/report.py` on 2026-11-20 [RUN]; raw outputs are
`data/gold/model-train-p6.txt`, `data/gold/model-heldout-p6.txt` and
`data/gold/model-report-p6.txt`.

## 1. What ran

- `rootsh model train` rebuilt the dataset from the DB, checked it hashes to
  manifest `a357bba6…`, fitted the five models of design 06 §3, and wrote
  bundle `ca33283a3dde` (`data/models/p6-a357bba6c02c/`, git-ignored). A dry
  run on a copy of the DB gave the same bundle hash: training is
  deterministic.
- `rootsh model evaluate` checked the bundle hashes and the sealed heldout
  file, then evaluated heldout **once**. A second call was refused
  (`heldout already evaluated for action, full, impact, provenance, rules`,
  exit 1); the DB's UNIQUE (model_sha256, split) key also refuses it
  (test `test_heldout_is_evaluated_once`).
- Nothing was tuned. No change was made between the first validation run
  and the heldout run.

## 2. Tables (generated)


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

## 3. Reading the results

- **Ranking is weak.** On the 162 human-labelled heldout steps the full
  monotone model reaches AUC 0.631 (trace-bootstrap 95% 0.538 to 0.711).
  The impact-only model (0.648) and the rules-only baseline (0.612) are
  within its interval; the action-only text baseline is 0.592. The
  provenance-only model is constant (AUC 0.500): the 74 training rows hold
  only 3 refusals with a content-only argument, so with 3 rows per leaf it
  never splits.
- **Provenance carries signal the training set cannot teach.** On
  validation, a content-only argument appears in 15 of 34 refusals and 3 of
  36 authorized steps (feature counts, `rootsh.model.features` on the
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

## 4. Gate (plan.md P6)

| # | criterion | status |
|---|---|---|
| 1 | manifest and sealed file verified before any fit | pass (`rootsh.model.data.load`) |
| 2 | unit tests: features, monotonicity, isotonic, conformal, once-only guard, bundle hashes | pass (13 tests in `tests/model`) |
| 3 | one heldout row per model in the research DB | pass (5 rows, one per model) |
| 4 | results generated from `model_runs` by script | pass (`research/model/report.py`) |
| 5 | ruff, format, mypy strict, pytest | pass (454 tests) |

## 5. Limits

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
