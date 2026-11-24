# ADR-0005: Archive the old researchwork project; reuse nothing

**Status:** Accepted · 2026-11-18

## Context

The audit (`docs/plan.md`, Phase A) found its labels leak through file-name templates, its heldout set was evaluated 12 times, and its system was not connected to any agent.

## Decision

`researchwork` is archived as-is (its `.venv` was later removed to free 380 MB). No data, model, or result from it enters Rootsh.

## Consequences

Its audit scripts live in `v0.11.0:research/old-audit/` so every Phase A number stays reproducible.
