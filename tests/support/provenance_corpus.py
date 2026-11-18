"""Compare the provenance engine against the hand-labelled transcripts."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from rootsh.effects import Context
from rootsh.events import Agent, AgentEvent
from rootsh.normalize import normalize
from rootsh.provenance import trace
from support import FIXTURES_DIR

NOW = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class Checked:
    label: str  # "<transcript>#<action> <target>"
    expected: dict[str, Any] | None
    actual: dict[str, Any] | None

    @property
    def matched(self) -> bool:
        return self.expected == self.actual


def events_of(agent: str, records: list[dict[str, Any]]) -> list[AgentEvent]:
    return [normalize(Agent(agent), r["hook"], r["payload"], NOW) for r in records]


def _actual(events: list[AgentEvent], action: int, ctx: Context) -> dict[tuple[str, str], Any]:
    result = trace(events[action], events[:action], ctx)
    return {
        (a.target, a.kind.value): {
            source.value: {
                "relation": m.relation.value,
                "event": m.event_seq,
                "same_turn": m.same_turn,
            }
            for source, m in a.best.items()
        }
        for a in result.arguments
    }


def _compare(
    name: str, labelled: list[dict[str, Any]], actual: dict[tuple[str, str], Any]
) -> list[Checked]:
    expected = {(a["target"], a["kind"]): a["sources"] for a in labelled}
    return [
        Checked(
            f"{name} {target} ({kind})", expected.get((target, kind)), actual.get((target, kind))
        )
        for target, kind in [*expected, *(k for k in actual if k not in expected)]
    ]


def constructed() -> list[Checked]:
    data = json.loads((FIXTURES_DIR / "transcripts_constructed.json").read_text(encoding="utf-8"))
    ctx = Context(data["context"]["cwd"], data["context"]["home"])
    checked: list[Checked] = []
    for t in data["transcripts"]:
        events = events_of(t["agent"], t["events"])
        checked += _compare(
            f"{t['id']}#{t['action']}", t["arguments"], _actual(events, t["action"], ctx)
        )
    return checked


def real() -> list[Checked]:
    data = json.loads((FIXTURES_DIR / "transcripts_real.json").read_text(encoding="utf-8"))
    labels = json.loads((FIXTURES_DIR / "provenance_real_labels.json").read_text(encoding="utf-8"))
    ctx = Context(data["context"]["cwd"], data["context"]["home"])
    labelled = {(a["session"], a["action"]): a["arguments"] for a in labels["actions"]}
    checked: list[Checked] = []
    for session in data["sessions"]:
        events = events_of(session["agent"], session["events"])
        for index, event in enumerate(events):
            if not event.is_tool_event or event.kind.value != "tool_pre":
                continue
            name = f"{session['id']}#{index}"
            checked += _compare(
                name, labelled.get((session["id"], index), []), _actual(events, index, ctx)
            )
    return checked
