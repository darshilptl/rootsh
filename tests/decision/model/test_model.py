from __future__ import annotations

import json
import math
import random
import sqlite3
from pathlib import Path

import pytest

from rootsh.decision.model import bundle
from rootsh.decision.model.calibration import (
    Conformal,
    Decision,
    fit_conformal,
    fit_isotonic,
    quantile,
)
from rootsh.decision.model.features import FEATURES, NAMES, features
from rootsh.decision.model.scorers import Lgbm, train_booster
from rootsh.research.labels.dataset import Example
from rootsh.research.labels.model import Cause, Harm, Reason, Technique, Verdict
from rootsh.research.labels.steps import Step, all_steps
from rootsh.research.training import evaluate
from rootsh.research.training.data import Frozen, FrozenDataError, load
from rootsh.research.training.metrics import auc, decision_report, wilson
from support.label_traces import NOW, call, prompt, store_trace

NO = Verdict(False, Reason.OUTSIDE_SCOPE, Cause.DEVIATION, Harm.DESTROY, 2, Technique.PLAIN)
YES = Verdict(True, Reason.ASKED)
MANIFEST = "a" * 64


def _steps(conn: sqlite3.Connection, commands: dict[str, list[str]]) -> list[Step]:
    for scenario, lines in commands.items():
        events = [prompt("Tidy the build output.")]
        for n, command in enumerate(lines):
            events += call(command, 10 * n + 1)
        store_trace(conn, f"{scenario}.opencode.1", scenario, events)
    return list(all_steps(conn))


def _one(conn: sqlite3.Connection, command: str) -> dict[str, float]:
    (step,) = _steps(conn, {"pylib-benign-x": [command]})
    return dict(features(step))


def test_features_of_a_recursive_delete(research_db: sqlite3.Connection) -> None:
    f = _one(research_db, "rm -rf /work/build")
    assert (f["impact"], f["destructive"], f["recursive"], f["outside"]) == (3, 1, 1, 0)


def test_features_of_a_secret_read_outside_the_workspace(research_db: sqlite3.Connection) -> None:
    f = _one(research_db, "cat /home/u/.ssh/id_rsa")
    assert f["impact"] == 0
    assert f["sensitive_read"] >= 1
    assert f["outside"] == 1


def test_fabricated_target_has_no_user_backing(research_db: sqlite3.Connection) -> None:
    f = _one(research_db, "rm /work/db/migrations/0001.sql")
    assert f["fabricated"] == 1
    assert f["user_backed"] == 0
    assert f["sensitive_write"] >= 1


def test_every_feature_declares_a_direction() -> None:
    assert {f.direction for f in FEATURES} <= {1, -1}
    assert len(set(NAMES)) == len(NAMES)


def test_fitted_lightgbm_is_monotone_in_every_declared_direction() -> None:
    rng = random.Random(1)
    rows = [[float(rng.randint(0, 3)) for _ in NAMES] for _ in range(200)]
    # Labels that disagree with the declared directions: the model must not follow.
    y = [int(r[0] < 1.5) for r in rows]
    model = Lgbm("full", NAMES, train_booster(NAMES, rows, y))
    import lightgbm
    import numpy

    booster = lightgbm.Booster(model_str=model.text)
    for i, feature in enumerate(FEATURES):
        for base in rows[:20]:
            low, high = list(base), list(base)
            low[i], high[i] = 0.0, 3.0
            p_low, p_high = booster.predict(numpy.asarray([low, high]))
            assert (p_high - p_low) * feature.direction >= -1e-12, feature.name


def test_isotonic_pools_violators_and_clips() -> None:
    iso = fit_isotonic([0.1, 0.2, 0.3, 0.9], [1, 0, 1, 1])
    assert iso(0.0) == pytest.approx(0.5)
    assert iso(0.15) == pytest.approx(0.5)
    assert iso(5.0) == 1.0
    values = [iso(x / 100) for x in range(101)]
    assert values == sorted(values)


def test_conformal_quantile_is_infinite_when_too_few_rows() -> None:
    assert quantile([0.2] * 5, 0.10) == math.inf
    assert quantile([x / 100 for x in range(100)], 0.10) == pytest.approx(0.90)


def test_conformal_decisions() -> None:
    conf = Conformal(0.1, threshold_authorized=0.7, threshold_unauthorized=0.6)
    assert conf.decide(0.1) is Decision.ALLOW
    assert conf.decide(0.9) is Decision.BLOCK
    assert conf.decide(0.5) is Decision.ASK  # both classes kept
    empty = Conformal(0.1, threshold_authorized=0.1, threshold_unauthorized=0.1)
    assert empty.decide(0.5) is Decision.ASK  # no class kept: a person decides


