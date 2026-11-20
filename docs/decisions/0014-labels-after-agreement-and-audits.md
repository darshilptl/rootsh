# ADR-0014: The labelled dataset after the agreement and R1 audits

**Status:** Accepted · 2026-11-20 (author delegated items 6–7 to Claude's
recommendation) · Applies ADR-0013
item 3; amends design 05 §9 and §10 gates 1–3. Every amendment below was
made **after** the numbers were seen and is reported as such.

## Context

Measured on the gold DB [RUN]:

- **Author vs. assistant, first pass, n = 77:** agreement 0.662, Cohen's
  kappa **0.347** on `authorized` (target ≥ 0.60). Disagreements are
  one-sided: 21 steps the author refused and the assistant allowed, 5 the
  other way.
- **R1 audit (60 steps, R1 v1):** the author refused 15. A second look
  (`rootsh label --adjudicate`, first answer shown) kept 5 refusals and
  turned 10 into yes. One of the 5 read `.git/hooks` and `git config`.
- **R1 v2** leaves reads inside `.git` to a person: 25 steps leave the rule
  (22 new author labels; 3 were audit steps). Under v2 the audit has 57 rule
  steps and **4 disagreements (7.0%)**. The 4 are plain reads of project
  files (`mkdocs.yml`, `orders.py`, `README.md`, a glob) that the author
  refused because of their context; no context-free rule separates them.
- **Variants:** 191 from 131 author-labelled parents (G1 138, G2 40, G3 3,
  G4 10), below the design's "few thousand" target, because only human
  labels may be parents now.

## Decision

1. **Assistant training labels are dropped** (ADR-0013 item 3). The 396
   train steps that only the assistant labelled are **excluded** from the
   dataset, not relabelled. Design 05 §10 gate 1 becomes: every heldout and
   validation step has a human or R1 label; excluded train steps are counted
   in the manifest.
2. **R1 v2 is the rule used.** Gate 2 ("no disagreement") is amended to:
   the audit disagreement is reported with its 95% interval and the four
   refusals listed. Heldout evaluation reports R1-labelled steps separately
   from human-labelled ones.
3. **Gate 3 (test–retest)** is replaced by the author–assistant kappa
   (ADR-0013 item 4); the value is reported even though it fails.
4. **The shortcut gates are two-sided:** `max(AUC, 1 − AUC) ≤ 0.60`. An
   out-of-fold AUC far below 0.5 predicts the label in reverse across
   repositories, which is a shortcut as much as one far above it. This is
   stricter than the design text, never more lenient.
5. **Adjudicated answers** are stored in the unused re-test slot with
   `source = 'adjudicated'`; agreement is always computed on first-pass
   answers.

6. **Training uses human labels only:** `human`, `adjudicated` and the kept
   variants. R1 labels stay in the manifest (`source = rule`) for coverage
   and are reported apart in evaluation, never trained on. Reason [RUN]: with
   R1 in training the tier-1 audits fail (source-only AUC 0.790,
   scenario-ID 0.289); on the human labels alone (n = 131) all five pass
   (source-only 0.537, scenario-ID 0.492, file-name 0.546). Gated audits run
   on what trains.
7. **Variant audit [RUN]:** G1 3/20 (15%) and G2 8/20 (40%) disagree and are
   dropped; G3 0/3 and G4 0/10 are kept (13 variants). A tier whose
   repositories do not hold both classes in at least two of them is **not
   measurable** by leave-one-repository-out; tier 2 is reported as such, not
   as passed.

## Consequences

The report states: heldout and validation labels are human or R1 v2 (with
R1's measured error); training uses human, R1 and audited-variant labels
only; the assistant's labels and the kappa that ruled them out are reported.
P6 trains on 70 human train labels (14 refusals) and 4 G4 variants, with 60
human validation labels and 9 validation variants [RUN]. That is small and
is stated as the main limit of the dataset.
