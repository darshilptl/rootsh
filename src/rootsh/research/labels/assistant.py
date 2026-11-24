"""The disclosed AI annotator's rounds (ADR-0012, ADR-0013).

Round 1 (ADR-0012: 100 agreement + 200 share steps) is stored in
`assistant_labels`; its sampler lived here until ADR-0013 moved the train
steps out of the author's queue (git history keeps it). Round 2 is
`remaining`: every train step the author does not label, plus stale labels.

The assistant sees each step exactly as the author's tool renders it, under a
neutral number. The number -> step map (trace ids carry scenario names) is
written to a separate file that only `import_answers` reads. Answers come back
as JSON lines and are checked against the closed vocabularies and the step
hash before anything is stored.
"""

from __future__ import annotations

import json
import random
import sqlite3
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from rootsh.research.labels.model import RUBRIC_VERSION, Cause, Harm, Reason, Technique, Verdict
from rootsh.research.labels.render import render
from rootsh.research.labels.session import Plan, assistant_steps
from rootsh.research.labels.steps import Step

BATCH = 20
SEED = 20261122


def remaining(
    conn: sqlite3.Connection, steps: Sequence[Step], plan: Plan
) -> list[tuple[Step, str]]:
    """What the assistant still owes (ADR-0013): every assistant training step
    without a current label (`share`), and every stored label whose step
    changed since (stale; re-labelled under its old purpose)."""
    by_key = {s.key: s for s in steps}
    stored = {
        (trace_id, seq): (sha, purpose)
        for trace_id, seq, sha, purpose in conn.execute(
            "SELECT trace_id, seq, step_sha256, purpose FROM assistant_labels"
        )
    }
    owed = [(s, "share") for s in assistant_steps(steps, plan) if s.key not in stored]
    owed += [
        (by_key[key], purpose)
        for key, (sha, purpose) in sorted(stored.items())
        if key in by_key and by_key[key].sha256 != sha
    ]
    return owed


def export_items(items: list[tuple[Step, str]], out: Path, map_path: Path) -> int:
    """Blind-shuffle (step, purpose) pairs into numbered batches."""
    if map_path.exists():
        raise FileExistsError(f"{map_path} exists; an earlier round's map is never overwritten")
    items = list(items)
    random.Random(SEED).shuffle(items)  # noqa: S311 - blind order, not a secret
    out.mkdir(parents=True, exist_ok=True)
    mapping = {}
    for start in range(0, len(items), BATCH):
        chunk = items[start : start + BATCH]
        texts = []
        for offset, (step, purpose) in enumerate(chunk):
            number = start + offset + 1
            mapping[str(number)] = [step.trace.id, step.seq, step.sha256, purpose]
            texts.append(render(step, f"===== STEP {number} " + "=" * 50))
        (out / f"batch-{start // BATCH + 1:02d}.txt").write_text("\n\n".join(texts) + "\n")
    map_path.parent.mkdir(parents=True, exist_ok=True)
    map_path.write_text(json.dumps(mapping, indent=1))
    return len(items)


class AnswerError(ValueError):
    """An answer line that is not a valid label."""


def parse_answer(line: str) -> tuple[str, Verdict]:
    raw = json.loads(line)
    try:
        authorized = bool(raw["authorized"])
        reason = Reason(raw["reason"])
        if authorized:
            return str(raw["n"]), Verdict(True, reason, note=raw.get("note"))
        return str(raw["n"]), Verdict(
            False,
            reason,
            Cause(raw["cause"]),
            Harm(raw["harm"]),
            int(raw["severity"]),
            Technique(raw["technique"]),
            raw.get("note"),
        )
    except (KeyError, ValueError) as exc:
        raise AnswerError(f"bad answer {line.strip()!r}: {exc}") from exc


def import_answers(
    conn: sqlite3.Connection,
    steps: Sequence[Step],
    answers: Path,
    map_path: Path,
    model: str,
    now: datetime,
) -> int:
    mapping = json.loads(map_path.read_text())
    by_key = {s.key: s for s in steps}
    rows = []
    for line in answers.read_text().splitlines():
        if not line.strip():
            continue
        number, verdict = parse_answer(line)
        trace_id, seq, sha, purpose = mapping[number]
        if by_key[(trace_id, seq)].sha256 != sha:
            raise AnswerError(f"step {number} changed since export; export again")
        rows.append(
            (
                trace_id,
                seq,
                model,
                purpose,
                int(verdict.authorized),
                verdict.cause.value,
                verdict.harm.value,
                verdict.severity,
                verdict.technique.value if verdict.technique else None,
                verdict.reason.value,
                verdict.note,
                RUBRIC_VERSION,
                sha,
                now.isoformat(),
            )
        )
    with conn:
        conn.executemany(
            "INSERT OR REPLACE INTO assistant_labels (trace_id, seq, model, purpose, authorized, "
            "cause, harm, severity, technique, reason_code, note, rubric_version, step_sha256, "
            "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
    return len(rows)
