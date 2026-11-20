"""Isotonic calibration and class-conditional conformal decisions
(docs/design/06-model.md §4-§5). Pure functions over plain floats, so the
runtime daemon needs neither scikit-learn nor numpy to apply them.
"""

from __future__ import annotations

import bisect
import math
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

ALPHA = 0.10


@dataclass(frozen=True, slots=True)
class Isotonic:
    """Increasing step-then-linear map: breakpoints xs (sorted) -> ys."""

    xs: tuple[float, ...]
    ys: tuple[float, ...]

    def __call__(self, score: float) -> float:
        if score <= self.xs[0]:
            return self.ys[0]
        if score >= self.xs[-1]:
            return self.ys[-1]
        i = bisect.bisect_right(self.xs, score)
        x0, x1, y0, y1 = self.xs[i - 1], self.xs[i], self.ys[i - 1], self.ys[i]
        return y0 if x1 == x0 else y0 + (y1 - y0) * (score - x0) / (x1 - x0)


def fit_isotonic(scores: Sequence[float], labels: Sequence[int]) -> Isotonic:
    """Pool-adjacent-violators on (score, label); equal scores pool first."""
    if not scores:
        raise ValueError("isotonic needs at least one point")
    points: dict[float, list[float]] = {}
    for s, y in zip(scores, labels, strict=True):
        points.setdefault(s, []).append(float(y))
    # blocks: [x_min, x_max, sum_y, weight]
    blocks: list[list[float]] = []
    for x in sorted(points):
        ys = points[x]
        blocks.append([x, x, sum(ys), float(len(ys))])
        while len(blocks) > 1 and blocks[-2][2] / blocks[-2][3] >= blocks[-1][2] / blocks[-1][3]:
            last = blocks.pop()
            blocks[-1][1] = last[1]
            blocks[-1][2] += last[2]
            blocks[-1][3] += last[3]
    xs: list[float] = []
    ys_out: list[float] = []
    for lo, hi, total, weight in blocks:
        value = total / weight
        xs += [lo] if lo == hi else [lo, hi]
        ys_out += [value] if lo == hi else [value, value]
    return Isotonic(tuple(xs), tuple(ys_out))


class Decision(StrEnum):
    ALLOW = "allow"
    ASK = "ask"
    BLOCK = "block"


@dataclass(frozen=True, slots=True)
class Conformal:
    """Per-class nonconformity thresholds; inf means the class is always kept."""

    alpha: float
    threshold_authorized: float  # on p (the score of class 0 is p itself)
    threshold_unauthorized: float  # on 1 - p

    def decide(self, p: float) -> Decision:
        keep_ok = p <= self.threshold_authorized
        keep_bad = 1 - p <= self.threshold_unauthorized
        if keep_ok and not keep_bad:
            return Decision.ALLOW
        if keep_bad and not keep_ok:
            return Decision.BLOCK
        return Decision.ASK  # both, or empty: a person decides


def quantile(scores: Sequence[float], alpha: float) -> float:
    """The ceil((n+1)(1-alpha))-th smallest score, or inf if n is too small."""
    n = len(scores)
    rank = math.ceil((n + 1) * (1 - alpha))
    return math.inf if rank > n else sorted(scores)[rank - 1]


def fit_conformal(probs: Sequence[float], labels: Sequence[int], alpha: float = ALPHA) -> Conformal:
    ok = [p for p, y in zip(probs, labels, strict=True) if y == 0]
    bad = [1 - p for p, y in zip(probs, labels, strict=True) if y == 1]
    return Conformal(alpha, quantile(ok, alpha), quantile(bad, alpha))
