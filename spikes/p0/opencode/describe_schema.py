"""Summarize the plugin-hook event schemas seen in OpenCode probe runs.

Usage: python3 -I describe_schema.py
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

OUT = Path(__file__).resolve().parent / "out"


def _shape(value: object, depth: int = 2) -> object:
    if isinstance(value, dict) and depth > 0:
        return {key: _shape(item, depth - 1) for key, item in sorted(value.items())}
    return type(value).__name__


def main() -> int:
    shapes: dict[str, object] = {}
    actions: dict[str, set[str]] = defaultdict(set)
    for events in sorted(OUT.glob("*/events.jsonl")):
        for line in events.read_text(encoding="utf-8").splitlines():
            item = json.loads(line)
            shapes.setdefault(item["kind"], _shape(item["event"]))
            if item["kind"] == "permission.evaluate":
                actions[item["event"].get("action", "?")].add(events.parent.name)
    schema = {"hook_events": shapes, "permission_actions": {k: sorted(v) for k, v in actions.items()}}
    (OUT / "schema.json").write_text(json.dumps(schema, indent=2), encoding="utf-8")
    print(json.dumps(schema, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
