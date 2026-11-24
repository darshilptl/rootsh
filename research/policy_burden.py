"""P7 policy burden on the frozen validation split (design 07 §8 item 4).

Every number is computed here from the frozen dataset and the P6 bundle;
heldout is not loaded. Run on a copy of the research DB:

  cp data/research.db "$SCRATCH/research.db"
  uv run python research/policy_burden.py "$SCRATCH/research.db" \
      data/freeze/manifest-a357bba6c02c.json data/model
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

from rootsh.decision.model.bundle import read
from rootsh.decision.model.features import WORKSPACE_ROOTS, values
from rootsh.decision.policy import Action, Layer, decide, model_from_bundle
from rootsh.research.collect.store import SANITIZED_HOME
from rootsh.research.training.data import load, target
from rootsh.storage.db import DatabaseKind, open_database

LABELS = {0: "authorized", 1: "refused"}


def main(db: str, manifest: str, bundle: str) -> None:
    conn = open_database(Path(db), DatabaseKind.RESEARCH, create=False)
    frozen = load(conn, Path(manifest), with_heldout=False)
    loaded = read(Path(bundle))
    model = model_from_bundle(loaded)
    by_layer: Counter[tuple[str, str, str]] = Counter()
    model_only: Counter[tuple[str, str]] = Counter()
    for example in frozen.validation:
        step, y = example.step, LABELS[target(example)]
        action = Action(step.analysis, step.provenance, WORKSPACE_ROOTS, SANITIZED_HOME)
        verdict = decide(action, model)
        by_layer[(verdict.layer.value, verdict.decision.value, y)] += 1
        f = values(step.analysis, step.provenance, roots=WORKSPACE_ROOTS, home=SANITIZED_HOME)
        model_only[(model.conformal.decide(model.risk(f)).value, y)] += 1
    n = Counter(LABELS[target(e)] for e in frozen.validation)
    print(
        f"manifest {frozen.manifest_sha256[:12]}, bundle {loaded.bundle_sha256[:12]}, "
        f"model full {model.sha256[:12]}; validation rows: {dict(n)}"
    )
    print("\n| layer | decision | authorized | refused |\n|---|---|---|---|")
    for layer in Layer:
        for decision in ("allow", "ask", "block"):
            a = by_layer[(layer.value, decision, "authorized")]
            r = by_layer[(layer.value, decision, "refused")]
            if a or r:
                print(f"| {layer.value} | {decision} | {a} | {r} |")
    print(
        "\n| policy | allow auth | ask auth | block auth | allow refused (false allow) "
        "| ask refused | block refused | asks per 1k authorized |"
    )
    print("|---|---|---|---|---|---|---|---|")

    def row(name: str, get: dict[tuple[str, str], int]) -> None:
        cells = [
            get.get((d, y), 0) for y in ("authorized", "refused") for d in ("allow", "ask", "block")
        ]
        per_k = round(1000 * cells[1] / n["authorized"]) if n["authorized"] else 0
        print(f"| {name} | " + " | ".join(map(str, cells)) + f" | {per_k} |")

    totals: Counter[tuple[str, str]] = Counter()
    for (_layer, d, y), c in by_layer.items():
        totals[(d, y)] += c
    row("P7 policy", dict(totals))
    row("model alone (P6)", dict(model_only))
    conn.close()


if __name__ == "__main__":
    main(*sys.argv[1:4])
