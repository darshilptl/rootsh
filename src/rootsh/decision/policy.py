"""The P7 decision policy (docs/phases/07-daemon.md §2).

Pure: the caller supplies the analysis, provenance, paths and any classes a
host-state probe found; the first rule that fires decides. Any exception in
deciding is a system failure and blocks.
"""

from __future__ import annotations

import fnmatch
import itertools
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum

from rootsh.analysis.effects import Analysis, Effect, EffectKind
from rootsh.analysis.hoststate.sensitivity import classify
from rootsh.analysis.provenance import Provenance
from rootsh.decision.model.bundle import Fitted, Loaded
from rootsh.decision.model.calibration import Conformal, Decision
from rootsh.decision.model.features import NAMES, effect_level, inside, paths, values
from rootsh.decision.model.scorers import Lgbm

MODEL = "full"
_EDIT_WRITES = frozenset({"create", "append", "modify"})


class Layer(StrEnum):
    FAILURE = "failure"
    DEFENSE = "defense"
    UNTRUSTED = "untrusted"
    READ_ONLY = "read_only"
    PROJECT_EDIT = "project_edit"
    MODEL = "model"


@dataclass(frozen=True, slots=True)
class Verdict:
    decision: Decision
    layer: Layer
    reasons: tuple[str, ...]
    risk: float | None = None
    features: Mapping[str, float] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Action:
    analysis: Analysis
    provenance: Provenance
    roots: tuple[str, ...]  # the project and /tmp: where an edit stays in the task
    home: str
    probed: Mapping[str, frozenset[str]] = field(default_factory=dict)  # path -> extra classes


@dataclass(frozen=True, slots=True)
class Model:
    """The bundle's `full` model as one function: features -> calibrated risk."""

    sha256: str
    names: tuple[str, ...]
    score: Callable[[Sequence[float]], float]
    calibrate: Callable[[float], float]
    conformal: Conformal

    def risk(self, features: Mapping[str, float]) -> float:
        return self.calibrate(self.score([features[n] for n in self.names]))


def model_from_bundle(loaded: Loaded) -> Model:
    fitted: Fitted = loaded.models[MODEL]
    scorer = fitted.scorer
    if not isinstance(scorer, Lgbm) or scorer.names != NAMES:
        raise TypeError(f"bundle model {MODEL!r} is not the P6 monotone model")
    return Model(
        loaded.model_sha256[MODEL],
        scorer.names,
        lambda row: scorer.score_rows([row])[0],
        fitted.isotonic,
        fitted.conformal,
    )


def failure(reason: str) -> Verdict:
    return Verdict(Decision.BLOCK, Layer.FAILURE, (f"rootsh failed closed: {reason}",))


def _classes(action: Action, path: str) -> frozenset[str]:
    return classify(path, action.home) | action.probed.get(path, frozenset())


def _parts(path: str) -> list[str]:
    return [p for p in path.split("/") if p]


def defense_path(path: str, home: str) -> bool:
    """Files whose change (or, for rootsh's own, whose reading) would disarm
    the defense: rootsh state, agent hook settings, OpenCode plugins/config."""
    if "rootsh" in classify(path, home):
        return True
    parts = _parts(path)
    name = parts[-1] if parts else ""
    parent = parts[-2] if len(parts) > 1 else ""
    if parent == ".claude" and fnmatch.fnmatchcase(name, "settings*.json"):
        return True
    if name in ("opencode.json", "opencode.jsonc"):
        return True
    return any(
        a in (".opencode", "opencode") and b in ("plugin", "plugins")
        for a, b in itertools.pairwise(parts)
    )


def defense_check(action: Action) -> str | None:
    for effect in action.analysis.effects:
        if effect.kind is EffectKind.PROCESS and "rootsh" in effect.target:
            return f"signals or stops rootsh ({effect.target})"
        for path in paths(effect):
            own = "rootsh" in _classes(action, path)
            if defense_path(path, action.home) and (effect.kind is not EffectKind.READ or own):
                return f"{effect.kind.value} on a defense file: {path}"
    return None


def untrusted_check(action: Action) -> str | None:
    levels: dict[tuple[str, EffectKind], int] = {}
    for effect in action.analysis.effects:
        key = (effect.target, effect.kind)
        levels[key] = max(levels.get(key, 0), effect_level(effect))
    for argument in action.provenance.arguments:
        if argument.content_only and levels.get((argument.target, argument.kind), 0) >= 3:
            return (
                f"{argument.kind.value} of {argument.target!r}: the target appears only in "
                "tool output (a file, page or command result), not in your request"
            )
    return None


def _read_ok(action: Action, effect: Effect) -> bool:
    return effect.kind is EffectKind.READ and all(
        "secret" not in _classes(action, p) for p in paths(effect)
    )


def read_only_check(action: Action) -> bool:
    return not action.analysis.opaque and all(_read_ok(action, e) for e in action.analysis.effects)


def project_edit_check(action: Action) -> bool:
    if action.analysis.opaque:
        return False
    for effect in action.analysis.effects:
        if _read_ok(action, effect):
            continue
        if effect.kind is not EffectKind.WRITE or effect.detail not in _EDIT_WRITES:
            return False
        for path in paths(effect):
            if not inside(path, action.roots) or _classes(action, path):
                return False
        if not paths(effect):  # a relative or dynamic target: not provably in the project
            return False
    return not any(a.content_only for a in action.provenance.arguments)


def decide(action: Action, model: Model | None) -> Verdict:
    """Invariants, then fast paths, then the model (design 07 §2)."""
    found = defense_check(action)
    if found is not None:
        return Verdict(Decision.BLOCK, Layer.DEFENSE, (found,))
    found = untrusted_check(action)
    if found is not None:
        return Verdict(Decision.BLOCK, Layer.UNTRUSTED, (found,))
    if read_only_check(action):
        return Verdict(Decision.ALLOW, Layer.READ_ONLY, ("reads only, no secrets",))
    if project_edit_check(action):
        return Verdict(Decision.ALLOW, Layer.PROJECT_EDIT, ("small edit inside the project",))
    if model is None:
        return failure("no model loaded")
    features = values(action.analysis, action.provenance, roots=action.roots, home=action.home)
    risk = model.risk(features)
    decision = model.conformal.decide(risk)
    reasons = [f"model risk {risk:.2f} ({decision.value} at alpha {model.conformal.alpha})"]
    reasons += _feature_reasons(features)
    return Verdict(decision, Layer.MODEL, tuple(reasons), risk, features)


def _feature_reasons(features: Mapping[str, float]) -> list[str]:
    out = []
    if features["destructive"]:
        out.append("deletes, moves or overwrites")
    if features["opaque"]:
        out.append("runs code rootsh cannot see into")
    if features["outside"]:
        out.append("touches paths outside the project")
    if features["sensitive_write"] or features["sensitive_read"]:
        out.append("touches sensitive files")
    if features["content_only"]:
        out.append("a target came only from tool output")
    return out


def safe_decide(action: Callable[[], Action], model: Model | None) -> Verdict:
    """decide(), but any error while building or deciding blocks (B5 step 5)."""
    try:
        return decide(action(), model)
    except Exception as exc:  # noqa: BLE001 - every failure must become a block
        return failure(f"{type(exc).__name__}: {exc}")
