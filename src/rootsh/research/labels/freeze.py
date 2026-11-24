"""`rootsh dataset freeze` (design 05 §8): the dataset as a hashed manifest.

The manifest holds every non-heldout example with its final label, the
heldout examples only as hashes, and the rule and rubric versions. Heldout
labels go to a separate sealed file that only the P6 evaluator reads. The
manifest has no timestamps, so freezing the same data twice gives the same
hash. A freeze refuses to run while any gate fails.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from rootsh.research.labels import variant_store
from rootsh.research.labels.audits import AuditResult
from rootsh.research.labels.dataset import TRAINING_SOURCES, Dataset, Example
from rootsh.research.labels.model import RUBRIC_VERSION
from rootsh.research.labels.rule import R1_VERSION
from rootsh.research.labels.splits import SALT
from rootsh.research.labels.steps import all_steps
from rootsh.research.labels.variants import RULES_VERSION


class FreezeError(RuntimeError):
    """A gate fails; nothing is written."""


def _label(e: Example) -> dict[str, object]:
    v = e.verdict
    return {
        "id": e.id,
        "split": e.split,
        "source": e.source,
        "authorized": v.authorized,
        "cause": v.cause.value,
        "harm": v.harm.value,
        "severity": v.severity,
        "technique": v.technique.value if v.technique else None,
        "reason": v.reason.value,
        "step_sha256": e.step.sha256,
    }


def _bytes(obj: object) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")


def manifest(dataset: Dataset) -> tuple[bytes, bytes]:
    """(manifest, sealed heldout labels), both canonical JSON."""
    open_rows = sorted((e for e in dataset.examples if e.split != "heldout"), key=lambda e: e.id)
    sealed = sorted((e for e in dataset.examples if e.split == "heldout"), key=lambda e: e.id)
    body = {
        "versions": {
            "rubric": RUBRIC_VERSION,
            "rule_r1": R1_VERSION,
            "variant_rules": RULES_VERSION,
            "split_salt": SALT,
        },
        "training_sources": sorted(TRAINING_SOURCES),
        "examples": [_label(e) for e in open_rows],
        "heldout": [hashlib.sha256(f"{e.id}|{e.step.sha256}".encode()).hexdigest() for e in sealed],
        "excluded_assistant_only": len(dataset.excluded),
        "generators": [
            {"generator": g.generator, "audited": g.audited, "disagree": g.disagree, "kept": g.kept}
            for g in dataset.generators
        ],
    }
    return _bytes(body), _bytes([_label(e) for e in sealed])


def gate_failures(
    conn: sqlite3.Connection, dataset: Dataset, audits: Sequence[AuditResult]
) -> list[str]:
    steps = list(all_steps(conn))
    sample = variant_store.audit_sample(variant_store.load(conn, {s.key: s for s in steps}))
    failures = []
    if variant_store.audited(conn) < {v.id for v in sample}:
        failures.append("variant audit incomplete")
    (deferred,) = conn.execute("SELECT count(*) FROM step_deferrals").fetchone()
    if deferred:
        failures.append(f"{deferred} deferred steps")
    failures += [
        f"tier {a.tier} {a.view} AUC {a.auc:.3f} beyond the gate"
        for a in audits
        if not a.passed and a.auc is not None
    ]
    return failures


def freeze(
    conn: sqlite3.Connection,
    dataset: Dataset,
    audits: Sequence[AuditResult],
    out: Path,
    now: datetime,
) -> str:
    """Write the manifest and the sealed file; record the hash. Returns it."""
    failures = gate_failures(conn, dataset, audits)
    if failures:
        raise FreezeError("; ".join(failures))
    body, sealed = manifest(dataset)
    digest = hashlib.sha256(body).hexdigest()
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"manifest-{digest[:12]}.json"
    path.write_bytes(body)
    (out / f"heldout-{digest[:12]}.sealed.json").write_bytes(sealed)
    with conn:
        conn.execute(
            "INSERT OR IGNORE INTO freezes (manifest_sha256, manifest_path, created_at) "
            "VALUES (?, ?, ?)",
            (digest, str(path), now.isoformat()),
        )
    return digest
