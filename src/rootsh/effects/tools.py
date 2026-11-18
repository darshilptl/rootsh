"""Non-shell agent tools -> effects (design doc §4)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from rootsh.effects.model import Analysis, Context, Effect, EffectKind
from rootsh.effects.shell import analyze_shell
from rootsh.effects.words import Word, resolve
from rootsh.events import JSON, Tool


@dataclass(frozen=True, slots=True)
class _FileTool:
    key: str
    kind: EffectKind
    detail: str = ""


# Keyed by (normalized tool, agent's own tool name); input keys verified in P0/P1.
_FILE_TOOLS: Mapping[tuple[Tool, str], _FileTool] = {
    (Tool.WRITE, "Write"): _FileTool("file_path", EffectKind.WRITE, "create"),
    (Tool.EDIT, "Edit"): _FileTool("file_path", EffectKind.WRITE, "modify"),
    (Tool.READ, "Read"): _FileTool("file_path", EffectKind.READ),
    (Tool.WRITE, "write"): _FileTool("path", EffectKind.WRITE, "create"),
}


def _opaque(tool_raw: str) -> Analysis:
    return Analysis((), (), (f"tool:{tool_raw}",), parse_error=False)


def analyze_tool(tool: Tool, tool_raw: str, args: Mapping[str, JSON], ctx: Context) -> Analysis:
    if tool is Tool.SHELL:
        command = args.get("command")
        if not isinstance(command, str):
            return _opaque(tool_raw)
        workdir = args.get("workdir")  # OpenCode's shell tool runs in this directory
        if isinstance(workdir, str) and workdir:
            target = resolve(Word(workdir), ctx)
            ctx = ctx.with_cwd(None if target.dynamic else target.path)
        return analyze_shell(command, ctx)
    if tool is Tool.FETCH and tool_raw == "WebFetch":
        url = args.get("url")
        if not isinstance(url, str) or not url:
            return _opaque(tool_raw)
        return Analysis((Effect(EffectKind.NETWORK, url, tool_raw, detail="fetch"),), (), (), False)
    spec = _FILE_TOOLS.get((tool, tool_raw))
    value = args.get(spec.key) if spec is not None else None
    if spec is None or not isinstance(value, str) or not value:
        return _opaque(tool_raw)
    target = resolve(Word(value), ctx)
    effect = Effect(spec.kind, target.path, tool_raw, dynamic=target.dynamic, detail=spec.detail)
    return Analysis((effect,), (), (), False)
