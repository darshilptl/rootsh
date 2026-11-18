"""Normalizer tests on sanitized real payloads recorded in P0/P1."""

from __future__ import annotations

import copy
import json
from datetime import UTC, datetime
from typing import Any

import pytest

from rootsh.events import MAX_TEXT_CHARS, Agent, EventKind, EventParseError, Tool, payload_digest
from rootsh.normalize import claude_code, normalize, opencode
from support import FIXTURES_DIR

NOW = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)
FIXTURES: dict[str, dict[str, dict[str, Any]]] = json.loads(
    (FIXTURES_DIR / "hook_payloads.json").read_text(encoding="utf-8")
)


def claude(hook: str) -> dict[str, Any]:
    return copy.deepcopy(FIXTURES["claude_code"][hook])


def oc(name: str) -> dict[str, Any]:
    return copy.deepcopy(FIXTURES["opencode"][name])


# --- Claude Code -------------------------------------------------------------


def test_claude_prompt() -> None:
    payload = claude("UserPromptSubmit")
    event = normalize(Agent.CLAUDE_CODE, "UserPromptSubmit", payload, NOW)
    assert event.kind is EventKind.PROMPT
    assert event.turn_id == payload["prompt_id"]
    assert event.text == payload["prompt"]
    assert event.tool is None
    assert event.args == {}
    assert event.cwd == "/home/user/project"
    assert event.raw_sha256 == payload_digest(payload)


def test_claude_tool_events_share_the_prompt_turn() -> None:
    turns = {
        normalize(Agent.CLAUDE_CODE, hook, claude(hook), NOW).turn_id
        for hook in ("UserPromptSubmit", "PreToolUse", "PostToolUse")
    }
    assert len(turns) == 1


def test_claude_pre_tool_use() -> None:
    payload = claude("PreToolUse")
    event = normalize(Agent.CLAUDE_CODE, "PreToolUse", payload, NOW)
    assert event.kind is EventKind.TOOL_PRE
    assert event.tool is Tool.SHELL
    assert event.tool_raw == "Bash"
    assert event.tool_call_id == payload["tool_use_id"]
    assert event.args["command"] == "touch ./marker"
    assert event.text is None


def test_claude_post_tool_use_reads_stdout() -> None:
    event = normalize(Agent.CLAUDE_CODE, "PostToolUse", claude("PostToolUse"), NOW)
    assert event.kind is EventKind.TOOL_POST
    assert event.text == ""


def test_claude_post_tool_use_failure_reads_error() -> None:
    event = normalize(Agent.CLAUDE_CODE, "PostToolUseFailure", claude("PostToolUseFailure"), NOW)
    assert event.kind is EventKind.TOOL_FAILURE
    assert event.text is not None
    assert event.text.startswith("Exit code 2")


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        ("plain", "plain"),
        ({"stdout": "out", "stderr": ""}, "out"),
        ({"stdout": "out", "stderr": "err"}, "out\nerr"),
        ({"stderr": "err"}, "\nerr"),
        ({"filePath": "/w/a", "type": "create"}, '{"filePath":"/w/a","type":"create"}'),
    ],
)
def test_claude_response_text(response: object, expected: str) -> None:
    assert claude_code.response_text(response) == expected


def test_claude_response_text_rejects_non_string_streams() -> None:
    with pytest.raises(EventParseError):
        claude_code.response_text({"stdout": 3})


@pytest.mark.parametrize(
    ("name", "tool"),
    [
        ("Bash", Tool.SHELL),
        ("Write", Tool.WRITE),
        ("Edit", Tool.EDIT),
        ("Read", Tool.READ),
        ("WebFetch", Tool.FETCH),
        ("mcp__github__create_issue", Tool.MCP),
        ("NotebookEdit", Tool.OTHER),
        ("SomethingNew", Tool.OTHER),
    ],
)
def test_claude_tool_map(name: str, tool: Tool) -> None:
    assert claude_code.tool_for(name) is tool


def test_claude_unknown_tool_keeps_raw_name() -> None:
    payload = claude("PreToolUse")
    payload["tool_name"] = "SomethingNew"
    event = normalize(Agent.CLAUDE_CODE, "PreToolUse", payload, NOW)
    assert event.tool is Tool.OTHER
    assert event.tool_raw == "SomethingNew"


def test_claude_long_prompt_is_clipped_not_dropped() -> None:
    payload = claude("UserPromptSubmit")
    payload["prompt"] = "x" * (MAX_TEXT_CHARS + 10)
    event = normalize(Agent.CLAUDE_CODE, "UserPromptSubmit", payload, NOW)
    assert event.truncated
    assert event.text is not None
    assert len(event.text) == MAX_TEXT_CHARS


