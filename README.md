# Rootsh

A defense layer for host-level coding agents (Claude Code, OpenCode) on
Ubuntu. For every proposed action it checks whether the action stays within
the authority the user granted, traces where each critical argument came
from, and allows, asks, or blocks — with kernel enforcement underneath.

Status: **P1 complete** (event model and storage). Phase results are in
`docs/verification/P0.md` and `docs/summaries/`; the design is `docs/PLAN.md`.
