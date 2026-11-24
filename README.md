# Rootsh

A defense layer for host-level coding agents (Claude Code, OpenCode) on
Ubuntu. For every proposed action it checks whether the action stays within
the authority the user granted, traces where each critical argument came
from, and allows, asks, or blocks. Kernel enforcement runs underneath.

Status: **v0.12.0**, phases P0–P12 done.

- Docs index ("where is the answer"): `docs/README.md`
- Every report number: `docs/report-tables.md` (`uv run python research/report_tables.py`)
- Code: `src/rootsh/{analysis,decision,enforcement,research}`, ADR-0019
- Checks: `uv run ruff check && uv run ruff format --check && uv run mypy && uv run pytest`
