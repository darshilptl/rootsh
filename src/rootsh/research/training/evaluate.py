"""Train and evaluate, logging every evaluation to `model_runs` (design 06 §6).

Heldout runs once per model: the research DB's UNIQUE (model_sha256, split)
key refuses a second heldout row, and `evaluate_heldout` checks it before it
even reads the sealed labels.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path

from rootsh.decision.model import bundle
from rootsh.decision.model.scorers import MODEL_NAMES
from rootsh.research.labels.dataset import TRAINING_SOURCES, Example
from rootsh.research.training import fit
from rootsh.research.training.data import Frozen, target
from rootsh.research.training.metrics import auc, cluster_bootstrap_auc, decision_report


class AlreadyEvaluatedError(RuntimeError):
    """A model in this bundle already has a heldout evaluation."""


def subset_metrics(fitted: bundle.Fitted, examples: Sequence[Example]) -> dict[str, object]:
    probs = fitted.probabilities(examples)
    labels = [target(e) for e in examples]
    decisions = [fitted.conformal.decide(p) for p in probs]
    interval = cluster_bootstrap_auc(probs, labels, [e.step.trace.id for e in examples])
    return {
        "n": len(examples),
        "auc": auc(probs, labels),
        "auc_bootstrap95": list(interval) if interval else None,
        **decision_report(decisions, labels),
        "false_allows": sorted(
            e.id for e, d, y in zip(examples, decisions, labels, strict=True)
            if y == 1 and d.value == "allow"
        ),
    }  # fmt: skip


def _log(
    conn: sqlite3.Connection,
    loaded: bundle.Loaded,
    split: str,
    metrics: Mapping[str, object],
    now: datetime,
) -> None:
    with conn:
        conn.executemany(
            "INSERT INTO model_runs (model_name, model_sha256, bundle_sha256, manifest_sha256, "
            "split, metrics_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (name, loaded.model_sha256[name], loaded.bundle_sha256, loaded.manifest_sha256,
                 split, json.dumps(metrics[name], sort_keys=True), now.isoformat())
                for name in MODEL_NAMES
            ],
        )  # fmt: skip


def train(conn: sqlite3.Connection, frozen: Frozen, out: Path, now: datetime) -> bundle.Loaded:
    """Fit every model, write the bundle, log validation metrics. Validation
    is also the conformal calibration set, so its decision counts are
    in-sample and reported as such."""
    fitted = {name: fit.fit_model(name, frozen) for name in MODEL_NAMES}
    fit.write(out, frozen.manifest_sha256, fitted)
    loaded = bundle.read(out)
    metrics = {
        name: {"validation": subset_metrics(m, frozen.validation)}
        for name, m in loaded.models.items()
    }
    _log(conn, loaded, "validation", metrics, now)
    return loaded


def evaluated(conn: sqlite3.Connection, loaded: bundle.Loaded) -> list[str]:
    hashes = list(loaded.model_sha256.values())
    marks = ",".join("?" * len(hashes))
    rows = conn.execute(
        f"SELECT model_name FROM model_runs WHERE split = 'heldout' "  # noqa: S608 - placeholders only
        f"AND model_sha256 IN ({marks})",
        hashes,
    ).fetchall()
    return sorted(r[0] for r in rows)


def heldout_subsets(examples: Sequence[Example]) -> dict[str, list[Example]]:
    return {
        "human": [e for e in examples if e.source in TRAINING_SOURCES],
        "rule": [e for e in examples if e.source == "rule"],
    }


def evaluate_heldout(
    conn: sqlite3.Connection, loaded: bundle.Loaded, frozen: Frozen, now: datetime
) -> dict[str, dict[str, dict[str, object]]]:
    if loaded.manifest_sha256 != frozen.manifest_sha256:
        raise bundle.BundleError("bundle was trained on another manifest")
    done = evaluated(conn, loaded)
    if done:
        raise AlreadyEvaluatedError(f"heldout already evaluated for {', '.join(done)}")
    subsets = heldout_subsets(frozen.heldout)
    metrics: dict[str, dict[str, dict[str, object]]] = {
        name: {subset: subset_metrics(fitted, rows) for subset, rows in subsets.items()}
        for name, fitted in loaded.models.items()
    }
    _log(conn, loaded, "heldout", metrics, now)
    return metrics
