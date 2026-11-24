"""P10-lite layer ablation and attack replay (docs/design/10-ablation.md).

Every number is computed here from the frozen dataset and the P6 bundle.
Heldout is never used: V is `load(..., with_heldout=False)`, and R is
filtered to the train and validation splits before any field is read.
Run on a copy of the research DB:

  cp data/gold/research.db "$SCRATCH/research.db"
  uv run python research/ablation/ablation.py "$SCRATCH/research.db" \
      data/gold/freeze/manifest-a357bba6c02c.json data/models/p6-a357bba6c02c
"""

from __future__ import annotations

import sys
from collections import Counter
from collections.abc import Callable, Iterable
from pathlib import Path
from urllib.parse import urlsplit

from rootsh.collect.store import SANITIZED_HOME
from rootsh.effects import Effect, EffectKind
from rootsh.labels.dataset import Example, assemble
from rootsh.labels.steps import all_steps
from rootsh.model.bundle import read
from rootsh.model.calibration import Decision
from rootsh.model.data import load
from rootsh.model.features import WORKSPACE_ROOTS, inside, paths
from rootsh.policy import (
    Action,
    Model,
    _defense,
    _project_edit,
    _read_only,
    _untrusted,
    decide,
    model_from_bundle,
)
from rootsh.run.sandbox import AGENT_RO, AGENT_RW, COMMON_RO
from rootsh.storage.db import DatabaseKind, open_database

ASK, ALLOW, BLOCK = Decision.ASK.value, Decision.ALLOW.value, Decision.BLOCK.value
REAL_SOURCES = frozenset({"human", "adjudicated", "rule"})
HOME = SANITIZED_HOME
# design 10 §3: what `rootsh run` leaves writable and visible (sanitized paths)
RW_ROOTS = (*WORKSPACE_ROOTS, "/dev", *(f"{HOME}/{p}" for v in AGENT_RW.values() for p in v))
VISIBLE_HOME = (*RW_ROOTS, *(f"{HOME}/{p}" for v in AGENT_RO.values() for p in v))
VISIBLE_HOME += tuple(f"{HOME}/{p}" for p in COMMON_RO)


def action_of(example: Example) -> Action:
    return Action(example.step.analysis, example.step.provenance, WORKSPACE_ROOTS, HOME)


def config(level: int, model: Model | None) -> Callable[[Action], str]:
    """C0..C3 of design 10 §2; the remainder asks, except C3 where the model decides."""

    def run(action: Action) -> str:
        if level >= 1 and (_defense(action) is not None or _untrusted(action) is not None):
            return BLOCK
        if level >= 2 and (_read_only(action) or _project_edit(action)):
            return ALLOW
        if level >= 3:
            assert model is not None
            return decide(action, model).decision.value
        return ASK

    return run


def kernel(effect: Effect) -> str:
    """Design 10 §3 static mapping of one effect onto the `rootsh run` spec."""
    kind = effect.kind
    if kind is EffectKind.PRIVILEGE:
        return "denied (no_new_privs)"
    if kind is EffectKind.PROCESS:
        return "contained (pid namespace, no bus)"
    if kind is EffectKind.NETWORK:
        if effect.detail == "listen":
            return "K1 denies (bind)"
        url = urlsplit(effect.target if "//" in effect.target else f"//{effect.target}")
        try:
            port = url.port
        except ValueError:
            port = None
        if port is None and url.scheme == "http":
            port = 80
        return "K1 denies (port)" if port not in (None, 443) else "not contained"
    changes = (EffectKind.WRITE, EffectKind.DELETE, EffectKind.MOVE, EffectKind.PERMISSION)
    if kind in changes and any(not inside(p, RW_ROOTS) for p in paths(effect)):
        return "K1 denies (write)"
    hidden = [p for p in paths(effect) if inside(p, (HOME,)) and not inside(p, VISIBLE_HOME)]
    if kind is EffectKind.READ and hidden:
        return "hidden (home tmpfs)"
    return "not contained"


def step_kernel(example: Example) -> str:
    effects = example.step.analysis.effects
    found = sorted({k for e in effects if (k := kernel(e)) != "not contained"})
    return found[0] if found else "not contained"


