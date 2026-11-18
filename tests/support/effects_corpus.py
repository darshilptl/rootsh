"""Compare the extractor against tests/fixtures/effects_corpus.json."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from typing import Any

from rootsh.effects import Analysis, Context, analyze_shell, analyze_tool
from rootsh.events import Tool
from rootsh.normalize import claude_code, opencode
from support import FIXTURES_DIR

CORPUS_PATH = FIXTURES_DIR / "effects_corpus.json"


@dataclass(frozen=True, slots=True)
class Result:
    entry: dict[str, Any]
    analysis: Analysis
    matched: bool
    diff: dict[str, Any]


def load() -> tuple[Context, list[dict[str, Any]]]:
    data = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))
    c = data["context"]
    return Context(c["cwd"], c["home"], c["env"]), data["entries"]


def _effect_key(effect: dict[str, Any]) -> tuple[Any, ...]:
    return (
        effect["kind"],
        effect["target"],
        effect["program"],
        effect.get("recursive", False),
        effect.get("dynamic", False),
        effect.get("glob", False),
        effect.get("detail", ""),
        effect.get("dest"),
    )


def _actual_effects(analysis: Analysis) -> Counter[tuple[Any, ...]]:
    return Counter(
        (e.kind.value, e.target, e.program, e.recursive, e.dynamic, e.glob, e.detail, e.dest)
        for e in analysis.effects
    )


def _tool(entry: dict[str, Any]) -> Tool:
    spec = entry["tool"]
    if spec["agent"] == "claude_code":
        return claude_code.tool_for(spec["name"])
    return opencode.tool_for(spec["name"])


def evaluate(entry: dict[str, Any], ctx: Context) -> Result:
    if "tool" in entry:
        analysis = analyze_tool(_tool(entry), entry["tool"]["name"], entry["tool"]["args"], ctx)
    else:
        analysis = analyze_shell(entry["cmd"], ctx)
    diff: dict[str, Any] = {}
    if analysis.parse_error != entry["parse_error"]:
        diff["parse_error"] = (entry["parse_error"], analysis.parse_error)
    if entry.get("partial"):
        # Error recovery decides which fragments survive, so only the flag counts.
        if entry["parse_error"] and "parse_error" not in analysis.opaque:
            diff["opaque"] = (entry["opaque"], list(analysis.opaque))
    elif sorted(analysis.opaque) != entry["opaque"]:
        diff["opaque"] = (entry["opaque"], list(analysis.opaque))
    if not entry.get("partial"):
        expected = Counter(_effect_key(e) for e in entry["effects"])
        actual = _actual_effects(analysis)
        if expected != actual:
            diff["effects"] = {
                "missing": sorted(map(str, expected - actual)),
                "extra": sorted(map(str, actual - expected)),
            }
        if list(analysis.programs) != entry["programs"]:
            diff["programs"] = (entry["programs"], list(analysis.programs))
    return Result(entry, analysis, not diff, diff)


def run() -> list[Result]:
    ctx, entries = load()
    return [evaluate(entry, ctx) for entry in entries]
