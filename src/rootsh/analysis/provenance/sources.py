"""Which source class each history event belongs to (design doc §3)."""

from __future__ import annotations

from rootsh.analysis.effects import Analysis, Context, EffectKind
from rootsh.analysis.events import AgentEvent, Tool
from rootsh.analysis.provenance.model import Source

LISTERS = frozenset({"ls", "find", "tree", "du", "stat", "file", "pwd", "git"})
FILTERS = frozenset(
    {"head", "tail", "sort", "uniq", "wc", "cut", "tr", "echo", "printf", "date", "true"}
)
LISTING_TOOLS = frozenset({"Glob", "LS", "glob", "list"})
# Their output only echoes the agent's own input; the write itself is `own`.
WRITE_TOOLS = frozenset({"Write", "Edit", "MultiEdit", "write", "edit"})
OWN_KINDS = frozenset({EffectKind.WRITE, EffectKind.MOVE})


def event_context(event: AgentEvent, base: Context) -> Context:
    return Context(event.cwd or base.cwd, base.home, base.env)


def is_listing(analysis: Analysis) -> bool:
    """Only listers and stdin filters ran, and only listers read files."""
    programs = set(analysis.programs)
    return (
        bool(programs & LISTERS)
        and programs <= LISTERS | FILTERS
        and not analysis.opaque
        and all(e.kind is EffectKind.READ and e.program in LISTERS for e in analysis.effects)
    )


def source_of(event: AgentEvent, analysis: Analysis | None) -> Source | None:
    """The source class of a tool's output; None for write tools' echoes.

    `analysis` is the effects analysis of the tool call (None if unknown).
    """
    if event.tool_raw in WRITE_TOOLS:
        return None
    if event.tool_raw in LISTING_TOOLS:
        return Source.WORKSPACE
    if event.tool is Tool.SHELL and analysis is not None and is_listing(analysis):
        return Source.WORKSPACE
    return Source.CONTENT


def written_paths(analysis: Analysis) -> list[str]:
    """Paths a tool call writes, creates, or moves into."""
    return [
        effect.dest if effect.dest is not None else effect.target
        for effect in analysis.effects
        if effect.kind in OWN_KINDS and not effect.dynamic
    ]
