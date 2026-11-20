# ADR-0013: Human labels for evaluation, disclosed model labels for training

**Status:** Accepted · 2026-11-20 (author) · Supersedes ADR-0011 (test–retest) and
ADR-0012 items 2 and 5

## Context

Measured pace: 52 author answers, median 56 s per step (50 s over the last
15) [RUN]. The full queue (767 answers) would take about 11 hours. Production
labelling and published dataset work label the evaluation set by hand and
the rest by model or crowd, with measured agreement.

## Decision

1. **The author labels:** every heldout step, every validation step, and the
   R1-audit steps in train (38). Remaining at the decision: about 220.
2. **Claude labels every other training step** blind, in `assistant_labels`
   (`purpose = 'share'`), as in ADR-0012: same rendering, no scenario, no
   trace id, no author answer. Where the author also labelled a step, the
   author's answer wins.
3. **Agreement:** Cohen's kappa between author and Claude on every step both
   labelled (ADR-0012's 100 agreement steps, mostly heldout and validation),
   reported before any adjudication. Target kappa ≥ 0.60 on `authorized`;
   below it, Claude's training labels are dropped and only human-labelled
   training steps are used.
4. **Self re-tests are dropped.** The author-vs-Claude kappa replaces them.
5. **Unchanged:** the evaluation (heldout) labels, the R1 audit, the variant
   audit and every variant parent are human only.

## Consequences

The report states: heldout and validation labels are 100% human; training
labels are model-labelled and human-audited, with the kappa. The queue and
the assistant selection change in code (next session); labels already
stored stay valid (same step hashes).
