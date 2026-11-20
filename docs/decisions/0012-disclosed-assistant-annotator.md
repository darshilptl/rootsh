# ADR-0012: A disclosed AI annotator for the training tail, measured by kappa

**Status:** Accepted · 2026-11-20 (author) · Amends ADR-0010 item 1 and ADR-0011

## Context

The author's measured pace is a median of 62 s per step [RUN: 31 answers in
41.3 min], so 767 answers would take about 12 hours. The author wants to
label as many as possible, with the steps the result depends on first.

## Decision

1. **Order.** The author's queue serves heldout steps first, then
   validation, then train (seeded order kept within each). No step is
   removed from the author's queue.
2. **Assistant share.** Claude (the model that builds rootsh, named per row in
   `assistant_labels.model`) labels the last 200 train steps of the queue
   blind: the same rendering the author sees, no scenario, no trace id, no
   author answer. A share label is used **only if the author leaves that
   step unlabelled**; the author's answer always wins.
3. **Agreement sample.** The assistant also labels 100 steps stratified
   across splits, agents and effect kinds (R1 audit steps excluded). Cohen's
   kappa between author and assistant is reported on every step both
   labelled. These labels are never final.
4. **Kept human-only:** every heldout and validation label that enters the
   frozen dataset, the R1 audit, the variant audit, and the parents of every
   tier-2 variant.
5. **Test-retest stays** (ADR-0011): re-tests remain in the author's queue.

## Consequences

The report states how many training labels come from the assistant, the
model, and the author-vs-assistant kappa. If that kappa on `authorized` is
below 0.60 (target), share labels are dropped and only human-labelled
training steps are used. The rule "no LLM judges an action" (AGENTS.md §2.3)
is about the defense at runtime and is unchanged; this is offline
annotation, disclosed and measured.
