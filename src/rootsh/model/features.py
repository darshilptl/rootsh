"""One explicit feature vector per step (docs/design/06-model.md §2).

Every feature has a declared monotone direction: +1 means the risk score may
only rise with it, -1 only fall. The model is constrained to these, so no
learned shortcut can make a more destructive or less user-backed action look
safer.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from rootsh.collect.sandbox import WORKDIR
from rootsh.collect.store import SANITIZED_HOME
from rootsh.effects import Effect, EffectKind
from rootsh.hoststate.sensitivity import classify
from rootsh.labels.steps import Step
from rootsh.provenance import Source

# Paths an agent may touch without leaving its task (the sandbox maps both).
WORKSPACE_ROOTS = (WORKDIR, "/tmp")  # noqa: S108 - a path compared, never opened
_DESTRUCTIVE_WRITES = frozenset({"truncate", "discard-changes"})
_LEVEL_2_WRITES = frozenset({"truncate", "discard-changes", "config"})
_EGRESS_FREE = frozenset({"fetch"})
DESTRUCTIVE_CAP = 10
OPAQUE_CAP = 3


@dataclass(frozen=True, slots=True)
class Feature:
    name: str
    direction: int  # +1 or -1
    group: str  # "impact" | "provenance"


FEATURES: tuple[Feature, ...] = (
    Feature("impact", 1, "impact"),
    Feature("destructive", 1, "impact"),
    Feature("recursive", 1, "impact"),
    Feature("opaque", 1, "impact"),
    Feature("sensitive_write", 1, "impact"),
    Feature("sensitive_read", 1, "impact"),
    Feature("outside", 1, "impact"),
    Feature("fabricated", 1, "provenance"),
    Feature("content_only", 1, "provenance"),
    Feature("user_backed", -1, "provenance"),
)
NAMES: tuple[str, ...] = tuple(f.name for f in FEATURES)


def group(name: str) -> tuple[Feature, ...]:
    return tuple(f for f in FEATURES if f.group == name)


def effect_level(effect: Effect) -> int:
    kind = effect.kind
    if kind is EffectKind.READ:
        return 0
    if kind is EffectKind.WRITE:
        return 2 if effect.detail in _LEVEL_2_WRITES else 1
    if kind is EffectKind.NETWORK:
        return 0 if effect.detail in _EGRESS_FREE else 3
    if kind in (EffectKind.DELETE, EffectKind.PRIVILEGE):
        return 3
    return 2  # move, permission, exec, process


def _destructive(effect: Effect) -> bool:
    return effect.kind in (EffectKind.DELETE, EffectKind.MOVE) or (
        effect.kind is EffectKind.WRITE and effect.detail in _DESTRUCTIVE_WRITES
    )


def _paths(effect: Effect) -> list[str]:
    paths = [effect.target, *([effect.dest] if effect.dest else [])]
    return [p for p in paths if p.startswith("/")]


def _inside_workspace(path: str) -> bool:
    return any(path == root or path.startswith(root + "/") for root in WORKSPACE_ROOTS)


def _classes(effects: list[Effect]) -> set[str]:
    return {c for e in effects for p in _paths(e) for c in classify(p, SANITIZED_HOME)}


def features(step: Step) -> Mapping[str, float]:
    effects = list(step.analysis.effects)
    reads = [e for e in effects if e.kind is EffectKind.READ]
    others = [e for e in effects if e.kind is not EffectKind.READ]
    arguments = [a for a in step.provenance.arguments if a.kind is not EffectKind.READ]
    user = sum(Source.USER in a.best for a in arguments)
    return {
        "impact": float(max((effect_level(e) for e in effects), default=0)),
        "destructive": float(min(sum(_destructive(e) for e in effects), DESTRUCTIVE_CAP)),
        "recursive": float(any(_destructive(e) and (e.recursive or e.glob) for e in effects)),
        "opaque": float(min(len(step.analysis.opaque), OPAQUE_CAP)),
        "sensitive_write": float(len(_classes(others))),
        "sensitive_read": float(len(_classes(reads))),
        "outside": float(len({p for e in effects for p in _paths(e) if not _inside_workspace(p)})),
        "fabricated": float(sum(a.fabricated for a in arguments)),
        "content_only": float(sum(a.content_only for a in arguments)),
        "user_backed": user / len(arguments) if arguments else 0.0,
    }


def vector(step: Step, names: tuple[str, ...] = NAMES) -> list[float]:
    values = features(step)
    return [values[n] for n in names]
