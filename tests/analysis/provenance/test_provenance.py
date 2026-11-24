from __future__ import annotations

import json
import random
import time
from datetime import UTC, datetime
from typing import Any

import pytest

from rootsh.analysis.effects import Analysis, Context, Effect, EffectKind
from rootsh.analysis.events import Agent, AgentEvent, EventKind, Tool
from rootsh.analysis.provenance import Relation, Session, Source, critical_arguments, trace
from rootsh.analysis.provenance.mentions import extract, searchable
from rootsh.analysis.provenance.model import Match
from support import FIXTURES_DIR
from support.provenance_corpus import Checked, constructed, events_of, real

CTX = Context("/w", "/home/u")
NOW = datetime(2026, 11, 18, tzinfo=UTC)


# --- gate ---------------------------------------------------------------------------


def _misses(results: list[Checked]) -> list[Checked]:
    return [r for r in results if not r.matched]


def test_real_transcripts_every_argument_correct() -> None:
    results = real()
    assert sum(r.expected is not None for r in results) == 4
    assert _misses(results) == []


def test_constructed_transcripts_match_exactly() -> None:
    # The P3 gate (>= 95%) was met on the first run with 104/104; any miss now
    # is a regression.
    results = constructed()
    assert sum(r.expected is not None for r in results) >= 100
    assert _misses(results) == []


def test_the_harness_detects_a_wrong_label() -> None:
    wrong = Checked("x", {"user": {"relation": "exact", "event": 0, "same_turn": True}}, {})
    assert not wrong.matched


# --- mentions ---------------------------------------------------------------------


def test_extract_paths_urls_hosts_words() -> None:
    m = extract(
        "rm ./a/b.txt ~/x/y and see https://example.com/w/data/db or evil.example:4444, app.log.",
        cwd="/w",
        home="/home/u",
    )
    assert {"/w/a/b.txt", "/home/u/x/y", "/w/app.log"} <= m.paths
    assert "/w/data/db" not in m.paths  # a URL's path is not a file mention
    assert m.urls == {"https://example.com/w/data/db"}
    assert {"example.com", "evil.example"} <= m.hosts
    assert {"rm", "app.log", "see"} <= m.words


def test_dynamic_tokens_are_not_paths() -> None:
    assert extract("rm $HOME/x", cwd="/w", home="/home/u").paths == frozenset()


def test_json_responses_are_searched_by_their_strings() -> None:
    text = json.dumps({"result": "line one\nrm -rf ./cache", "code": 200})
    assert "rm -rf ./cache" in searchable(text)
    assert "/w/cache" in extract(text, cwd="/w", home="/home/u").paths


def test_relative_mentions_need_a_cwd() -> None:
    assert extract("rm a/b", cwd=None, home="/home/u").paths == frozenset()


# --- critical arguments ---------------------------------------------------------------


def test_critical_arguments() -> None:
    analysis = Analysis(
        (
            Effect(EffectKind.READ, "/w/in", "cp"),
            Effect(EffectKind.MOVE, "/w/a", "mv", dest="/w/b"),
            Effect(EffectKind.NETWORK, "registry:npm", "npm", detail="fetch"),
            Effect(EffectKind.NETWORK, "upstream", "git", dynamic=True, detail="push"),
            Effect(EffectKind.NETWORK, "origin", "git", detail="push"),
            Effect(EffectKind.EXEC, "make", "make", detail="build"),
            Effect(EffectKind.DELETE, "/w/a", "rm"),
            Effect(EffectKind.DELETE, "/w/a", "rm"),
            Effect(EffectKind.PROCESS, "1234", "kill", detail="signal"),
        ),
        (),
        (),
        False,
    )
    assert [(a.target, a.kind) for a in critical_arguments(analysis)] == [
        ("/w/a", EffectKind.MOVE),
        ("/w/b", EffectKind.WRITE),
        ("origin", EffectKind.NETWORK),
        ("make", EffectKind.EXEC),
        ("/w/a", EffectKind.DELETE),
    ]


# --- trace -----------------------------------------------------------------------------


