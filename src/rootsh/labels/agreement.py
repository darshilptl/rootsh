"""Cohen's kappa between two passes over the same steps (design 05 §6)."""

from __future__ import annotations

from collections import Counter
from collections.abc import Hashable, Sequence
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Agreement:
    n: int
    observed: float  # raw agreement
    kappa: float | None  # None when chance agreement is 1 (one category only)


def cohen_kappa(a: Sequence[Hashable], b: Sequence[Hashable]) -> Agreement:
    if len(a) != len(b):
        raise ValueError("both passes must cover the same items")
    n = len(a)
    if n == 0:
        return Agreement(0, 0.0, None)
    observed = sum(x == y for x, y in zip(a, b, strict=True)) / n
    left, right = Counter(a), Counter(b)
    expected = sum(left[c] * right[c] for c in left.keys() | right.keys()) / (n * n)
    kappa = None if expected == 1 else (observed - expected) / (1 - expected)
    return Agreement(n, observed, kappa)


def weighted_kappa(a: Sequence[int], b: Sequence[int], levels: int) -> Agreement:
    """Quadratic-weighted kappa for ordinal scores 0..levels-1 (severity)."""
    if len(a) != len(b):
        raise ValueError("both passes must cover the same items")
    n = len(a)
    if n == 0:
        return Agreement(0, 0.0, None)
    weight = [[(i - j) ** 2 / (levels - 1) ** 2 for j in range(levels)] for i in range(levels)]
    left, right = Counter(a), Counter(b)
    disagreement = sum(weight[x][y] for x, y in zip(a, b, strict=True)) / n
    expected = sum(weight[i][j] * left[i] * right[j] for i in range(levels) for j in range(levels))
    expected /= n * n
    observed = sum(x == y for x, y in zip(a, b, strict=True)) / n
    kappa = None if expected == 0 else 1 - disagreement / expected
    return Agreement(n, observed, kappa)
