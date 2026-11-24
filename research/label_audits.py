"""Print the variant audit, shortcut audits and diversity (read-only).

uv run python research/label_audits.py data/research.db
"""

from __future__ import annotations

import sqlite3
import sys
from collections import Counter
from pathlib import Path

from rootsh.research.labels.audits import diversity, run_audits
from rootsh.research.labels.dataset import assemble
from rootsh.research.labels.freeze import gate_failures
from rootsh.research.labels.steps import all_steps


def main(db: Path) -> int:
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    data = assemble(conn, list(all_steps(conn)))
    print("## variant audit")
    for g in data.generators:
        rate = g.disagree / g.audited if g.audited else float("nan")
        print(f"  {g.generator}: audited {g.audited}, disagree {g.disagree} ({rate:.1%}), "
              f"{'kept' if g.kept else 'DROPPED'}")  # fmt: skip
    print("## examples (split, source, authorized)")
    for key, n in sorted(Counter((e.split, e.source, e.verdict.authorized)
                                 for e in data.examples).items()):  # fmt: skip
        print(f"  {key}: {n}")
    print(f"  excluded (assistant only): {len(data.excluded)}")
    print("## shortcut audits (leave-one-repository-out AUC)")
    audits = run_audits(data.examples)
    for a in audits:
        auc = "n/a" if a.auc is None else f"{a.auc:.3f}"
        mark = (
            "not measurable"
            if a.auc is None
            else ("pass" if a.passed else "FAIL")
            if a.gated
            else "report"
        )
        print(f"  tier {a.tier} {a.view:<17} n={a.n:<5} AUC={auc:<6} {mark}")
    print("## diversity (action 3-gram overlap with train)")
    for d in diversity(data.examples):
        seen = d.trigram_overlap_with_train
        overlap = "-" if seen is None else f"{seen:.3f}"
        print(f"  tier {d.tier} {d.split:<10} n={d.n:<5} prompts={d.unique_prompts:<4} "
              f"actions={d.unique_actions:<5} overlap={overlap}")  # fmt: skip
    print("## gate failures:", gate_failures(conn, data, audits) or "none")
    return 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1])))
