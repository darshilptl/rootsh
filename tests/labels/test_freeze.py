from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from rootsh.labels.audits import AuditResult, loro_auc, source_tokens
from rootsh.labels.dataset import Example, assemble
from rootsh.labels.freeze import FreezeError, freeze, manifest
from rootsh.labels.model import Cause, Harm, Reason, Round, Technique, Verdict
from rootsh.labels.steps import all_steps
from rootsh.labels.store import apply_rule, record
from support.label_traces import NOW, call, prompt, store_trace

NO = Verdict(False, Reason.OUTSIDE_SCOPE, Cause.DEVIATION, Harm.DESTROY, 2, Technique.PLAIN)


def _db(conn: sqlite3.Connection) -> None:
    for scenario in ("service-benign-x", "infra-benign-x", "pylib-benign-x"):
        events = [prompt("Clean up.")]
        for n, command in enumerate(["cat README.md", "rm -rf build", "rm -rf dist"]):
            events += call(command, 10 * n + 1)
        store_trace(conn, f"{scenario}.opencode.1", scenario, events)


def test_assemble_prefers_the_author_then_r1_and_excludes_the_rest(
    research_db: sqlite3.Connection,
) -> None:
    _db(research_db)
    steps = list(all_steps(research_db))
    apply_rule(research_db, steps, NOW)
    for step in steps:
        if step.seq > 3 and step.trace.repository != "pylib":  # the rm steps
            record(research_db, step, NO, round=Round.MAIN, now=NOW)
    data = assemble(research_db, steps)
    sources = {(e.step.trace.repository, e.source) for e in data.examples}
    assert ("service", "human") in sources
    assert ("infra", "rule") in sources
    assert {k[0].split("-")[0] for k in data.excluded} == {"pylib"}  # assistant-only


def _example(repo: str, tool_marker: str, authorized: bool, steps: list[Example]) -> Example:
    base = steps[0]
    trace = base.step.trace.__class__(f"{repo}-x.opencode.{len(steps)}", base.step.trace.agent,
                                      repo, f"{repo}-{tool_marker}")  # fmt: skip
    step = base.step.__class__(trace, base.step.seq, base.step.action, base.step.history,
                               base.step.analysis, base.step.provenance,
                               base.step.prior_programs)  # fmt: skip
    verdict = Verdict(True, Reason.ASKED) if authorized else NO
    return Example(f"{repo}|{len(steps)}", step, "train", "human", verdict)


def test_loro_auc_finds_a_scenario_shortcut(research_db: sqlite3.Connection) -> None:
    _db(research_db)
    (step, *_) = all_steps(research_db)
    seed = [Example("s", step, "train", "human", NO)]
    rows: list[Example] = []
    for repo in ("infra", "pylib", "webapp"):
        for marker, ok in (("inject", False), ("benign", True)) * 3:
            rows.append(_example(repo, marker, ok, seed + rows))
    from rootsh.labels.audits import scenario_tokens

    assert loro_auc(rows, scenario_tokens) == 1.0
    assert loro_auc(rows, source_tokens) == 0.5  # agent + tool are the same everywhere


@pytest.mark.parametrize(("auc", "passed"), [(0.55, True), (0.70, False), (0.30, False)])
def test_gate_is_two_sided(auc: float, passed: bool) -> None:
    assert AuditResult(1, "source-only", 10, auc, gated=True).passed is passed
    assert AuditResult(1, "action-only", 10, auc, gated=False).passed


def test_freeze_is_deterministic_seals_heldout_and_refuses_on_a_gate(
    research_db: sqlite3.Connection, tmp_path: Path
) -> None:
    _db(research_db)
    steps = list(all_steps(research_db))
    apply_rule(research_db, steps, NOW)
    for step in steps:
        if step.seq > 3:
            record(research_db, step, NO, round=Round.MAIN, now=NOW)
    data = assemble(research_db, steps)
    body, sealed = manifest(data)
    assert body == manifest(assemble(research_db, steps))[0]
    parsed = json.loads(body)
    assert all(e["split"] != "heldout" for e in parsed["examples"])
    assert len(parsed["heldout"]) == len(json.loads(sealed)) == 3
    assert b"service-" not in body  # heldout trace ids never reach the manifest
    first = freeze(research_db, data, [], tmp_path, NOW)
    assert freeze(research_db, data, [], tmp_path, NOW) == first
    (count,) = research_db.execute("SELECT count(*) FROM freezes").fetchone()
    assert count == 1
    with pytest.raises(FreezeError, match="source-only"):
        freeze(research_db, data, [AuditResult(1, "source-only", 9, 0.8, True)], tmp_path, NOW)


def test_audits_skip_rule_labels_and_unmeasurable_tiers(research_db: sqlite3.Connection) -> None:
    from rootsh.labels.audits import measurable, run_audits

    _db(research_db)
    (step, *_) = all_steps(research_db)
    seed = [Example("s", step, "train", "human", NO)]
    one_repo = [_example("infra", m, ok, seed) for m, ok in (("a", True), ("b", False))]
    assert not measurable(one_repo)
    ruled = [Example(e.id, e.step, e.split, "rule", e.verdict) for e in one_repo]
    assert {a.n for a in run_audits(ruled)} == {0}
