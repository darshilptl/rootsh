"""Fit, calibrate and conformalize every model; write the bundle
(docs/phases/06-model.md §3-§6). `rootsh.decision.model.bundle.read` verifies it.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Sequence
from pathlib import Path

from rootsh.decision.model.bundle import Fitted, model_sha256
from rootsh.decision.model.calibration import ALPHA, fit_conformal, fit_isotonic
from rootsh.decision.model.scorers import fit
from rootsh.research.labels.dataset import Example
from rootsh.research.training.data import Frozen, target


def out_of_fold(name: str, examples: Sequence[Example]) -> list[float]:
    """Leave-one-repository-out raw scores on the training rows."""
    repos = [e.step.trace.repository for e in examples]
    scores = [0.0] * len(examples)
    for held in sorted(set(repos)):
        inside = [e for e, r in zip(examples, repos, strict=True) if r != held]
        outside = [i for i, r in enumerate(repos) if r == held]
        scorer = fit(name, [e.step for e in inside], [target(e) for e in inside])
        for i, s in zip(outside, scorer.scores([examples[i].step for i in outside]), strict=True):
            scores[i] = s
    return scores


def fit_model(name: str, frozen: Frozen, alpha: float = ALPHA) -> Fitted:
    y = [target(e) for e in frozen.train]
    isotonic = fit_isotonic(out_of_fold(name, frozen.train), y)
    scorer = fit(name, [e.step for e in frozen.train], y)
    raw = scorer.scores([e.step for e in frozen.validation])
    conformal = fit_conformal(
        [isotonic(s) for s in raw], [target(e) for e in frozen.validation], alpha
    )
    return Fitted(scorer, isotonic, conformal)


def _finite(x: float) -> float | None:
    return None if math.isinf(x) else x


def _spec(fitted: Fitted, file_sha: str) -> dict[str, object]:
    c = fitted.conformal
    return {
        "file_sha256": file_sha,
        "isotonic": {"xs": list(fitted.isotonic.xs), "ys": list(fitted.isotonic.ys)},
        "conformal": {
            "alpha": c.alpha,
            "threshold_authorized": _finite(c.threshold_authorized),
            "threshold_unauthorized": _finite(c.threshold_unauthorized),
        },
    }


def write(out: Path, manifest_sha256: str, models: dict[str, Fitted]) -> str:
    """Write the model files and bundle.json; return the bundle's SHA-256."""
    out.mkdir(parents=True, exist_ok=True)
    specs: dict[str, object] = {}
    for name, fitted in models.items():
        text = fitted.scorer.dump().encode()
        (out / f"{name}.model").write_bytes(text)
        spec = _spec(fitted, hashlib.sha256(text).hexdigest())
        specs[name] = {**spec, "model_sha256": model_sha256(spec)}
    body = json.dumps(
        {"manifest_sha256": manifest_sha256, "models": specs}, sort_keys=True, indent=1
    ).encode()
    (out / "bundle.json").write_bytes(body)
    return hashlib.sha256(body).hexdigest()
