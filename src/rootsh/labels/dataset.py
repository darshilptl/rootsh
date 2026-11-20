"""The labelled dataset as it will be frozen (design 05 §8, ADR-0014).

One final label per step: the author's standing answer (adjudicated where a
second look was taken), else the current R1 rule label. Training steps that
only the disclosed assistant labelled are excluded: its agreement with the
author was below the ADR-0013 target. Variants come from generators whose
blind audit disagreement is at most `DROP_ABOVE`.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass

from rootsh.labels import variant_store
from rootsh.labels.model import Verdict
from rootsh.labels.rule import R1_VERDICT, R1_VERSION, r1_applies
from rootsh.labels.splits import split_of
from rootsh.labels.steps import Step
from rootsh.labels.store import Key

DROP_ABOVE = 0.10  # design 05 §7: a generator disagreeing more than this is dropped
# What a model may train on (ADR-0014): R1's all-yes read labels made agent and
# tool a shortcut (tier-1 source-only AUC 0.790), so R1 labels only report.
TRAINING_SOURCES = frozenset({"human", "adjudicated", "G1", "G2", "G3", "G4"})


@dataclass(frozen=True, slots=True)
class Example:
    id: str  # "trace_id|seq" for a real step, the variant id for a variant
    step: Step
    split: str
    source: str  # human | adjudicated | rule | G1..G4
    verdict: Verdict


@dataclass(frozen=True, slots=True)
class GeneratorAudit:
    generator: str
    audited: int
    disagree: int

    @property
    def kept(self) -> bool:
        return self.audited > 0 and self.disagree / self.audited <= DROP_ABOVE


@dataclass(frozen=True, slots=True)
class Dataset:
    examples: tuple[Example, ...]
    excluded: tuple[Key, ...]  # train steps with only assistant labels (ADR-0014)
    generators: tuple[GeneratorAudit, ...]


def generator_audits(conn: sqlite3.Connection) -> tuple[GeneratorAudit, ...]:
    rows = conn.execute(
        "SELECT v.generator, count(a.variant_id), "
        "coalesce(sum(a.authorized != v.authorized), 0) "
        "FROM variants v LEFT JOIN variant_audit a ON a.variant_id = v.id "
        "GROUP BY v.generator ORDER BY v.generator"
    ).fetchall()
    return tuple(GeneratorAudit(g, n, d) for g, n, d in rows)


def assemble(conn: sqlite3.Connection, steps: Sequence[Step]) -> Dataset:
    by_key = {s.key: s for s in steps}
    final = variant_store.final_labels(conn, by_key)
    sources = dict(conn.execute("SELECT id, source FROM step_labels WHERE annotator = 'author'"))
    rule = {
        (t, s): sha
        for t, s, sha in conn.execute(
            "SELECT trace_id, seq, step_sha256 FROM step_labels "
            "WHERE annotator = 'rule:R1' AND rubric_version = ?",
            (f"R1.{R1_VERSION}",),
        )
    }
    examples: list[Example] = []
    excluded: list[Key] = []
    for step in steps:
        split = split_of(step.trace.repository)
        name = f"{step.trace.id}|{step.seq}"
        if step.key in final:
            label_id, verdict = final[step.key]
            examples.append(Example(name, step, split, sources[label_id], verdict))
        elif r1_applies(step) and rule.get(step.key) == step.sha256:
            examples.append(Example(name, step, split, "rule", R1_VERDICT))
        else:
            excluded.append(step.key)
    audits = generator_audits(conn)
    kept = {a.generator for a in audits if a.kept}
    for variant in variant_store.load(conn, by_key):
        if variant.generator in kept:
            split = split_of(variant.step.trace.repository)
            examples.append(
                Example(variant.id, variant.step, split, variant.generator, variant.verdict)
            )
    return Dataset(tuple(examples), tuple(excluded), audits)
