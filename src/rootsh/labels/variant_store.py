"""Final labels, stored variants, and the variant audit (design 05 §6-§7)."""

from __future__ import annotations

import json
import random
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from rootsh.labels.model import RUBRIC_VERSION, Cause, Harm, Reason, Round, Technique, Verdict
from rootsh.labels.session import stratified
from rootsh.labels.splits import HELDOUT
from rootsh.labels.steps import Step
from rootsh.labels.store import Key, author_labels
from rootsh.labels.variants import RULES_VERSION, Edits, Generated, Variant, apply

AUDIT_PER_GENERATOR = 20
AUDIT_SEED = 20261121


class VariantError(Exception):
    """Variants cannot be (re)generated safely."""


def final_labels(
    conn: sqlite3.Connection, by_key: Mapping[Key, Step]
) -> dict[Key, tuple[int, Verdict]]:
    """The author's standing answer per step: the adjudicated one when a
    re-test disagreed, else the first pass. Stale answers (the step's context
    changed since) are left out."""
    chosen = dict(conn.execute("SELECT trace_id || '|' || seq, chosen_label_id FROM adjudications"))
    labels = author_labels(conn)
    by_id = {s.id: s for s in labels}
    final: dict[Key, tuple[int, Verdict]] = {}
    for stored in labels:
        if stored.round is Round.RETEST or by_key[stored.key].sha256 != stored.step_sha256:
            continue
        pick = chosen.get(f"{stored.key[0]}|{stored.key[1]}")
        winner = by_id[pick] if pick is not None else stored
        final[stored.key] = (winner.id, winner.verdict)
    return final


def parents(conn: sqlite3.Connection, steps: Sequence[Step]) -> list[tuple[Step, Verdict, int]]:
    """Hand-labelled steps outside the heldout repositories."""
    by_key = {s.key: s for s in steps}
    final = final_labels(conn, by_key)
    return [
        (step, final[step.key][1], final[step.key][0])
        for step in steps
        if step.key in final and step.trace.repository not in HELDOUT
    ]


def store(
    conn: sqlite3.Connection, generated: Generated, label_ids: Mapping[Key, int], now: datetime
) -> int:
    """Replace every variant. Refused once any variant has been audited:
    regenerating would change what was audited."""
    (audited,) = conn.execute("SELECT count(*) FROM variant_audit").fetchone()
    if audited:
        raise VariantError(f"{audited} variants are already audited; refusing to regenerate")
    with conn:
        conn.execute("DELETE FROM variants")
        conn.executemany(
            "INSERT INTO variants (id, parent_trace_id, parent_seq, parent_label_id, generator, "
            "rules_version, edits_json, authorized, cause, harm, severity, technique, "
            "reason_code, step_sha256, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [_row(v, label_ids[v.parent.key], now) for v in generated.variants],
        )
    return len(generated.variants)


def _row(v: Variant, label_id: int, now: datetime) -> tuple[object, ...]:
    return (
        v.id,
        v.parent.trace.id,
        v.parent.seq,
        label_id,
        v.generator,
        RULES_VERSION,
        v.edits.to_json(),
        *_verdict_columns(v.verdict),
        v.step.sha256,
        now.isoformat(),
    )


def _verdict_columns(verdict: Verdict) -> tuple[object, ...]:
    return (
        int(verdict.authorized),
        verdict.cause.value,
        verdict.harm.value,
        verdict.severity,
        verdict.technique.value if verdict.technique else None,
        verdict.reason.value,
    )


@dataclass(frozen=True, slots=True)
class StoredVariant:
    id: str
    generator: str
    step: Step
    verdict: Verdict


def _edits(text: str) -> Edits:
    raw = json.loads(text)
    plant = raw["plant"]
    return Edits(raw["prompt"], (plant[0], plant[1]) if plant else None, raw["args"])


def load(conn: sqlite3.Connection, by_key: Mapping[Key, Step]) -> list[StoredVariant]:
    rows = conn.execute(
        "SELECT id, parent_trace_id, parent_seq, generator, edits_json, authorized, cause, harm, "
        "severity, technique, reason_code FROM variants ORDER BY id"
    ).fetchall()
    return [
        StoredVariant(
            vid,
            generator,
            apply(by_key[(tid, seq)], _edits(edits)),
            Verdict(
                bool(auth),
                Reason(reason),
                Cause(cause),
                Harm(harm),
                sev,
                Technique(tech) if tech else None,
            ),
        )
        for vid, tid, seq, generator, edits, auth, cause, harm, sev, tech, reason in rows
    ]


def audit_sample(variants: Sequence[StoredVariant]) -> list[StoredVariant]:
    """Up to 20 per generator, stratified by repository, in a seeded order."""
    rng = random.Random(AUDIT_SEED)  # noqa: S311 - a reproducible sample, not a secret
    picked: list[StoredVariant] = []
    for generator in sorted({v.generator for v in variants}):
        mine = [v for v in variants if v.generator == generator]
        picked += stratified(mine, lambda v: v.step.trace.repository, AUDIT_PER_GENERATOR, rng)
    rng.shuffle(picked)
    return picked


def audited(conn: sqlite3.Connection) -> set[str]:
    return {vid for (vid,) in conn.execute("SELECT variant_id FROM variant_audit")}


def record_audit(
    conn: sqlite3.Connection, variant_id: str, verdict: Verdict, now: datetime
) -> None:
    with conn:
        conn.execute(
            "INSERT INTO variant_audit (variant_id, authorized, cause, harm, severity, technique, "
            "reason_code, note, rubric_version, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (variant_id, *_verdict_columns(verdict), verdict.note, RUBRIC_VERSION, now.isoformat()),
        )
