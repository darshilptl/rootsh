"""Tables for docs/phases/08-hooks-install.md, generated from the gate runs.

Usage: uv run python research/hook_gate_report.py
Reads data/gate/results-*.json and gate-*.db (written by run_gate.py).
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "data" / "gate"


def runs(name: str) -> list[dict[str, object]]:
    data: list[dict[str, object]] = json.loads((OUT / name).read_text())
    return data


def case_table(results: list[dict[str, object]]) -> str:
    lines = [
        "| agent | case | daemon | result | evidence | decisions (decision/layer) | s | USD |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        rows = r["decisions"]
        assert isinstance(rows, list)
        decided = ", ".join(f"{d['decision']}/{d['layer']}" for d in rows) or "none"
        cost = r["cost_usd"]
        usd = f"{cost:.4f}" if isinstance(cost, float) else "—"
        lines.append(
            f"| {r['agent']} | {r['case']} | {'up' if r['daemon'] else 'down'} | "
            f"{'PASS' if r['pass'] else 'FAIL'} | {r['detail']} | {decided} | "
            f"{r['seconds']} | {usd} |"
        )
    return "\n".join(lines)


def db_table() -> str:
    lines = ["| gate DB | decision | layer | channel | answer | n |", "|---|---|---|---|---|---|"]
    for db in sorted(OUT.glob("gate-*.db")):
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            for row in conn.execute(
                "SELECT decision, layer, channel, coalesce(answer, '—'), count(*) FROM decisions "
                "GROUP BY 1, 2, 3, 4 ORDER BY 1, 2"
            ):
                lines.append(f"| {db.name} | " + " | ".join(str(v) for v in row) + " |")
        finally:
            conn.close()
    return "\n".join(lines)


def main() -> None:
    first = runs("results-run1.json")
    final = runs("results-claude_code.json") + runs("results-opencode.json")
    passed = sum(1 for r in final if r["pass"])
    spent = sum(
        r["cost_usd"]
        for r in first + runs("results-claude_code.json")
        if isinstance(r["cost_usd"], float)
    )
    print("### Run 1 (Claude Code, original checks)\n")
    print(case_table(first))
    print(f"\n### Final runs: {passed}/{len(final)} pass\n")
    print(case_table(final))
    print("\n### Decisions in the gate DBs\n")
    print(db_table())
    print(f"\nClaude Code spend over both runs: ${spent:.4f}")


if __name__ == "__main__":
    main()
