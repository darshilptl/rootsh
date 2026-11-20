# ADR-0010: Labelling scope, rule labels, and shortcut gates per tier

**Status:** Accepted · 2026-11-20 (author, at the start of P5)

## Context

The gold set has 1,764 proposed steps [RUN]; 990 are OpenCode reads. Labels
must come from a person (plan B1: no auto-generated reasoning), and the
author's time is the limit. Plan B1 also asks every one-field classifier,
including action-only and prompt-only, to score heldout AUC ≤ 0.60. On real
traces the action carries real signal (`rm -rf ~` is harmful whatever the
prompt), so that bar can only be met where contrastive twins are built in.

## Decision

1. **Hand labels** for every step outside rule R1 (design 05 §4), plus a
   stratified sample of R1 steps that audits the rule.
2. **Rule R1** labels the remaining steps (fully analysed, read-only, every
   target inside the run's workspace). Such rows store `source = rule`, the
   rule id and its version, never a person's name.
3. **Second annotator:** the author's guide labels a stratified sample;
   Cohen's κ is reported (ADR-0004).
4. **Tier-2 variant labels** come from a fixed edit → label table committed
   before generation; the author audits a stratified sample, and a generator
   whose disagreement exceeds its target is dropped.
5. **Shortcut gates per tier.** On tier-2 training data every one-field
   audit gates at AUC ≤ 0.60. On tier-1 gold, source, task/scenario-ID and
   file-name-token audits gate; action-only and prompt-only are reported, not
   gated.
6. **Audits never touch heldout.** They run as leave-one-repository-out over
   the non-heldout repositories; heldout is first read in P6.

## Consequences

The R1 rule is a possible shortcut (read-only ⇒ authorized); the audit
sample and the tier-2 twins that keep a read but change its target bound it.
The tier-1 action-only number is a property of the real world, reported as
such, not a dataset defect.
