# ADR-0002: Size the trace budget from measured cost

**Status:** Accepted · 2026-11-18

## Context

Collecting agent traces costs model usage, and the author's Claude Code budget and RAM are limited.

## Decision

P0 used a handful of short runs on the smallest model. P4 sets the trace budget from the per-run cost P0 measured: a one-tool Claude Code run on Haiku cost $0.0026–$0.0037 (mean $0.0028, 13 runs); OpenCode free models cost $0.

## Consequences

No collection starts before the P4 design states the counts and the budget. See ADR-0008 for how the data is produced.
