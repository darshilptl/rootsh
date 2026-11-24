"""P4 results: every number in docs/phases/04-trace-harness.md, from the DB.

Prints Markdown tables. Read-only: opens the research DB with create=False.

Usage: uv run python research/gold_report.py <research.db> <catalog dir> <data dir>
"""

from __future__ import annotations

import json
import sqlite3
import statistics
import sys
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from datetime import datetime
from pathlib import Path

from rootsh.analysis.effects import Context, analyze_event
from rootsh.analysis.events import Agent, AgentEvent, EventKind, Tool
from rootsh.analysis.provenance import Session, Source
from rootsh.research.collect import load_catalog
from rootsh.research.collect.runner import CLAUDE_TOTAL_CAP_USD, plan
from rootsh.research.collect.store import Identity
from rootsh.research.collect.verify import verify
from rootsh.storage.db import DatabaseKind, open_database

CTX = Context("/work", "/home/u")


def table(headers: Sequence[str], rows: Iterable[Sequence[object]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    lines += ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]
    return "\n".join(lines)


def percentile(values: Sequence[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(fraction * (len(ordered) - 1)))]


def load_events(conn: sqlite3.Connection, tid: str, agent: Agent) -> list[AgentEvent]:
    rows = conn.execute(
        "SELECT kind, turn_id, tool_call_id, tool, tool_raw, args_json, text, truncated, cwd, "
        "received_at, raw_sha256 FROM trace_events WHERE trace_id = ? ORDER BY seq",
        (tid,),
    ).fetchall()
    return [
        AgentEvent(
            agent=agent,
            kind=EventKind(kind),
            session_id=tid,
            turn_id=turn,
            tool_call_id=call,
            tool=None if tool is None else Tool(tool),
            tool_raw=raw_tool,
            args=json.loads(args),
            text=text,
            truncated=bool(truncated),
            cwd=cwd,
            received_at=datetime.fromisoformat(at),
            raw_sha256=digest,
        )
        for kind, turn, call, tool, raw_tool, args, text, truncated, cwd, at, digest in rows
    ]


def source_class(best: Iterable[Source], kind: str) -> str:
    sources = set(best)
    if not sources:
        # A program name (`make`, `cc`) is common knowledge, not a fabrication:
        # keep it apart from untraced paths, URLs and other targets.
        return "nowhere: program name" if kind == "exec" else "nowhere: other target"
    if Source.USER in sources:
        return "user"
    if sources == {Source.CONTENT}:
        return "content only"
    return "+".join(sorted(sources))


def main(db: Path, catalog_dir: Path, data: Path) -> int:
    catalog = load_catalog(catalog_dir)
    intent = {s.id: s.intent.value for s in catalog}
    planned = Counter(r.agent.value for r in plan(catalog))
    conn = open_database(db, DatabaseKind.RESEARCH, create=False)
    runs = conn.execute(
        "SELECT t.id, t.agent, t.scenario, r.exit_code, r.timed_out, r.duration_ms, r.cost_usd, "
        "r.denied, r.events_rejected, r.workspace_sha256 != r.final_sha256, r.attempt, "
        "(SELECT count(*) FROM trace_events e WHERE e.trace_id = t.id), "
        "r.agent_error IS NOT NULL, r.hook_errors, r.recorder_loaded, "
        "json_array_length(r.home_files_json) "
        "FROM traces t JOIN trace_runs r ON r.trace_id = t.id ORDER BY t.id"
    ).fetchall()

    print("## Runs\n")
    rows = []
    for agent in (Agent.OPENCODE, Agent.CLAUDE_CODE):
        mine = [r for r in runs if r[1] == agent.value]
        rows.append(
            (
                agent.value,
                planned[agent.value],
                len(mine),
                sum(r[3] == 0 for r in mine),
                sum(r[3] not in (0, None) for r in mine),
                sum(r[4] for r in mine),
                sum(r[11] == 0 for r in mine),
                sum(r[11] for r in mine),
                sum(r[8] for r in mine),
                sum(r[7] for r in mine),
                sum(r[9] for r in mine),
                sum(r[12] for r in mine),
                sum(r[13] for r in mine),
                sum(1 - r[14] for r in mine),
                sum(r[15] > 0 for r in mine),
            )
        )
    headers = (
        "agent",
        "planned",
        "stored",
        "exit 0",
        "exit ≠ 0",
        "timed out",
        "0 events",
        "events",
        "rejected lines",
        "denied calls",
        "workspace changed",
        "agent error",
        "hook errors",
        "recorder missing",
        "left files in home",
    )
    print(table(headers, rows))

    print("\n## Duration and cost\n")
    rows = []
    for agent in (Agent.OPENCODE, Agent.CLAUDE_CODE):
        secs = [r[5] / 1000 for r in runs if r[1] == agent.value]
        costs = [r[6] for r in runs if r[1] == agent.value and r[6] is not None]
        if not secs:
            continue
        rows.append(
            (
                agent.value,
                f"{statistics.median(secs):.1f}",
                f"{percentile(secs, 0.9):.1f}",
                f"{max(secs):.1f}",
                f"{sum(secs) / 3600:.2f}",
                f"${sum(costs):.4f}" if costs else "-",
                f"${max(costs):.4f}" if costs else "-",
                sum(1 for r in runs if r[1] == agent.value and r[6] is None)
                if agent is Agent.CLAUDE_CODE
                else "-",
            )
        )
    print(
        table(
            ("agent", "p50 s", "p90 s", "max s", "total h", "spend", "max run", "cost unknown"),
            rows,
        )
    )
    print(f"\nClaude Code cap: ${CLAUDE_TOTAL_CAP_USD:.2f}")

    print("\n## Proposed tool calls\n")
    tools: Counter[tuple[str, str]] = Counter()
    args_by_intent: dict[str, Counter[str]] = defaultdict(Counter)
    arg_kinds: Counter[str] = Counter()
    opaque: Counter[str] = Counter()
    unknown: Counter[str] = Counter()
    critical_sets: dict[tuple[str, str], list[frozenset[tuple[str, str]]]] = defaultdict(list)
    actions_with_args = total_args = 0
    for tid, agent_name, scenario, *_ in runs:
        agent = Agent(agent_name)
        session = Session(CTX)
        targets: set[tuple[str, str]] = set()
        for event in load_events(conn, tid, agent):
            if event.kind is EventKind.TOOL_PRE and event.tool is not None:
                tools[(agent_name, event.tool.value)] += 1
                analysis = analyze_event(event, CTX)
                for reason in analysis.opaque:
                    opaque[reason.split(":", 1)[0]] += 1
                    if reason.startswith("unknown_program:"):
                        unknown[reason.split(":", 1)[1]] += 1
                provenance = session.trace(event)
                if provenance.arguments:
                    actions_with_args += 1
                for argument in provenance.arguments:
                    total_args += 1
                    arg_kinds[argument.kind.value] += 1
                    args_by_intent[intent[scenario]][
                        source_class(argument.best, argument.kind.value)
                    ] += 1
                    targets.add((argument.kind.value, argument.target))
            session.add(event)
        critical_sets[(scenario, agent_name)].append(frozenset(targets))
    print(table(("agent", "tool", "calls"), sorted((a, t, n) for (a, t), n in tools.items())))

    print(f"\n## Critical arguments\n\nactions with ≥ 1 critical argument: {actions_with_args}")
    print(f"critical arguments: {total_args} (P3 real transcripts: 4)\n")
    print(table(("effect kind", "arguments"), arg_kinds.most_common()))
    classes = sorted({c for counts in args_by_intent.values() for c in counts})
    order = ("benign", "injection", "hallucination", "deviation")
    print()
    print(
        table(
            ("origin", *order),
            [(c, *(args_by_intent[i][c] for i in order)) for c in classes],
        )
    )

    print("\n## Effect extractor coverage on real commands\n")
    print(table(("opaque reason", "count"), opaque.most_common()))
    print()
    print(table(("unknown program", "count"), unknown.most_common(25)))

    print("\n## Repeat runs (OpenCode, same scenario 3 times)\n")
    rows = []
    for (scenario, _agent), sets in sorted(critical_sets.items()):
        if len(sets) < 2:
            continue
        rows.append((scenario, len(sets), len(set(sets)), "yes" if len(set(sets)) == 1 else "no"))
    print(table(("scenario", "runs", "distinct critical-argument sets", "identical"), rows))

    print("\n## By design intent\n")
    rows = []
    for name in order:
        mine = [r for r in runs if intent[r[2]] == name]
        rows.append(
            (
                name,
                len(mine),
                sum(r[9] for r in mine),
                sum(r[7] > 0 for r in mine),
            )
        )
    print(table(("intent", "runs", "workspace changed", "runs with a denied call"), rows))

    problems = verify(conn, catalog, data, Identity.current())
    print(f"\n## Verify\n\n{len(runs)} runs verified, {len(problems)} problems")
    for problem in problems:
        print(f"- {problem}")
    conn.close()
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])))
