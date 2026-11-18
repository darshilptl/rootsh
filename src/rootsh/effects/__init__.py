"""Effect extraction: what a proposed tool call would do to the host."""

from __future__ import annotations

from rootsh.effects.model import Analysis, Context, Effect, EffectKind
from rootsh.effects.shell import analyze_shell
from rootsh.effects.tools import analyze_tool
from rootsh.events import AgentEvent


def analyze_event(event: AgentEvent, ctx: Context) -> Analysis:
    """Effects of the tool call an event proposes. Prompt events have none."""
    if not event.is_tool_event or event.tool is None or event.tool_raw is None:
        raise ValueError("only tool events have effects")
    return analyze_tool(event.tool, event.tool_raw, event.args, ctx)


__all__ = [
    "Analysis",
    "Context",
    "Effect",
    "EffectKind",
    "analyze_event",
    "analyze_shell",
    "analyze_tool",
]
