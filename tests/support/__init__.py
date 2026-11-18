"""Shared test helpers (importable because pyproject sets pythonpath = tests)."""

from pathlib import Path

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures"
