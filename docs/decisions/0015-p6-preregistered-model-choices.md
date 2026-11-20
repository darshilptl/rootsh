# ADR-0015: Pre-registered model choices for P6

**Status:** Accepted · 2026-11-20 (author delegated P6 choices to Claude's
recommendation under the submission deadline) · Applies plan B5 step 4.

## Context

The frozen dataset (manifest `a357bba6…`, ADR-0014) has 74 training rows
with 14 refusals and 70 validation rows, all from one validation repository.
Heldout has 162 human-labelled steps and 273 R1 steps. With this little data
any search over features or hyper-parameters on validation would overfit it,
and validation is also the conformal calibration set. Time left before
submission is hours.

## Decision

1. **No filesystem or git host-state probes as features.** The gold
   workspaces are not rebuilt in P6; path features come from the P2
   sensitivity classes on the action's own targets. Host-state probes stay
   a P7 runtime item (already open: which process runs them).
2. **Features, monotone directions, hyper-parameters, α = 0.10 and the
   baselines are fixed in design 06 before fitting.** Nothing is tuned.
3. **Isotonic calibration on out-of-fold train scores** (leave one
   repository out) so validation is used once, for conformal thresholds.
4. **Class-conditional conformal**, so the stated guarantee is on the
   false-allow rate of unauthorized steps, the error that causes harm.
5. **LightGBM native text model + SHA-256**, no pickle (plan B3).
6. **Heldout is evaluated once per bundle**, enforced by a UNIQUE key in
   the research DB, not by convention.

## Consequences

- Results carry wide intervals and are reported as such; a weak model is a
  finding, not a reason to tune.
- Validation is a single repository, so the conformal exchangeability
  assumption with heldout repositories is not guaranteed; empirical
  coverage on heldout is reported as the check.
