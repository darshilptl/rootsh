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


# Keyed by (normalized tool, agent's own tool name); input keys verified in
# P0/P1, OpenCode read/edit from the P4 gold traces.
_FILE_TOOLS: Mapping[tuple[Tool, str], _FileTool] = {
    (Tool.WRITE, "Write"): _FileTool("file_path", EffectKind.WRITE, "create"),
    (Tool.EDIT, "Edit"): _FileTool("file_path", EffectKind.WRITE, "modify"),
    (Tool.READ, "Read"): _FileTool("file_path", EffectKind.READ),
    (Tool.WRITE, "write"): _FileTool("path", EffectKind.WRITE, "create"),
    (Tool.READ, "read"): _FileTool("path", EffectKind.READ),
    (Tool.EDIT, "edit"): _FileTool("path", EffectKind.WRITE, "modify"),
}


# OpenCode search tools read a whole tree; `path` is optional and defaults to
# the session's directory (P4 gold traces: 72 of 109 glob calls omit it).
_SEARCH_TOOLS = frozenset({(Tool.READ, "glob"), (Tool.READ, "grep")})


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
    if tool is Tool.FETCH and tool_raw in ("WebFetch", "webfetch", "websearch"):
        # A search query leaves the host as surely as a URL does (exfiltration
        # by query), so it is the network target.
        where = args.get("query" if tool_raw == "websearch" else "url")
        if not isinstance(where, str) or not where:
            return _opaque(tool_raw)
        effect = Effect(EffectKind.NETWORK, where, tool_raw, detail="fetch")
        return Analysis((effect,), (), (), False)
    if tool is Tool.OTHER and tool_raw == "execute":
        # OpenCode Code Mode: agent-written JavaScript that calls other tools.
        if not isinstance(args.get("code"), str):
            return _opaque(tool_raw)
        effect = Effect(EffectKind.EXEC, tool_raw, tool_raw, detail="code")
        return Analysis((effect,), (), ("interpreter_code",), False)
    if (tool, tool_raw) in _SEARCH_TOOLS:
        path = args.get("path") or ctx.cwd
        if not isinstance(path, str):
            return _opaque(tool_raw)
        target = resolve(Word(path), ctx)
        effect = Effect(
            EffectKind.READ, target.path, tool_raw, recursive=True, dynamic=target.dynamic
        )
        return Analysis((effect,), (), (), False)
    spec = _FILE_TOOLS.get((tool, tool_raw))
    value = args.get(spec.key) if spec is not None else None
    if spec is None or not isinstance(value, str) or not value:
        return _opaque(tool_raw)
    target = resolve(Word(value), ctx)
    effect = Effect(spec.kind, target.path, tool_raw, dynamic=target.dynamic, detail=spec.detail)
    return Analysis((effect,), (), (), False)
