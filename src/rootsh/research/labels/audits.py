"""Shortcut audits and the diversity report (design 05 §9).

Each audit fits the same fixed classifier, logistic regression on hashed
token counts, to one narrow view of a step and predicts `authorized`
leave-one-repository-out over the non-heldout repositories. A view that
predicts the label well (AUC above `GATE`) is a shortcut a model could learn
instead of the action's meaning. AUC is computed on the pooled out-of-fold
scores, since a single repository may hold one class only. The gate is
two-sided (ADR-0014): an AUC far below 0.5 out of fold means the view
predicts the label in reverse across repositories, which is as much a
shortcut, so `max(auc, 1 - auc)` must stay at or below `GATE`.
"""

from __future__ import annotations

import posixpath
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from rootsh.analysis.events import EventKind
from rootsh.decision.model.tokens import action_tokens, tokens, tool_name
from rootsh.research.labels.dataset import TRAINING_SOURCES, Example
from rootsh.research.labels.steps import Step

GATE = 0.60


def prompt_tokens(step: Step) -> list[str]:
    return tokens(" ".join(e.text or "" for e in step.history if e.kind is EventKind.PROMPT))


def source_tokens(step: Step) -> list[str]:
    return [step.trace.agent.value, tool_name(step)]


def scenario_tokens(step: Step) -> list[str]:
    return [step.trace.repository, *step.trace.scenario.split("-")]


def file_name_tokens(step: Step) -> list[str]:
    names = {posixpath.basename(e.target.rstrip("/")) for e in step.analysis.effects if e.target}
    return [t for name in sorted(names) for t in tokens(name)] or ["<none>"]


View = Callable[[Step], list[str]]
VIEWS: dict[str, View] = {
    "action-only": action_tokens,
    "prompt-only": prompt_tokens,
    "source-only": source_tokens,
    "task/scenario-ID": scenario_tokens,
    "file-name": file_name_tokens,
}
GATED = {
    1: frozenset({"source-only", "task/scenario-ID", "file-name"}),
    2: frozenset(VIEWS),
}


@dataclass(frozen=True, slots=True)
class AuditResult:
    tier: int
    view: str
    n: int
    auc: float | None  # None when only one class is present
    gated: bool

    @property
    def passed(self) -> bool:
        return not self.gated or self.auc is None or max(self.auc, 1 - self.auc) <= GATE


def loro_auc(examples: Sequence[Example], view: View) -> float | None:
    """Pooled out-of-fold AUC, leaving one repository out at a time."""
    from sklearn.feature_extraction import FeatureHasher
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import roc_auc_score

    labels = [int(not e.verdict.authorized) for e in examples]
    if len(set(labels)) < 2:
        return None
    hasher = FeatureHasher(n_features=2**18, input_type="string", alternate_sign=False)
    features = hasher.transform([view(e.step) for e in examples])
    repos = [e.step.trace.repository for e in examples]
    scores = [0.0] * len(examples)
    for held in sorted(set(repos)):
        train = [i for i, r in enumerate(repos) if r != held]
        test = [i for i, r in enumerate(repos) if r == held]
        train_labels = [labels[i] for i in train]
        if len(set(train_labels)) < 2:
            prior = float(train_labels[0]) if train_labels else 0.5
            for i in test:
                scores[i] = prior
            continue
        model = LogisticRegression(max_iter=2000)
        model.fit(features[train], train_labels)
        for i, p in zip(test, model.predict_proba(features[test])[:, 1], strict=True):
            scores[i] = float(p)
    return float(roc_auc_score(labels, scores))


def measurable(examples: Sequence[Example]) -> bool:
    """Leave-one-repository-out says something only when at least two
    repositories hold both classes (ADR-0014)."""
    classes: dict[str, set[bool]] = {}
    for e in examples:
        classes.setdefault(e.step.trace.repository, set()).add(e.verdict.authorized)
    return sum(len(c) == 2 for c in classes.values()) >= 2


def run_audits(examples: Sequence[Example]) -> list[AuditResult]:
    """Audits on what trains (ADR-0014): tier 1 the human-labelled real steps,
    tier 2 the kept variants. R1 labels and heldout never enter. A tier that
    is not `measurable` reports no AUC."""
    trains = [e for e in examples if e.split != "heldout" and e.source in TRAINING_SOURCES]
    tiers = {
        1: [e for e in trains if not e.source.startswith("G")],
        2: [e for e in trains if e.source.startswith("G")],
    }
    return [
        AuditResult(
            tier,
            name,
            len(rows),
            loro_auc(rows, view) if measurable(rows) else None,
            name in GATED[tier],
        )
        for tier, rows in tiers.items()
        for name, view in VIEWS.items()
    ]


@dataclass(frozen=True, slots=True)
class Diversity:
    tier: int
    split: str
    n: int
    unique_prompts: int
    unique_actions: int
    trigram_overlap_with_train: float | None  # share of this split's action 3-grams seen in train


def _trigrams(examples: Sequence[Example]) -> set[tuple[str, ...]]:
    grams: set[tuple[str, ...]] = set()
    for e in examples:
        words = action_tokens(e.step)
        grams |= {tuple(words[i : i + 3]) for i in range(len(words) - 2)}
    return grams


def diversity(examples: Sequence[Example]) -> list[Diversity]:
    out = []
    for tier in (1, 2):
        rows = [
            e
            for e in examples
            if e.source in TRAINING_SOURCES and e.source.startswith("G") == (tier == 2)
        ]
        train = _trigrams([e for e in rows if e.split == "train"])
        for split in ("train", "validation"):
            mine = [e for e in rows if e.split == split]
            grams = _trigrams(mine)
            overlap = len(grams & train) / len(grams) if grams and split != "train" else None
            out.append(
                Diversity(
                    tier,
                    split,
                    len(mine),
                    len({" ".join(prompt_tokens(e.step)) for e in mine}),
                    len({" ".join(action_tokens(e.step)) for e in mine}),
                    overlap,
                )
            )
    return out
