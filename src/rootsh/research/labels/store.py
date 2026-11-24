"""Reading and writing step labels (research DB, migration 3)."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from rootsh.research.labels.model import (
    AUTHOR,
    RUBRIC_VERSION,
    Cause,
    Harm,
    Reason,
    Round,
    Source,
    Technique,
    Verdict,
)
from rootsh.research.labels.rule import R1_VERDICT, R1_VERSION, r1_applies
from rootsh.research.labels.steps import Step

RULE_ANNOTATOR = "rule:R1"

type Key = tuple[str, int]


@dataclass(frozen=True, slots=True)
class Stored:
    id: int
    key: Key
    round: Round
    step_sha256: str
    verdict: Verdict


def record(
    conn: sqlite3.Connection,
    step: Step,
    verdict: Verdict,
    *,
    round: Round,
    now: datetime,
    source: Source = Source.HUMAN,
) -> int:
    """Store one answer; clears the step's deferral. Returns the row id."""
    annotator = RULE_ANNOTATOR if source is Source.RULE else AUTHOR
    version = f"R1.{R1_VERSION}" if source is Source.RULE else RUBRIC_VERSION
    with conn:
        cursor = conn.execute(
            "INSERT INTO step_labels (trace_id, seq, annotator, source, round, authorized, "
            "cause, harm, severity, technique, reason_code, note, rubric_version, step_sha256, "
            "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                step.trace.id,
                step.seq,
                annotator,
                source.value,
                round.value,
                int(verdict.authorized),
                verdict.cause.value,
                verdict.harm.value,
                verdict.severity,
                verdict.technique.value if verdict.technique else None,
                verdict.reason.value,
                verdict.note,
                version,
                step.sha256,
                now.isoformat(),
            ),
        )
        conn.execute("DELETE FROM step_deferrals WHERE trace_id = ? AND seq = ?", step.key)
    row = cursor.lastrowid
    assert row is not None
    return row


def undo(conn: sqlite3.Connection, row_id: int) -> None:
    with conn:
        conn.execute("DELETE FROM step_labels WHERE id = ? AND annotator = ?", (row_id, AUTHOR))


def defer(conn: sqlite3.Connection, step: Step, now: datetime) -> None:
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO step_deferrals (trace_id, seq, step_sha256, created_at) "
            "VALUES (?, ?, ?, ?)",
            (*step.key, step.sha256, now.isoformat()),
        )


def deferred(conn: sqlite3.Connection) -> set[Key]:
    return {(t, s) for t, s in conn.execute("SELECT trace_id, seq FROM step_deferrals")}


def author_labels(conn: sqlite3.Connection) -> list[Stored]:
    """Every answer the author gave, oldest first."""
    rows = conn.execute(
        "SELECT id, trace_id, seq, round, step_sha256, authorized, cause, harm, severity, "
        "technique, reason_code, note FROM step_labels WHERE annotator = ? ORDER BY id",
        (AUTHOR,),
    ).fetchall()
    return [
        Stored(
            row_id,
            (tid, seq),
            Round(rnd),
            sha,
            Verdict(
                bool(auth),
                Reason(reason),
                Cause(cause),
                Harm(harm),
                severity,
                Technique(tech) if tech else None,
                note,
            ),
        )
        for row_id, tid, seq, rnd, sha, auth, cause, harm, severity, tech, reason, note in rows
    ]


def apply_rule(conn: sqlite3.Connection, steps: Iterable[Step], now: datetime) -> int:
    """Bring R1 labels up to date: label every step R1 covers that has no
    current-version label, and remove R1 labels from steps the current
    version no longer covers. Returns how many labels were added."""
    version = f"R1.{R1_VERSION}"
    done = {
        (t, s): (sha, ver)
        for t, s, sha, ver in conn.execute(
            "SELECT trace_id, seq, step_sha256, rubric_version FROM step_labels "
            "WHERE annotator = ?",
            (RULE_ANNOTATOR,),
        )
    }
    count = 0
    for step in steps:
        wanted = r1_applies(step)
        current = done.get(step.key)
        if current is not None and (not wanted or current != (step.sha256, version)):
            with conn:
                conn.execute(
                    "DELETE FROM step_labels WHERE trace_id = ? AND seq = ? AND annotator = ?",
                    (*step.key, RULE_ANNOTATOR),
                )
        if wanted and current != (step.sha256, version):
            record(conn, step, R1_VERDICT, round=Round.MAIN, now=now, source=Source.RULE)
            count += 1
    return count


def r1_disputes(conn: sqlite3.Connection, steps: Iterable[Step]) -> list[tuple[Step, Stored]]:
    """R1-audit steps the author refused (R1 says yes), not yet adjudicated,
    with the author's first answer (design 05 §10 gate 2)."""
    by_key = {s.key: s for s in steps}
    settled = {(t, s) for t, s in conn.execute("SELECT trace_id, seq FROM adjudications")}
    firsts = {
        row_id
        for (row_id,) in conn.execute(
            "SELECT id FROM step_labels WHERE annotator = ? AND source = ? AND round = ? "
            "AND authorized = 0",
            (AUTHOR, Source.HUMAN.value, Round.AUDIT.value),
        )
    }
    return [
        (by_key[stored.key], stored)
        for stored in author_labels(conn)
        if stored.id in firsts
        and stored.key not in settled
        and by_key[stored.key].sha256 == stored.step_sha256
    ]


def adjudicate(conn: sqlite3.Connection, key: Key, chosen_label_id: int, now: datetime) -> None:
    """Record which of the author's answers stands for `key`."""
    with conn:
        conn.execute(
            "INSERT INTO adjudications (trace_id, seq, chosen_label_id, created_at) "
            "VALUES (?, ?, ?, ?)",
            (*key, chosen_label_id, now.isoformat()),
        )
