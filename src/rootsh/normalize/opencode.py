"""OpenCode v2.3.0 plugin hook events (verified in P0 §2 and P1) -> AgentEvent."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from rootsh.events import (
    Agent,
    AgentEvent,
    EventKind,
    EventParseError,
    Tool,
    canonical_json,
    clip_text,
    payload_digest,
)
from rootsh.normalize._fields import require_mapping, require_object, require_str, require_text

HOOKS = frozenset({"session.prompt", "tool.execute.before", "tool.execute.after"})

_TOOLS: Mapping[str, Tool] = {"shell": Tool.SHELL, "write": Tool.WRITE}

_AFTER_STATUS: Mapping[str, EventKind] = {
    "completed": EventKind.TOOL_POST,
    "error": EventKind.TOOL_FAILURE,
}


def tool_for(name: str) -> Tool:
    return _TOOLS.get(name, Tool.OTHER)


def result_text(result: object) -> str:
    """Join the text parts of `result.content`."""
    if not isinstance(result, Mapping):
        raise EventParseError("completed tool result must be an object")
    content = result.get("content")
    if not isinstance(content, list):
        raise EventParseError("tool result content must be a list")
    parts = []
    for part in content:
        if isinstance(part, Mapping) and part.get("type") == "text":
            text = part.get("text")
            if not isinstance(text, str):
                raise EventParseError("text content part must carry a string")
            parts.append(text)
    return "\n".join(parts)


def error_text(error: object) -> str | None:
    """`message`, plus the nested `reason` when a block explains itself."""
    if error is None:
        return None
    if not isinstance(error, Mapping):
        raise EventParseError("tool error must be an object")
    message = error.get("message")
    inner = error.get("error")
    reason = inner.get("reason") if isinstance(inner, Mapping) else None
    parts = [part for part in (message, reason) if isinstance(part, str) and part]
    return "\n".join(parts) if parts else canonical_json(error)


def normalize(hook: str, raw: object, now: datetime) -> AgentEvent:
    if hook not in HOOKS:
        raise EventParseError(f"unknown OpenCode hook {hook!r}")
    payload = require_object(raw)
    common: dict[str, Any] = {
        "agent": Agent.OPENCODE,
        "session_id": require_str(payload, "sessionID"),
        "cwd": None,
        "received_at": now,
        "raw_sha256": payload_digest(payload),
    }

    if hook == "session.prompt":
        prompt = require_mapping(payload, "prompt")
        text, truncated = clip_text(require_text(prompt, "text"))
        return AgentEvent(
            **common,
            kind=EventKind.PROMPT,
            turn_id=require_str(payload, "messageID"),
            tool_call_id=None,
            tool=None,
            tool_raw=None,
            args={},
            text=text,
            truncated=truncated,
        )

    if hook == "tool.execute.before":
        kind = EventKind.TOOL_PRE
        output: str | None = None
    else:
        status = require_str(payload, "status")
        after_kind = _AFTER_STATUS.get(status)
        if after_kind is None:
            raise EventParseError(f"unknown tool status {status!r}")
        kind = after_kind
        if kind is EventKind.TOOL_POST:
            output = result_text(payload.get("result"))
        else:
            output = error_text(payload.get("error"))

    tool_raw = require_str(payload, "tool")
    text, truncated = clip_text(output)
    return AgentEvent(
        **common,
        kind=kind,
        # The tool events' messageID is the assistant message, not the user's
        # prompt; the store links them to the session's latest prompt.
        turn_id=None,
        tool_call_id=require_str(payload, "id"),
        tool=tool_for(tool_raw),
        tool_raw=tool_raw,
        args=require_mapping(payload, "input"),
        text=text,
        truncated=truncated,
    )
