"""The disclosed assistant's labelling round (ADR-0012).

    rest:   uv run python research/labels/assistant_round.py rest DB BATCH_DIR MAP
    import: uv run python research/labels/assistant_round.py import DB ANSWERS MAP MODEL

`rest` (ADR-0013) exports every training step the assistant still owes, plus
its stale labels, to a new batch dir and a new map. rest reads the DB
read-only and refuses to overwrite an existing map. The map (number -> trace id, which names the
scenario) must stay out of the labeller's sight; keep it under data/.
"""

from __future__ import annotations

import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

from rootsh.labels import assistant
from rootsh.labels.session import build_plan
from rootsh.labels.steps import all_steps
from rootsh.storage.db import DatabaseKind, open_database


def main(argv: list[str]) -> int:
    command, db = argv[0], Path(argv[1])
    if command == "rest":
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        steps = list(all_steps(conn))
        items = assistant.remaining(conn, steps, build_plan(steps))
        count = assistant.export_items(items, Path(argv[2]), Path(argv[3]))
        labelled = set(conn.execute("SELECT trace_id, seq FROM assistant_labels"))
        stale = sum(1 for step, _ in items if step.key in labelled)
        print(f"exported {count} steps ({stale} stale re-labels)")
        return 0
    if command == "import":
        conn = open_database(db, DatabaseKind.RESEARCH, create=False)
        steps = list(all_steps(conn))
        count = assistant.import_answers(
            conn, steps, Path(argv[2]), Path(argv[3]), argv[4], datetime.now(UTC)
        )
        print(f"stored {count} assistant labels")
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
