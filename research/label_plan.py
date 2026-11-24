"""Print the P5 labelling plan from the research DB (read-only).

uv run python research/label_plan.py data/research.db
"""

from __future__ import annotations

import sqlite3
import sys
from collections import Counter
from pathlib import Path

from rootsh.research.labels.rule import r1_applies
from rootsh.research.labels.session import assistant_steps, build_plan
from rootsh.research.labels.splits import split_of
from rootsh.research.labels.steps import all_steps


def main(db: Path) -> int:
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    steps = list(all_steps(conn))
    plan = build_plan(steps)
    split = Counter((s.trace.agent.value, "R1" if r1_applies(s) else "hand") for s in steps)
    print(f"steps: {len(steps)}")
    for (agent, kind), n in sorted(split.items()):
        print(f"  {agent:<12} {kind:<5} {n}")
    queue = Counter(split_of(s.trace.repository) for s in plan.queue)
    print(f"author queue (ADR-0013): {len(plan.queue)} {dict(sorted(queue.items()))}")
    print(f"  R1 audit: {len(plan.audit)}")
    print(f"assistant train steps: {len(assistant_steps(steps, plan))}")
    return 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1])))
