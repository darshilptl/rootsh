"""Claude Code hook payloads (verified in P0 §1 and P1) -> AgentEvent."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from rootsh.analysis.events import (
    Agent,
    AgentEvent,
    EventKind,
    EventParseError,
    Tool,
    canonical_json,
    clip_text,
    payload_digest,
)
from rootsh.analysis.normalize._fields import (
    optional_str,
    require_mapping,
    require_object,
    require_str,
    require_text,
)

HOOK_KINDS: Mapping[str, EventKind] = {
    "UserPromptSubmit": EventKind.PROMPT,
    "PreToolUse": EventKind.TOOL_PRE,
    "PostToolUse": EventKind.TOOL_POST,
    "PostToolUseFailure": EventKind.TOOL_FAILURE,
}

# Only names verified against the installed version or the official docs;
# anything else is Tool.OTHER with its raw name kept.
_TOOLS: Mapping[str, Tool] = {
    "Bash": Tool.SHELL,
    "Write": Tool.WRITE,
    "Edit": Tool.EDIT,
    "Read": Tool.READ,
    "WebFetch": Tool.FETCH,
}


def tool_for(name: str) -> Tool:
    if name.startswith("mcp__"):
        return Tool.MCP
    return _TOOLS.get(name, Tool.OTHER)


def response_text(response: object) -> str:
    if isinstance(response, str):
        return response
    if isinstance(response, Mapping) and ("stdout" in response or "stderr" in response):
        stdout = response.get("stdout") or ""
        stderr = response.get("stderr") or ""
        if not isinstance(stdout, str) or not isinstance(stderr, str):
            raise EventParseError("tool_response stdout/stderr must be strings")
        return f"{stdout}\n{stderr}" if stderr else stdout
    if isinstance(response, Mapping) and isinstance(response.get("file"), Mapping):
        content = response["file"].get("content")  # Read: {"type", "file": {"filePath", "content"}}
        if isinstance(content, str):
            return content
    return canonical_json(response)


def normalize(hook: str, raw: object, now: datetime) -> AgentEvent:
    kind = HOOK_KINDS.get(hook)
    if kind is None:
        raise EventParseError(f"unknown Claude Code hook {hook!r}")
    payload = require_object(raw)
    if payload.get("hook_event_name") != hook:
        raise EventParseError(f"hook_event_name does not match delivered hook {hook!r}")

    common: dict[str, Any] = {
        "agent": Agent.CLAUDE_CODE,
        "kind": kind,
        "session_id": require_str(payload, "session_id"),
        "turn_id": require_str(payload, "prompt_id"),
        "cwd": optional_str(payload, "cwd"),
        "received_at": now,
        "raw_sha256": payload_digest(payload),
    }

    if kind is EventKind.PROMPT:
        text, truncated = clip_text(require_text(payload, "prompt"))
        return AgentEvent(
            **common,
            tool_call_id=None,
            tool=None,
            tool_raw=None,
            args={},
            text=text,
            truncated=truncated,
        )

    tool_raw = require_str(payload, "tool_name")
    if kind is EventKind.TOOL_POST:
        if "tool_response" not in payload:
            raise EventParseError("field 'tool_response' is required")
        output: str | None = response_text(payload["tool_response"])
    elif kind is EventKind.TOOL_FAILURE:
        output = require_text(payload, "error")
    else:
        output = None
    text, truncated = clip_text(output)
    return AgentEvent(
        **common,
        tool_call_id=require_str(payload, "tool_use_id"),
        tool=tool_for(tool_raw),
        tool_raw=tool_raw,
        args=require_mapping(payload, "tool_input"),
        text=text,
        truncated=truncated,
    )
