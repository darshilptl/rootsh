"""Hook payload normalization: one entry point for both agents."""

from __future__ import annotations

from datetime import datetime

from rootsh.events import Agent, AgentEvent
from rootsh.normalize import claude_code, opencode


def normalize(agent: Agent, hook: str, payload: object, now: datetime) -> AgentEvent:
    """Convert one hook delivery into an AgentEvent, or raise EventParseError."""
    if agent is Agent.CLAUDE_CODE:
        return claude_code.normalize(hook, payload, now)
    return opencode.normalize(hook, payload, now)
