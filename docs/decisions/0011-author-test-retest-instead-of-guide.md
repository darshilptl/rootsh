# ADR-0011: Author test–retest instead of a second annotator

**Status:** Accepted · 2026-11-20 (author) · Supersedes ADR-0010 item 3

## Context

ADR-0010 named the author's guide as the second annotator. The guide is not
available to label (author, 2026-11-20); all labelling is the author's.

## Decision

ADR-0004's fallback applies: agreement is measured as **test–retest**. The
labelling tool re-serves a stratified sample of steps the author already
labelled, unannounced, mixed with new steps, once at least 150 other steps
have been labelled since the first label. The author has hours, not days
(author, 2026-11-20), so the gap is counted in steps; the achieved gap is
reported. κ is computed between the two passes.

## Consequences

Test–retest measures the author's consistency, not agreement between
people, and a same-day gap lets memory inflate it; the results state both
as limitations. Disagreements between the two
passes are adjudicated by the author with a reason code.
