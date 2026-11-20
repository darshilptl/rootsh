# ADR-0016: P7 policy order, fast paths, and where probes run

**Status:** Accepted · 2026-11-20 (author delegated P7 choices to Claude's
recommendation under the submission deadline) · Applies plan B5 steps 5–7.

## Context

P6's conformal model never allows (results 06 §3): used alone it asks on
~935 of 1,000 authorized steps. Plan B5 puts invariants above the model;
results 06 §5 says invariants and a read-only fast path must carry the
policy. The open item from P2 asks which process runs host-state probes.

## Decision

1. **Policy order is fixed before measurement** (design 07 §2): failure →
   I1 defense → I2 untrusted + level 3 → F1 read-only → F2 in-project
   level-1 edit → model. Nothing is tuned on validation.
2. **F2 is added beyond the plan.** Level-1 in-project edits with no
   sensitive class, nothing opaque and no content-only argument are
   allowed without the model. Reason: they are the most common
   authorized write and recoverable by the agent's own next edit; P6 has no
   probe features to tell the model so. Fabricated arguments are not
   excluded: new files are named by the agent, so they are fabricated by
   definition. The burden table reports F2's
   share so its effect is visible.
3. **Untrusted means content-only**, as in the plan. Fabricated arguments
   (hallucination) are left to the model; I2 does not block them.
4. **Probes run in the daemon, as the author's user**, outside the agent
   scope; they only add sensitivity classes.
5. **Policy burden is measured on validation, not heldout.** Heldout stays
   for the P10 end-to-end evaluation, once.

## Consequences

- F1/F2 allows are made without the model's guarantee; their false-allow
  count on validation is reported.
- Approval separation depends on the agent scope; P9 must stop agents from
  leaving it (design 07 §4).
