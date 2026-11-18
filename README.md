# Rootsh

A defense layer for host-level coding agents (Claude Code, OpenCode) on
Ubuntu. For every proposed action it checks whether the action stays within
the authority the user granted, traces where each critical argument came
from, and allows, asks, or blocks — with kernel enforcement underneath.

Status: **P3 complete** (provenance engine).

- Plan and build order: `docs/plan.md`
- Decisions: `docs/decisions/`
- Design per phase: `docs/design/` · Results per phase: `docs/results/`
