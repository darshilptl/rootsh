"""Fit, calibrate and conformalize every model; write and verify the bundle
(docs/design/06-model.md §3-§6).
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from rootsh.labels.dataset import Example
from rootsh.model.calibration import ALPHA, Conformal, Isotonic, fit_conformal, fit_isotonic
from rootsh.model.data import Frozen, target
from rootsh.model.scorers import MODEL_NAMES, Scorer, fit, load


class BundleError(RuntimeError):
    """A bundle file is missing or does not match its recorded hash."""


@dataclass(frozen=True, slots=True)
class Fitted:
    scorer: Scorer
    isotonic: Isotonic
    conformal: Conformal

    def probabilities(self, examples: Sequence[Example]) -> list[float]:
        return [self.isotonic(s) for s in self.scorer.scores([e.step for e in examples])]


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


def model_sha256(spec: dict[str, object]) -> str:
    """Hash of the whole decision function: scorer file, calibration, thresholds."""
    return hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest()


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


@dataclass(frozen=True, slots=True)
class Loaded:
    bundle_sha256: str
    manifest_sha256: str
    models: dict[str, Fitted]
    model_sha256: dict[str, str]


def _threshold(value: object) -> float:
    return math.inf if value is None else float(value)  # type: ignore[arg-type]


def read(out: Path) -> Loaded:
    """Load a bundle, refusing any file whose hash does not match."""
    body = (out / "bundle.json").read_bytes()
    bundle = json.loads(body)
    models: dict[str, Fitted] = {}
    hashes: dict[str, str] = {}
    for name in MODEL_NAMES:
        spec = bundle["models"][name]
        text = (out / f"{name}.model").read_bytes()
        if hashlib.sha256(text).hexdigest() != spec["file_sha256"]:
            raise BundleError(f"{name}.model does not match its recorded hash")
        recorded = spec.pop("model_sha256")
        if model_sha256(spec) != recorded:
            raise BundleError(f"{name}: calibration or thresholds were edited")
        conf = spec["conformal"]
        models[name] = Fitted(
            load(name, text.decode()),
            Isotonic(tuple(spec["isotonic"]["xs"]), tuple(spec["isotonic"]["ys"])),
            Conformal(
                conf["alpha"],
                _threshold(conf["threshold_authorized"]),
                _threshold(conf["threshold_unauthorized"]),
            ),
        )
        hashes[name] = recorded
    return Loaded(hashlib.sha256(body).hexdigest(), bundle["manifest_sha256"], models, hashes)
