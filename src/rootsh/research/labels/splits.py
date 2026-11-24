"""Splits by repository, fixed by hash before any label (design 05 §8)."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable

SALT = "rootsh-p5-split:"
HELDOUT = frozenset({"service", "docsite"})
VALIDATION = frozenset({"dotfiles"})


def hash_order(repositories: Iterable[str]) -> list[str]:
    return sorted(repositories, key=lambda r: hashlib.sha256((SALT + r).encode()).hexdigest())


def split_of(repository: str) -> str:
    if repository in HELDOUT:
        return "heldout"
    return "validation" if repository in VALIDATION else "train"
