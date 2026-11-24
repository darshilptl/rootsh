from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import pytest

from rootsh.analysis.events import (
    MAX_TEXT_CHARS,
    Agent,
    AgentEvent,
    EventKind,
    Tool,
    canonical_json,
    clip_text,
    payload_digest,
)

NOW = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)
DIGEST = "0" * 64


def _event(**overrides: Any) -> AgentEvent:
    fields: dict[str, Any] = {
        "agent": Agent.CLAUDE_CODE,
        "kind": EventKind.TOOL_PRE,
        "session_id": "s1",
        "turn_id": "t1",
        "tool_call_id": "c1",
        "tool": Tool.SHELL,
        "tool_raw": "Bash",
        "args": {"command": "ls"},
        "text": None,
        "truncated": False,
        "cwd": "/w",
        "received_at": NOW,
        "raw_sha256": DIGEST,
    }
    fields.update(overrides)
    return AgentEvent(**fields)


def test_canonical_json_is_key_order_independent() -> None:
    assert canonical_json({"b": 1, "a": [1, "é"]}) == canonical_json({"a": [1, "é"], "b": 1})
    assert canonical_json({"a": "é"}) == '{"a":"é"}'


def test_payload_digest_changes_with_content() -> None:
    assert payload_digest({"a": 1}) == payload_digest({"a": 1})
    assert payload_digest({"a": 1}) != payload_digest({"a": 2})
    assert len(payload_digest({})) == 64


def test_clip_text_marks_truncation_only_when_cut() -> None:
    assert clip_text(None) == (None, False)
    assert clip_text("x" * MAX_TEXT_CHARS) == ("x" * MAX_TEXT_CHARS, False)
    text, truncated = clip_text("x" * (MAX_TEXT_CHARS + 1))
    assert truncated
    assert text is not None
    assert len(text) == MAX_TEXT_CHARS


def test_valid_tool_event_is_frozen() -> None:
    event = _event()
    assert event.is_tool_event
    with pytest.raises(TypeError):
        event.args["command"] = "rm -rf /"  # type: ignore[index]


def test_args_are_copied_not_aliased() -> None:
    source = {"command": "ls"}
    event = _event(args=source)
    source["command"] = "rm -rf /"
    assert event.args["command"] == "ls"


@pytest.mark.parametrize("missing", ["tool", "tool_raw", "tool_call_id"])
def test_tool_event_requires_tool_fields(missing: str) -> None:
    with pytest.raises(ValueError, match="tool_call_id"):
        _event(**{missing: None})


def test_prompt_event_rejects_tool_fields_and_args() -> None:
    prompt = {"kind": EventKind.PROMPT, "tool": None, "tool_raw": None, "tool_call_id": None}
    assert not _event(**prompt, args={}).is_tool_event
    with pytest.raises(ValueError, match="prompt"):
        _event(**prompt, args={"x": 1})
    with pytest.raises(ValueError, match="prompt"):
        _event(**{**prompt, "tool": Tool.SHELL}, args={})


@pytest.mark.parametrize(
    "overrides",
    [
        {"session_id": ""},
        {"received_at": datetime(2026, 11, 18, 12, 0)},  # naive on purpose
        {"received_at": NOW.astimezone(timezone(timedelta(hours=5, minutes=30)))},
        {"text": "x" * (MAX_TEXT_CHARS + 1)},
        {"raw_sha256": "abc"},
    ],
)
def test_invariants_are_enforced(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValueError):  # noqa: PT011 - each case has its own message
        _event(**overrides)