@pytest.mark.parametrize(
    ("hook", "mutate"),
    [
        ("Stop", lambda p: None),
        ("PreToolUse", lambda p: p.update(hook_event_name="PostToolUse")),
        ("PreToolUse", lambda p: p.pop("session_id")),
        ("PreToolUse", lambda p: p.update(session_id="")),
        ("PreToolUse", lambda p: p.pop("prompt_id")),
        ("PreToolUse", lambda p: p.pop("tool_use_id")),
        ("PreToolUse", lambda p: p.update(tool_input="ls")),
        ("PreToolUse", lambda p: p.update(cwd=7)),
        ("UserPromptSubmit", lambda p: p.pop("prompt")),
        ("PostToolUse", lambda p: p.pop("tool_response")),
        ("PostToolUseFailure", lambda p: p.pop("error")),
    ],
)
def test_claude_parse_errors(hook: str, mutate: Any) -> None:
    payload = claude("PreToolUse" if hook == "Stop" else hook)
    mutate(payload)
    with pytest.raises(EventParseError):
        normalize(Agent.CLAUDE_CODE, hook, payload, NOW)


@pytest.mark.parametrize("payload", [None, [], "text", {1: "x"}])
def test_non_object_payloads_are_rejected(payload: object) -> None:
    with pytest.raises(EventParseError):
        normalize(Agent.CLAUDE_CODE, "PreToolUse", payload, NOW)
    with pytest.raises(EventParseError):
        normalize(Agent.OPENCODE, "tool.execute.before", payload, NOW)


# --- OpenCode ----------------------------------------------------------------


def test_opencode_prompt() -> None:
    payload = oc("session.prompt")
    event = normalize(Agent.OPENCODE, "session.prompt", payload, NOW)
    assert event.kind is EventKind.PROMPT
    assert event.turn_id == payload["messageID"]
    assert event.text == payload["prompt"]["text"]


def test_opencode_tool_before() -> None:
    payload = oc("tool.execute.before")
    event = normalize(Agent.OPENCODE, "tool.execute.before", payload, NOW)
    assert event.kind is EventKind.TOOL_PRE
    assert event.tool is Tool.SHELL
    assert event.tool_call_id == payload["id"]
    assert event.args == {"command": "touch ./marker"}
    assert event.turn_id is None  # resolved at ingest
    assert event.cwd is None


def test_opencode_tool_after_completed() -> None:
    event = normalize(Agent.OPENCODE, "tool.execute.after", oc("tool.execute.after"), NOW)
    assert event.kind is EventKind.TOOL_POST
    assert event.text == "(no output)"


def test_opencode_tool_after_error_explains_the_block() -> None:
    payload = oc("tool.execute.after:error")
    event = normalize(Agent.OPENCODE, "tool.execute.after", payload, NOW)
    assert event.kind is EventKind.TOOL_FAILURE
    assert event.text == "Unable to execute command: touch ./marker\nrootsh p0 probe: deny"


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (None, None),
        ({"message": "boom"}, "boom"),
        ({"_tag": "X"}, '{"_tag":"X"}'),
    ],
)
def test_opencode_error_text(error: object, expected: str | None) -> None:
    assert opencode.error_text(error) == expected


def test_opencode_result_text_joins_text_parts_only() -> None:
    result = {
        "content": [{"type": "text", "text": "a"}, {"type": "image"}, {"type": "text", "text": "b"}]
    }
    assert opencode.result_text(result) == "a\nb"


@pytest.mark.parametrize(
    ("name", "mutate"),
    [
        ("tool.execute.before", lambda p: p.pop("sessionID")),
        ("tool.execute.before", lambda p: p.pop("id")),
        ("tool.execute.before", lambda p: p.pop("tool")),
        ("tool.execute.before", lambda p: p.update(input=[])),
        ("tool.execute.after", lambda p: p.update(status="running")),
        ("tool.execute.after", lambda p: p.pop("status")),
        ("tool.execute.after", lambda p: p.update(result=None)),
        ("tool.execute.after", lambda p: p.update(result={"content": "x"})),
        ("tool.execute.after", lambda p: p.update(result={"content": [{"type": "text"}]})),
        ("tool.execute.after:error", lambda p: p.update(error="boom")),
        ("session.prompt", lambda p: p.pop("messageID")),
        ("session.prompt", lambda p: p.update(prompt={"files": []})),
    ],
)
def test_opencode_parse_errors(name: str, mutate: Any) -> None:
    payload = oc(name)
    mutate(payload)
    with pytest.raises(EventParseError):
        normalize(Agent.OPENCODE, name.split(":")[0], payload, NOW)


def test_opencode_unknown_hook() -> None:
    with pytest.raises(EventParseError):
        normalize(Agent.OPENCODE, "shell.create.before", oc("tool.execute.before"), NOW)


def test_opencode_unknown_tool_is_other() -> None:
    assert opencode.tool_for("webfetch") is Tool.OTHER
