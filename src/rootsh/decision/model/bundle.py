"""Read and verify a model bundle (docs/phases/06-model.md §6). Fitting and
writing a bundle is training code: `rootsh.research.training.fit`.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from rootsh.decision.model.calibration import Conformal, Isotonic
from rootsh.decision.model.scorers import MODEL_NAMES, Scorer, load

if TYPE_CHECKING:
    from rootsh.research.labels.dataset import Example


class BundleError(RuntimeError):
    """A bundle file is missing or does not match its recorded hash."""


@dataclass(frozen=True, slots=True)
class Fitted:
    scorer: Scorer
    isotonic: Isotonic
    conformal: Conformal

    def probabilities(self, examples: Sequence[Example]) -> list[float]:
        return [self.isotonic(s) for s in self.scorer.scores([e.step for e in examples])]


def model_sha256(spec: dict[str, object]) -> str:
    """Hash of the whole decision function: scorer file, calibration, thresholds."""
    return hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest()


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