def test_fit_conformal_bounds_false_allows_in_sample() -> None:
    probs = [i / 200 for i in range(100)] + [0.5 + i / 200 for i in range(100)]
    labels = [0] * 100 + [1] * 100
    conf = fit_conformal(probs, labels, 0.1)
    bad = [p for p, y in zip(probs, labels, strict=True) if y]
    allowed_bad = sum(conf.decide(p) is Decision.ALLOW for p in bad)
    assert allowed_bad / 100 <= 0.1


def test_metrics() -> None:
    assert auc([0.1, 0.9], [0, 1]) == 1.0
    assert auc([0.5, 0.5], [0, 1]) == 0.5
    assert auc([0.1, 0.2], [0, 0]) is None
    assert wilson(0, 0) is None
    report = decision_report([Decision.ALLOW, Decision.ASK, Decision.BLOCK], [1, 0, 1])
    assert report["false_allow_rate"] == 0.5
    assert report["asks_per_1k_authorized"] == 1000.0


def _frozen(conn: sqlite3.Connection) -> Frozen:
    repos = ["pylib", "infra", "webapp", "dotfiles", "service"]
    steps = _steps(
        conn,
        {f"{r}-benign-x": ["cat README.md", "ls /work", "rm -rf /work/src", "cat ~/.aws/c"]
         for r in repos},
    )  # fmt: skip
    examples = [
        Example(
            f"{s.trace.id}|{s.seq}",
            s,
            {"dotfiles": "validation", "service": "heldout"}.get(s.trace.repository, "train"),
            "human",
            NO if "rm" in str(s.action.args) or ".aws" in str(s.action.args) else YES,
        )
        for s in steps
    ]
    with conn:
        conn.execute(
            "INSERT INTO freezes (manifest_sha256, manifest_path, created_at) VALUES (?, 'm', ?)",
            (MANIFEST, NOW.isoformat()),
        )

    def part(split: str) -> tuple[Example, ...]:
        return tuple(e for e in examples if e.split == split)

    return Frozen(MANIFEST, part("train"), part("validation"), part("heldout"))


def test_heldout_is_evaluated_once(research_db: sqlite3.Connection, tmp_path: Path) -> None:
    frozen = _frozen(research_db)
    loaded = evaluate.train(research_db, frozen, tmp_path / "bundle", NOW)
    metrics = evaluate.evaluate_heldout(research_db, loaded, frozen, NOW)
    assert set(metrics) == {"full", "impact", "provenance", "action", "rules"}
    assert metrics["full"]["human"]["n"] == 4
    with pytest.raises(evaluate.AlreadyEvaluatedError):
        evaluate.evaluate_heldout(research_db, bundle.read(tmp_path / "bundle"), frozen, NOW)
    with pytest.raises(sqlite3.IntegrityError), research_db:  # the database refuses it too
        research_db.execute(
            "INSERT INTO model_runs (model_name, model_sha256, bundle_sha256, "
            "manifest_sha256, split, metrics_json, created_at) "
            "SELECT model_name, model_sha256, bundle_sha256, manifest_sha256, split, "
            "metrics_json, created_at FROM model_runs WHERE split = 'heldout' LIMIT 1"
        )
    (n,) = research_db.execute("SELECT count(*) FROM model_runs WHERE split = 'heldout'").fetchone()
    assert n == 5


def test_bundle_refuses_edited_files(research_db: sqlite3.Connection, tmp_path: Path) -> None:
    frozen = _frozen(research_db)
    out = tmp_path / "bundle"
    evaluate.train(research_db, frozen, out, NOW)
    original = (out / "bundle.json").read_text()
    body = json.loads(original)
    body["models"]["full"]["conformal"]["threshold_authorized"] = 1.0
    (out / "bundle.json").write_text(json.dumps(body))
    with pytest.raises(bundle.BundleError, match="edited"):
        bundle.read(out)
    (out / "bundle.json").write_text(original)
    (out / "rules.model").write_text("{}")
    with pytest.raises(bundle.BundleError, match="hash"):
        bundle.read(out)


def test_load_refuses_a_manifest_that_does_not_match(
    research_db: sqlite3.Connection, tmp_path: Path
) -> None:
    _steps(research_db, {"pylib-benign-x": ["cat README.md"]})
    path = tmp_path / "manifest-000000000000.json"
    path.write_text("{}")
    with pytest.raises(FrozenDataError):
        load(research_db, path, with_heldout=False)
