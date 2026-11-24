"""`rootsh model train|evaluate` (design 06 §6)."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from rootsh.decision.model import bundle
from rootsh.research.training import data, evaluate


def _summary(metrics: dict[str, dict[str, dict[str, object]]]) -> str:
    lines = []
    for name, subsets in metrics.items():
        for subset, m in subsets.items():
            counts = m["counts"]
            if not isinstance(counts, dict):
                raise TypeError("counts must be a mapping")
            lines.append(
                f"{name:<11} {subset:<10} n={m['n']:<4} auc={m['auc']} "
                f"false_allow={m['false_allow_rate']} "
                + " ".join(f"{k}={v}" for k, v in counts.items())
            )
    return "\n".join(lines)


def train(conn: sqlite3.Connection, manifest: Path, out: Path) -> int:
    frozen = data.load(conn, manifest, with_heldout=False)
    loaded = evaluate.train(conn, frozen, out, datetime.now(UTC))
    rows = conn.execute(
        "SELECT model_name, metrics_json FROM model_runs WHERE bundle_sha256 = ? "
        "AND split = 'validation'",
        (loaded.bundle_sha256,),
    ).fetchall()
    print(f"bundle {loaded.bundle_sha256[:12]} in {out}")
    print(_summary({name: json.loads(m) for name, m in rows}))
    return 0


def evaluate_heldout(conn: sqlite3.Connection, manifest: Path, out: Path) -> int:
    loaded = bundle.read(out)
    done = evaluate.evaluated(conn, loaded)
    if done:  # checked before the sealed labels are read
        raise evaluate.AlreadyEvaluatedError(f"heldout already evaluated for {', '.join(done)}")
    frozen = data.load(conn, manifest, with_heldout=True)
    metrics = evaluate.evaluate_heldout(conn, loaded, frozen, datetime.now(UTC))
    print(f"heldout evaluated once for bundle {loaded.bundle_sha256[:12]}")
    print(_summary(metrics))
    return 0
