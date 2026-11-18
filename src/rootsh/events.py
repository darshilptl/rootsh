"""The normalized event both agents' hook payloads are converted into."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from types import MappingProxyType
from typing import Any

MAX_TEXT_CHARS = 262_144

type JSON = bool | int | float | str | list[JSON] | dict[str, JSON] | None


class Agent(StrEnum):
    CLAUDE_CODE = "claude_code"
    OPENCODE = "opencode"


class EventKind(StrEnum):
    PROMPT = "prompt"
    TOOL_PRE = "tool_pre"
    TOOL_POST = "tool_post"
    TOOL_FAILURE = "tool_failure"


class Tool(StrEnum):
    SHELL = "shell"
    WRITE = "write"
    EDIT = "edit"
    READ = "read"
    FETCH = "fetch"
    MCP = "mcp"
    OTHER = "other"


TOOL_KINDS = frozenset({EventKind.TOOL_PRE, EventKind.TOOL_POST, EventKind.TOOL_FAILURE})


class EventParseError(ValueError):
    """A hook payload could not be normalized; callers must fail closed."""


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def payload_digest(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def clip_text(text: str | None) -> tuple[str | None, bool]:
    """Cut text to MAX_TEXT_CHARS; report whether anything was cut."""
    if text is None or len(text) <= MAX_TEXT_CHARS:
        return text, False
    return text[:MAX_TEXT_CHARS], True


@dataclass(frozen=True, slots=True)
class AgentEvent:
    agent: Agent
    kind: EventKind
    session_id: str
    turn_id: str | None
    tool_call_id: str | None
    tool: Tool | None
    tool_raw: str | None
    args: Mapping[str, JSON]
    text: str | None
    truncated: bool
    cwd: str | None
    received_at: datetime
    raw_sha256: str = field(repr=False)

    def __post_init__(self) -> None:
        if not self.session_id:
            raise ValueError("session_id must be non-empty")
        tool_fields = (self.tool, self.tool_raw, self.tool_call_id)
        if self.kind in TOOL_KINDS:
            if any(value is None for value in tool_fields):
                raise ValueError(f"{self.kind} events need tool, tool_raw and tool_call_id")
        elif any(value is not None for value in tool_fields) or self.args:
            raise ValueError("prompt events carry no tool fields or args")
        if self.received_at.utcoffset() != timedelta(0):
            raise ValueError("received_at must be UTC")
        if self.text is not None and len(self.text) > MAX_TEXT_CHARS:
            raise ValueError("text exceeds MAX_TEXT_CHARS; use clip_text")
        if len(self.raw_sha256) != 64:
            raise ValueError("raw_sha256 must be a sha256 hex digest")
        # Freeze args so a stored event cannot be mutated through its mapping.
        object.__setattr__(self, "args", MappingProxyType(dict(self.args)))

    @property
    def is_tool_event(self) -> bool:
        return self.kind in TOOL_KINDS
