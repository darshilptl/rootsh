"""How a step is shown to the labeller. Never shows the scenario, the trace
id (it contains the scenario name), or any earlier answer."""

from __future__ import annotations

import json
from collections.abc import Mapping

from rootsh.collect.store import SANITIZED_HOME
from rootsh.events import AgentEvent, EventKind
from rootsh.hoststate.sensitivity import classify
from rootsh.labels.steps import Step
from rootsh.provenance import Source

RECENT = 12  # history lines shown by default; `h` shows all
LINE = 110
BLOCK = 24  # lines of written or edited content shown

_ORIGIN = {
    Source.USER: "user prompt",
    Source.CONTENT: "file / tool output",
    Source.WORKSPACE: "workspace listing",
    Source.OWN: "agent's own earlier write",
}


def _cut(text: str, width: int = LINE) -> str:
    one = " ⏎ ".join(line.strip() for line in text.strip().splitlines() if line.strip())
    return one if len(one) <= width else one[: width - 1] + "…"


def _block(text: str, indent: str = "    ") -> list[str]:
    lines = text.splitlines() or [""]
    shown = [indent + line[:LINE] for line in lines[:BLOCK]]
    if len(lines) > BLOCK:
        shown.append(f"{indent}… {len(lines) - BLOCK} more lines")
    return shown


def _args_text(args: Mapping[str, object]) -> str:
    return json.dumps(args, default=dict, ensure_ascii=False)


def action_lines(event: AgentEvent) -> list[str]:
    args = event.args
    tool = event.tool_raw or "?"
    command = args.get("command")
    if isinstance(command, str):
        return [f"  {tool}", *_block(command)]
    path = args.get("file_path") or args.get("filePath") or args.get("path")
    content = args.get("content")
    if isinstance(content, str):
        return [f"  {tool}  {path}", "    --- content ---", *_block(content)]
    old, new = (
        args.get("old_string") or args.get("oldString"),
        args.get("new_string") or args.get("newString"),
    )
    if isinstance(old, str) and isinstance(new, str):
        return [
            f"  {tool}  {path}",
            "    --- replace ---",
            *_block(old),
            "    --- with ---",
            *_block(new),
        ]
    return [f"  {tool}  {_cut(_args_text(args))}"]


def _history_line(number: int, event: AgentEvent, *, full: bool) -> list[str]:
    if event.kind is EventKind.PROMPT:
        return [f"{number:>4} USER: {event.text or ''}"]
    if event.kind is EventKind.TOOL_PRE:
        command = event.args.get("command")
        shown = command if isinstance(command, str) else _args_text(event.args)
        return [f"{number:>4} → {event.tool_raw}: {_cut(shown, LINE - 12)}"]
    text = event.text or ""
    lines = text.splitlines()
    if full:
        return [f"{number:>4} ← output ({len(lines)} lines)", *_block(text, "       ")]
    first = _cut(text, LINE - 30) if text.strip() else "(empty)"
    return [f"{number:>4} ← {len(lines)} lines: {first}"]


def history_lines(step: Step, *, full: bool) -> list[str]:
    events = list(enumerate(step.history, start=1))
    hidden = 0 if full else max(0, len(events) - RECENT)
    out = [
        f"HISTORY ({len(events)} events" + (f", {hidden} earlier hidden: h" if hidden else "") + ")"
    ]
    for number, event in events[hidden:]:
        out += _history_line(number, event, full=full)
    return out


def effect_lines(step: Step) -> list[str]:
    out = ["EFFECTS"]
    for e in step.analysis.effects:
        flags = [
            f
            for f, on in (("recursive", e.recursive), ("glob", e.glob), ("dynamic", e.dynamic))
            if on
        ]
        classes = sorted(classify(e.target, SANITIZED_HOME)) if e.target.startswith("/") else []
        extra = ", ".join([*([e.detail] if e.detail else []), *flags, *(f"⚠ {c}" for c in classes)])
        dest = f" → {e.dest}" if e.dest else ""
        out.append(f"  {e.kind.value:<10} {e.target}{dest}" + (f"   ({extra})" if extra else ""))
    if not step.analysis.effects:
        out.append("  (none)")
    if step.analysis.opaque:
        out.append(f"  opaque: {', '.join(step.analysis.opaque)} (what it does cannot be read off)")
    return out


def origin_lines(step: Step) -> list[str]:
    if not step.provenance.arguments:
        return []
    out = ["WHERE EACH CHOSEN TARGET CAME FROM"]
    for argument in step.provenance.arguments:
        if not argument.best:
            out.append(f"  {argument.target}  ← nowhere (not in any prompt or output)")
            continue
        parts = [
            f"{_ORIGIN[source]} ({match.relation.value}: {_cut(match.mention, 40)})"
            for source, match in sorted(argument.best.items(), key=lambda kv: kv[0].value)
        ]
        out.append(f"  {argument.target}  ← " + "; ".join(parts))
    return out


def render(step: Step, header: str, *, full: bool = False) -> str:
    prompts = [e.text or "" for e in step.history if e.kind is EventKind.PROMPT]
    lines = [
        header,
        f"agent: {step.trace.agent.value}   cwd: {step.action.cwd or '/work'}",
        "USER PROMPT",
    ]
    lines += [f"  {p}" for p in prompts] or ["  (none)"]
    lines += history_lines(step, full=full)
    lines += ["ACTION", *action_lines(step.action)]
    lines += effect_lines(step) + origin_lines(step)
    return "\n".join(lines)
