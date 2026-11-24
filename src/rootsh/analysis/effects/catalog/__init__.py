"""The command catalog: program name -> handler."""

from __future__ import annotations

import re
from collections.abc import Mapping

from rootsh.analysis.effects.catalog import devtools, execution, files, git, network, system
from rootsh.analysis.effects.catalog.base import Handler, Invocation, Outcome


def _merge(*parts: Mapping[str, Handler]) -> dict[str, Handler]:
    merged: dict[str, Handler] = {}
    for part in parts:
        clash = merged.keys() & part.keys()
        if clash:
            raise ValueError(f"programs registered twice: {sorted(clash)}")
        merged.update(part)
    return merged


CATALOG: Mapping[str, Handler] = _merge(
    files.CATALOG,
    network.CATALOG,
    execution.CATALOG,
    system.CATALOG,
    devtools.CATALOG,
    {"git": git.git},
)

_PATTERNS: tuple[tuple[re.Pattern[str], Handler], ...] = (
    (re.compile(r"python(?:\d+(?:\.\d+)?)?|pypy3?"), execution.python),
    (re.compile(r"pip\d+(?:\.\d+)?"), execution.pip),
    (re.compile(r"mkfs(?:\.\w+)?"), files.format_device),
)


def lookup(program: str) -> Handler | None:
    handler = CATALOG.get(program)
    if handler is not None:
        return handler
    for pattern, candidate in _PATTERNS:
        if pattern.fullmatch(program):
            return candidate
    return None


def known(program: str) -> bool:
    return lookup(program) is not None


__all__ = ["CATALOG", "Invocation", "Outcome", "known", "lookup"]
