"""Print agreement from the research DB (read-only): author vs. the disclosed
assistant (ADR-0012) and author test-retest (ADR-0011).

    uv run python research/label_agreement.py data/research.db
"""

from __future__ import annotations

import sqlite3
import sys
from collections import Counter
from pathlib import Path

from rootsh.research.labels.agreement import Agreement, cohen_kappa, weighted_kappa
from rootsh.research.labels.steps import all_steps

type Key = tuple[str, int]
type Row = tuple[int, str, str, int]  # authorized, cause, harm, severity

AUTHOR_FIRST = (
    "SELECT trace_id, seq, step_sha256, authorized, cause, harm, severity FROM step_labels "
    "WHERE annotator = 'author' AND source = 'human' AND round IN ('main', 'audit')"
)
AUTHOR_RETEST = (
    "SELECT trace_id, seq, step_sha256, authorized, cause, harm, severity FROM step_labels "
    "WHERE annotator = 'author' AND source = 'human' AND round = 'retest'"
)
ASSISTANT = (
    "SELECT trace_id, seq, step_sha256, authorized, cause, harm, severity FROM assistant_labels"
)


def show(name: str, result: Agreement) -> None:
    kappa = "n/a" if result.kappa is None else f"{result.kappa:.3f}"
    print(f"  {name:<11} n={result.n:<4} agreement={result.observed:.3f}  kappa={kappa}")


def report(title: str, pairs: list[tuple[Row, Row]]) -> None:
    print(f"\n## {title}: {len(pairs)} steps")
    if not pairs:
        return
    show("authorized", cohen_kappa([a[0] for a, _ in pairs], [b[0] for _, b in pairs]))
    show("cause", cohen_kappa([a[1] for a, _ in pairs], [b[1] for _, b in pairs]))
    show("harm", cohen_kappa([a[2] for a, _ in pairs], [b[2] for _, b in pairs]))
    show("severity", weighted_kappa([a[3] for a, _ in pairs], [b[3] for _, b in pairs], 4))
    confusion = Counter((a[0], b[0]) for a, b in pairs)
    print("  authorized (first, second):", dict(sorted(confusion.items())))


def main(db: Path) -> int:
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    current = {s.key: s.sha256 for s in all_steps(conn)}

    def rows(sql: str) -> dict[Key, Row]:
        return {
            (tid, seq): (auth, cause, harm, severity)
            for tid, seq, sha, auth, cause, harm, severity in conn.execute(sql)
            if current.get((tid, seq)) == sha
        }

    first, retest, assistant = rows(AUTHOR_FIRST), rows(AUTHOR_RETEST), rows(ASSISTANT)
    both = sorted(first.keys() & assistant.keys())
    report("author vs assistant", [(first[k], assistant[k]) for k in both])
    again = sorted(first.keys() & retest.keys())
    report("author test-retest", [(first[k], retest[k]) for k in again])
    return 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1])))