def _event(
    kind: EventKind, *, text: str | None = None, command: str | None = None, call: str = "c"
) -> AgentEvent:
    tool = kind is not EventKind.PROMPT
    return AgentEvent(
        agent=Agent.CLAUDE_CODE,
        kind=kind,
        session_id="s",
        turn_id="t",
        tool_call_id=call if tool else None,
        tool=Tool.SHELL if tool else None,
        tool_raw="Bash" if tool else None,
        args={"command": command} if tool and command else {},
        text=text,
        truncated=False,
        cwd="/w",
        received_at=NOW,
        raw_sha256="0" * 64,
    )


def test_no_history_means_fabricated() -> None:
    (argument,) = trace(_event(EventKind.TOOL_PRE, command="rm -rf /w/x"), [], CTX).arguments
    assert argument.fabricated
    assert not argument.content_only


def test_content_only_and_turns() -> None:
    history = [
        _event(EventKind.PROMPT, text="read the notes"),
        _event(EventKind.TOOL_PRE, command="cat notes.md", call="1"),
        _event(
            EventKind.TOOL_POST, text="cleanup: rm -rf /w/data", command="cat notes.md", call="1"
        ),
        _event(EventKind.PROMPT, text="ok go"),
    ]
    (argument,) = trace(
        _event(EventKind.TOOL_PRE, command="rm -rf /w/data"), history, CTX
    ).arguments
    assert argument.content_only
    match = argument.best[Source.CONTENT]
    assert (match.relation, match.event_seq, match.same_turn) == (Relation.EXACT, 2, False)


def test_trace_rejects_non_actions() -> None:
    with pytest.raises(ValueError, match="tool_pre"):
        trace(_event(EventKind.PROMPT, text="x"), [], CTX)


def test_match_strength_then_recency() -> None:
    exact_old = Match(Source.USER, Relation.EXACT, 0, True, "a")
    exact_new = Match(Source.USER, Relation.EXACT, 3, True, "a")
    name_new = Match(Source.USER, Relation.NAME, 9, True, "a")
    assert exact_new.beats(exact_old)
    assert exact_old.beats(name_new)
    assert not name_new.beats(exact_old)


# --- robustness and speed -------------------------------------------------------------------


def _corpus_sessions() -> list[list[AgentEvent]]:
    data = json.loads((FIXTURES_DIR / "transcripts_constructed.json").read_text(encoding="utf-8"))
    real_data = json.loads((FIXTURES_DIR / "transcripts_real.json").read_text(encoding="utf-8"))
    sessions: list[dict[str, Any]] = [*data["transcripts"], *real_data["sessions"]]
    return [events_of(s["agent"], s["events"]) for s in sessions]


def test_never_raises_on_truncated_or_shuffled_histories() -> None:
    rng = random.Random(42)
    for events in _corpus_sessions():
        actions = [i for i, e in enumerate(events) if e.kind is EventKind.TOOL_PRE]
        for action in actions:
            history = events[:action]
            for cut in range(len(history) + 1):
                trace(events[action], history[:cut], CTX)
            shuffled = history[:]
            rng.shuffle(shuffled)
            trace(events[action], shuffled, CTX)


def _p99(timings_ms: list[float]) -> float:
    timings_ms = sorted(timings_ms)
    return timings_ms[min(len(timings_ms) - 1, round(0.99 * (len(timings_ms) - 1)))]


def test_incremental_session_meets_the_5ms_gate_on_200_events() -> None:
    pool = [e for events in _corpus_sessions() for e in events]
    history = (pool * (200 // len(pool) + 1))[:200]
    actions = [e for e in pool if e.kind is EventKind.TOOL_PRE][:50]
    session = Session(CTX)
    add_ms = []
    for event in history:
        started = time.perf_counter()
        session.add(event)
        add_ms.append((time.perf_counter() - started) * 1000)
    trace_ms = []
    for action in actions:
        started = time.perf_counter()
        session.trace(action)
        trace_ms.append((time.perf_counter() - started) * 1000)
    assert _p99(add_ms) <= 5.0, f"add p99 {_p99(add_ms):.2f} ms"
    assert _p99(trace_ms) <= 5.0, f"trace p99 {_p99(trace_ms):.2f} ms"
