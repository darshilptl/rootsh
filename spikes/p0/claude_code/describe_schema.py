"""Summarize the hook-event and transcript schemas seen in probe runs.

Reads out/*/events.jsonl, prints the field set of each hook event, and the
entry and content-block types of one session transcript.

Usage: python3 -I describe_schema.py
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

OUT = Path(__file__).resolve().parent / "out"


def _hook_fields() -> tuple[dict[str, list[str]], str | None]:
    fields: dict[str, set[str]] = defaultdict(set)
    transcript: str | None = None
    for events in sorted(OUT.glob("*/events.jsonl")):
        for line in events.read_text(encoding="utf-8").splitlines():
            event = json.loads(line)["event"]
            fields[event["hook_event_name"]].update(event)
            if event["hook_event_name"] == "PostToolUse" and transcript is None:
                transcript = event["transcript_path"]
    return {name: sorted(keys) for name, keys in sorted(fields.items())}, transcript


def _transcript_shape(path: str) -> dict[str, object]:
    entry_types: dict[str, set[str]] = defaultdict(set)
    block_types: set[str] = set()
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        entry = json.loads(line)
        entry_types[entry.get("type", "?")].update(entry)
        content = entry.get("message", {}).get("content") if isinstance(entry.get("message"), dict) else None
        if isinstance(content, list):
            block_types.update(block.get("type", "?") for block in content)
    return {
        "entry_types": {name: sorted(keys) for name, keys in sorted(entry_types.items())},
        "content_block_types": sorted(block_types),
    }


def main() -> int:
    hooks, transcript = _hook_fields()
    schema = {"hook_events": hooks, "transcript": _transcript_shape(transcript) if transcript else None}
    (OUT / "schema.json").write_text(json.dumps(schema, indent=2), encoding="utf-8")
    print(json.dumps(schema, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
