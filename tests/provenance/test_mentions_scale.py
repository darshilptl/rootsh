"""Mention extraction must stay linear: the daemon runs it on every tool
output while holding its lock (found in P8: 60k chars took 5 s)."""

from __future__ import annotations

import time

import pytest

from rootsh.events.model import MAX_TEXT_CHARS
from rootsh.provenance.mentions import extract


@pytest.mark.parametrize("unit", ["x", "a.", "a/", "ab1+", "abc "])
def test_extract_is_linear_on_the_largest_text(unit: str) -> None:
    text = (unit * (MAX_TEXT_CHARS // len(unit) + 1))[:MAX_TEXT_CHARS]
    started = time.monotonic()
    extract(text, cwd="/p", home="/h")
    assert time.monotonic() - started < 2.0


def test_long_scheme_runs_still_find_the_url() -> None:
    found = extract("see " + "x" * 50 + "https://example.com/a", cwd=None, home="/h")
    assert "example.com" in found.hosts
