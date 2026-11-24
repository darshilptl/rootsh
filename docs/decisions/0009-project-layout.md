# ADR-0009: Project layout

**Status:** Superseded by ADR-0019 · 2026-11-18

## Context

The repository grew phase by phase (`spikes/`, `scripts/`, flat tests, docs by phase number).

## Decision

One package folder per subsystem under `src/rootsh/`; tests mirror the package with shared helpers in `tests/support/`; docs by kind (`plan.md`, `decisions/`, `design/`, `results/`); experiments and reproducibility scripts in `research/`; local corpora in git-ignored `data/`. The TypeScript OpenCode plugin will live in `plugins/opencode/` from P8.

## Consequences

No behavior change: the restructure kept all 181 tests passing.
