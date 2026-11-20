"""The five scorers of design 06 §3: how each is fitted, scored and stored.

Each scorer turns steps into raw risk scores (higher = more likely not
authorized). LightGBM models are stored in LightGBM's own text format, the
others as JSON; nothing is pickled.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from rootsh.labels.audits import action_tokens
from rootsh.labels.steps import Step
from rootsh.model.features import NAMES, features, group, vector

SEED = 20261120
HASH_FEATURES = 2**18
LGBM_PARAMS: dict[str, object] = {
    "objective": "binary",
    "learning_rate": 0.05,
    "num_leaves": 4,
    "min_data_in_leaf": 3,
    "bagging_fraction": 1.0,
    "feature_fraction": 1.0,
    "monotone_constraints_method": "advanced",
    "deterministic": True,
    "force_col_wise": True,
    "seed": SEED,
    "num_threads": 1,
    "verbose": -1,
}
ROUNDS = 100


class Scorer(Protocol):
    @property
    def name(self) -> str: ...

    def scores(self, steps: Sequence[Step]) -> list[float]: ...

    def dump(self) -> str: ...


def _directions(names: Sequence[str]) -> list[int]:
    by_name = {f.name: f.direction for f in group("impact") + group("provenance")}
    return [by_name[n] for n in names]


@dataclass(frozen=True, slots=True)
class Lgbm:
    name: str
    names: tuple[str, ...]
    text: str  # LightGBM native model

    def scores(self, steps: Sequence[Step]) -> list[float]:
        return self.score_rows([vector(s, self.names) for s in steps])

    def score_rows(self, rows: Sequence[Sequence[float]]) -> list[float]:
        """Raw scores for plain feature rows, in the order of `self.names`."""
        import lightgbm
        import numpy

        if not rows:
            return []
        booster = lightgbm.Booster(model_str=self.text)
        return [float(p) for p in booster.predict(numpy.asarray(rows, dtype=float))]

    def dump(self) -> str:
        return self.text


def train_booster(names: Sequence[str], rows: Sequence[Sequence[float]], y: Sequence[int]) -> str:
    """A monotone LightGBM model on plain feature rows, as LightGBM text."""
    import lightgbm
    import numpy

    params = {**LGBM_PARAMS, "monotone_constraints": _directions(names)}
    data = lightgbm.Dataset(
        numpy.asarray(rows, dtype=float), label=list(y), feature_name=list(names),
        params={"verbose": -1},
    )  # fmt: skip
    return str(lightgbm.train(params, data, num_boost_round=ROUNDS).model_to_string())


def fit_lgbm(name: str, names: tuple[str, ...], steps: Sequence[Step], y: Sequence[int]) -> Lgbm:
    return Lgbm(name, names, train_booster(names, [vector(s, names) for s in steps], y))


@dataclass(frozen=True, slots=True)
class Rules:
    """No training: impact, plus one each for any sensitivity class and any
    opaqueness, plus the content-only argument count (design 06 §3)."""

    name: str = "rules"

    def scores(self, steps: Sequence[Step]) -> list[float]:
        out = []
        for step in steps:
            f = features(step)
            out.append(
                f["impact"]
                + min(f["sensitive_write"] + f["sensitive_read"], 1.0)
                + min(f["opaque"], 1.0)
                + f["content_only"]
            )
        return out

    def dump(self) -> str:
        return json.dumps({"rules": "impact + min(sensitive,1) + min(opaque,1) + content_only"})


def _hashed(steps: Sequence[Step]) -> object:
    from sklearn.feature_extraction import FeatureHasher

    hasher = FeatureHasher(n_features=HASH_FEATURES, input_type="string", alternate_sign=False)
    return hasher.transform([action_tokens(s) for s in steps])


@dataclass(frozen=True, slots=True)
class Action:
    """Logistic regression on hashed action tokens (the P5 action-only view)."""

    name: str
    intercept: float
    weights: dict[int, float]

    def scores(self, steps: Sequence[Step]) -> list[float]:
        import math

        if not steps:
            return []
        matrix = _hashed(steps).tocsr()  # type: ignore[attr-defined]
        out = []
        for i in range(matrix.shape[0]):
            row = matrix.getrow(i)
            z = self.intercept + sum(
                self.weights.get(int(j), 0.0) * float(v)
                for j, v in zip(row.indices, row.data, strict=True)
            )
            out.append(1.0 / (1.0 + math.exp(-z)))
        return out

    def dump(self) -> str:
        return json.dumps(
            {"intercept": self.intercept, "weights": {str(k): v for k, v in self.weights.items()}},
            sort_keys=True,
        )


def fit_action(steps: Sequence[Step], y: Sequence[int]) -> Action:
    from sklearn.linear_model import LogisticRegression

    if len(set(y)) < 2:  # a fold with one class: predict it, with no token weights
        return Action("action", 20.0 if y and y[0] == 1 else -20.0, {})
    model = LogisticRegression(max_iter=2000, random_state=SEED)
    model.fit(_hashed(steps), list(y))
    coef = model.coef_[0]
    weights = {int(j): float(coef[j]) for j in coef.nonzero()[0]}
    return Action("action", float(model.intercept_[0]), weights)


MODEL_NAMES = ("full", "impact", "provenance", "action", "rules")


def fit(name: str, steps: Sequence[Step], y: Sequence[int]) -> Scorer:
    if name == "full":
        return fit_lgbm(name, NAMES, steps, y)
    if name in ("impact", "provenance"):
        return fit_lgbm(name, tuple(f.name for f in group(name)), steps, y)
    if name == "action":
        return fit_action(steps, y)
    if name == "rules":
        return Rules()
    raise ValueError(f"unknown model {name!r}")


def load(name: str, text: str) -> Scorer:
    if name == "full":
        return Lgbm(name, NAMES, text)
    if name in ("impact", "provenance"):
        return Lgbm(name, tuple(f.name for f in group(name)), text)
    if name == "action":
        body = json.loads(text)
        return Action(name, body["intercept"], {int(k): v for k, v in body["weights"].items()})
    if name == "rules":
        return Rules()
    raise ValueError(f"unknown model {name!r}")
