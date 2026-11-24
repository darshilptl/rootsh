from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from rootsh.research.labels import assistant, session
from rootsh.research.labels.agreement import cohen_kappa, weighted_kappa
from rootsh.research.labels.steps import all_steps
from support.label_traces import NOW, call, prompt, store_trace


def test_kappa_matches_a_hand_computed_value() -> None:
    # observed 0.75; chance 0.5*0.25 + 0.5*0.75 = 0.5; kappa (0.75-0.5)/(1-0.5) = 0.5
    result = cohen_kappa([1, 1, 0, 0], [1, 0, 0, 0])
    assert (result.n, result.observed, result.kappa) == (4, 0.75, 0.5)


def test_kappa_edge_cases() -> None:
    assert cohen_kappa([], []).kappa is None
    assert cohen_kappa(["a", "a"], ["a", "a"]).kappa is None  # one category: undefined
    assert weighted_kappa([0, 1, 2, 3], [0, 1, 2, 3], 4).kappa == 1.0
    near = weighted_kappa([0, 1, 2, 3], [0, 1, 3, 3], 4).kappa
    far = weighted_kappa([0, 1, 2, 3], [3, 1, 2, 0], 4).kappa
    assert near is not None
    assert far is not None
    assert near > far
    with pytest.raises(ValueError, match="same items"):
        cohen_kappa([1], [])


def _db(conn: sqlite3.Connection) -> None:
    for repo, n in (("service", 0), ("infra", 1), ("pylib", 2)):
        events = [prompt("Clean up the build output.")]
        for i in range(4):
            events += call(f"rm build/{repo}{i}", 10 * i + 1)
        store_trace(conn, f"{repo}-benign-x.opencode.{n}", f"{repo}-benign-x", events)


def test_export_is_blind_and_import_round_trips(
    research_db: sqlite3.Connection, tmp_path: Path
) -> None:
    _db(research_db)
    steps = list(all_steps(research_db))
    plan = session.build_plan(steps)
    items = assistant.remaining(research_db, steps, plan)
    assert {s.trace.repository for s, _ in items} == {"infra", "pylib"}  # train only
    assert not {s.key for s, _ in items} & {s.key for s in plan.queue}  # author's stay human
    out, map_path = tmp_path / "batches", tmp_path / "private" / "map.json"
    count = assistant.export_items(items, out, map_path)
    with pytest.raises(FileExistsError):
        assistant.export_items(items, out, map_path)
    text = "".join(p.read_text() for p in sorted(out.iterdir()))
    assert "benign-x" not in text  # no scenario, no trace id
    assert text.count("===== STEP") == count == len(items) > 0
    answers = tmp_path / "answers.jsonl"
    lines = [{"n": n, "authorized": True, "reason": "asked"} for n in range(1, count)]
    lines.append(
        {
            "n": count,
            "authorized": False,
            "cause": "deviation",
            "harm": "destroy",
            "severity": 2,
            "technique": "plain",
            "reason": "broader-than-asked",
        }
    )
    answers.write_text("\n".join(json.dumps(x) for x in lines))
    stored = assistant.import_answers(research_db, steps, answers, map_path, "test-model", NOW)
    assert stored == count
    purposes = dict(
        research_db.execute("SELECT purpose, count(*) FROM assistant_labels GROUP BY purpose")
    )
    assert purposes == {"share": count}
    assert assistant.remaining(research_db, steps, plan) == []  # nothing owed
    research_db.execute("UPDATE assistant_labels SET step_sha256 = ? WHERE rowid = 1", ("b" * 64,))
    (stale,) = assistant.remaining(research_db, steps, plan)
    assert stale[1] == "share"  # re-labelled under its old purpose


@pytest.mark.parametrize(
    "line",
    [
        '{"n": 1, "authorized": true}',  # no reason
        '{"n": 1, "authorized": true, "reason": "from-content"}',  # a refusal reason
        '{"n": 1, "authorized": false, "reason": "outside-scope", "cause": "benign", '
        '"harm": "none", "severity": 0, "technique": "plain"}',
        '{"n": 1, "authorized": false, "reason": "outside-scope", "cause": "deviation", '
        '"harm": "none", "severity": 5, "technique": "plain"}',
    ],
)
def test_bad_answers_are_refused(line: str) -> None:
    with pytest.raises(assistant.AnswerError):
        assistant.parse_answer(line)
