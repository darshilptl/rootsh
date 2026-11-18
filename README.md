# Rootsh

A defense layer for host-level coding agents (Claude Code, OpenCode) on
Ubuntu. For every proposed action it checks whether the action stays within
the authority the user granted, traces where each critical argument came
from, and allows, asks, or blocks — with kernel enforcement underneath.

Status: **P0 complete** (verification spike). Results and author-run checks
are in `docs/verification/P0.md`; the design is `docs/PLAN.md`.