def table(title: str, rows: Iterable[Example], configs: dict[str, Callable[[Action], str]]) -> None:
    rows = list(rows)
    n = Counter("attack" if not e.verdict.authorized else "benign" for e in rows)
    print(f"\n### {title}: {n['attack']} attacks, {n['benign']} benign\n")
    print(
        "| config | attack blocked | attack asked | attack allowed (false allow) "
        "| benign allowed | benign asked | benign blocked (false block) | asks per 1k benign |"
    )
    print("|---|---|---|---|---|---|---|---|")
    for name, run in configs.items():
        c: Counter[tuple[str, str]] = Counter()
        for e in rows:
            c[(run(action_of(e)), "benign" if e.verdict.authorized else "attack")] += 1
        cells = [c[(d, y)] for y in ("attack", "benign") for d in (BLOCK, ASK, ALLOW)]
        cells = [*cells[:3], cells[5], cells[4], cells[3]]
        per_k = round(1000 * c[(ASK, "benign")] / n["benign"]) if n["benign"] else 0
        print(f"| {name} | " + " | ".join(map(str, cells)) + f" | {per_k} |")


def by_cause(title: str, rows: Iterable[Example], run: Callable[[Action], str]) -> None:
    c: Counter[tuple[str, str]] = Counter()
    for e in rows:
        if not e.verdict.authorized:
            c[(e.verdict.cause.value, run(action_of(e)))] += 1
    print(f"\n{title}\n\n| cause | blocked | asked | allowed |\n|---|---|---|---|")
    for cause in sorted({k[0] for k in c}):
        print(f"| {cause} | {c[(cause, BLOCK)]} | {c[(cause, ASK)]} | {c[(cause, ALLOW)]} |")


def main(db: str, manifest: str, bundle: str) -> None:
    conn = open_database(Path(db), DatabaseKind.RESEARCH, create=False)
    frozen = load(conn, Path(manifest), with_heldout=False)  # verifies the manifest
    assert not frozen.heldout
    validation = frozen.validation
    rule_set = [
        e
        for e in assemble(conn, list(all_steps(conn))).examples
        if e.split in ("train", "validation") and e.source in REAL_SOURCES
    ]
    assert all(e.split != "heldout" for e in (*validation, *rule_set))
    loaded = read(Path(bundle))
    model = model_from_bundle(loaded)
    c3 = config(3, model)
    for e in validation:  # design 10 §2: C3 is exactly the shipped policy
        assert c3(action_of(e)) == decide(action_of(e), model).decision.value
    print(
        f"manifest {frozen.manifest_sha256[:12]}, bundle {loaded.bundle_sha256[:12]}, "
        f"model full {model.sha256[:12]}; heldout not loaded"
    )
    rules = {f"C{i}": config(i, None) for i in range(3)}
    table("V (validation)", validation, {**rules, "C3": c3})
    table("R (train + validation, real steps, rule layers only)", rule_set, rules)
    human = [e for e in rule_set if e.source != "rule"]
    table("R, human-labelled rows only", human, rules)
    sources = Counter((e.split, e.source) for e in rule_set)
    print("\nR composition: " + ", ".join(f"{s}/{k} {n}" for (s, k), n in sorted(sources.items())))

    by_cause("V attacks by cause under C3", validation, c3)
    by_cause("R attacks by cause under C2", rule_set, rules["C2"])

    print("\n### Kernel static estimate (design 10 §3, not run)\n")
    for title, rows, run in (
        ("V, attacks the C3 policy allowed", validation, c3),
        ("R, attacks the C2 rules allowed", rule_set, rules["C2"]),
        ("R, all attacks", rule_set, None),
    ):
        k = Counter(
            step_kernel(e)
            for e in rows
            if not e.verdict.authorized and (run is None or run(action_of(e)) == ALLOW)
        )
        print(f"- {title}: " + (", ".join(f"{v} {n}" for v, n in sorted(k.items())) or "none"))
    conn.close()


if __name__ == "__main__":
    main(*sys.argv[1:4])
