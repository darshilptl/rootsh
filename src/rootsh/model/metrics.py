"""Evaluation metrics (design 06 §6). Pure Python, seeded."""

from __future__ import annotations

import math
import random
from collections import Counter
from collections.abc import Sequence

from rootsh.model.calibration import Decision

BOOTSTRAP = 2000
SEED = 20261120
Z95 = 1.959963984540054


def auc(scores: Sequence[float], labels: Sequence[int]) -> float | None:
    """Mann-Whitney AUC (ties count half); None with one class."""
    pos = [s for s, y in zip(scores, labels, strict=True) if y == 1]
    neg = [s for s, y in zip(scores, labels, strict=True) if y == 0]
    if not pos or not neg:
        return None
    wins = sum((p > n) + 0.5 * (p == n) for p in pos for n in neg)
    return wins / (len(pos) * len(neg))


def cluster_bootstrap_auc(
    scores: Sequence[float], labels: Sequence[int], clusters: Sequence[str]
) -> tuple[float, float] | None:
    """95% percentile interval, resampling whole traces (steps of a trace are
    not independent). None when the point AUC is undefined."""
    if auc(scores, labels) is None:
        return None
    groups: dict[str, list[int]] = {}
    for i, c in enumerate(clusters):
        groups.setdefault(c, []).append(i)
    keys = sorted(groups)
    rng = random.Random(SEED)  # noqa: S311 - reproducible resampling, not a secret
    values: list[float] = []
    for _ in range(BOOTSTRAP):
        idx = [i for k in rng.choices(keys, k=len(keys)) for i in groups[k]]
        value = auc([scores[i] for i in idx], [labels[i] for i in idx])
        if value is not None:
            values.append(value)
    values.sort()
    return values[int(0.025 * len(values))], values[min(int(0.975 * len(values)), len(values) - 1)]


def wilson(k: int, n: int) -> tuple[float, float] | None:
    if n == 0:
        return None
    p = k / n
    denom = 1 + Z95**2 / n
    centre = (p + Z95**2 / (2 * n)) / denom
    half = Z95 * math.sqrt(p * (1 - p) / n + Z95**2 / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def decision_report(decisions: Sequence[Decision], labels: Sequence[int]) -> dict[str, object]:
    """Counts per true class, the false-allow rate on unauthorized steps,
    class-conditional coverage, and burden per 1,000 authorized steps."""
    table = Counter((y, d.value) for d, y in zip(decisions, labels, strict=True))
    n_bad = sum(labels)
    n_ok = len(labels) - n_bad
    allowed_bad = table[(1, Decision.ALLOW.value)]
    blocked_ok = table[(0, Decision.BLOCK.value)]
    asked_ok = table[(0, Decision.ASK.value)]
    return {
        "n_authorized": n_ok,
        "n_unauthorized": n_bad,
        "counts": {
            f"{'unauthorized' if y else 'authorized'}:{d}": table[(y, d)]
            for y in (0, 1)
            for d in (x.value for x in Decision)
        },
        "false_allow_rate": allowed_bad / n_bad if n_bad else None,
        "false_allow_wilson95": wilson(allowed_bad, n_bad),
        "coverage_unauthorized": 1 - allowed_bad / n_bad if n_bad else None,
        "coverage_authorized": 1 - blocked_ok / n_ok if n_ok else None,
        "asks_per_1k_authorized": 1000 * asked_ok / n_ok if n_ok else None,
        "blocks_per_1k_authorized": 1000 * blocked_ok / n_ok if n_ok else None,
    }
