"""`rootsh collect verify`: every stored run still matches its hashes, its
events are exactly what its raw file normalizes to, and nothing stored
identifies the author."""

from __future__ import annotations

import hashlib
import sqlite3
import tempfile
from collections.abc import Sequence
from pathlib import Path

from rootsh.collect.scenario import Scenario
from rootsh.collect.store import Identity, event_rows, parse_raw, sanitize, stored_event_rows
from rootsh.collect.workspace import build, workspace_sha256
from rootsh.events import Agent


def rebuilt_sha256(scenario: Scenario) -> str:
    with tempfile.TemporaryDirectory(prefix="rootsh-verify-") as tmp:
        root = Path(tmp) / "ws"
        build(scenario, root)
        return workspace_sha256(root, git=scenario.git)


def _leaks(conn: sqlite3.Connection, who: Identity) -> list[str]:
    """Stored text the sanitizer would still change: the author's identity."""
    found = []
    for table, key, columns in (
        ("trace_events", "trace_id || '#' || seq", ("args_json", "text", "cwd")),
        ("trace_runs", "trace_id", ("agent_error", "home_files_json")),
        ("traces", "id", ("notes",)),
    ):
        for column in columns:
            for where, value in conn.execute(
                f"SELECT {key}, {column} FROM {table} WHERE {column} IS NOT NULL"  # noqa: S608
            ):
                if sanitize(value, who) != value:
                    found.append(f"{where}: {table}.{column} identifies the author")
    return found


def verify(
    conn: sqlite3.Connection, catalog: Sequence[Scenario], data: Path, who: Identity
) -> list[str]:
    """Problems found; empty means every run verifies. `who` is the author who
    collected: the raw files hold their identity and are parsed with it."""
    by_id = {s.id: s for s in catalog}
    rebuilt: dict[str, str] = {}
    problems: list[str] = []
    rows = conn.execute(
        "SELECT t.id, t.agent, t.scenario, r.scenario_sha256, r.workspace_sha256, "
        "r.raw_sha256, r.raw_path, r.output_sha256, r.output_path, r.denied, "
        "r.events_rejected, r.recorder_loaded "
        "FROM traces t JOIN trace_runs r ON r.trace_id = t.id ORDER BY t.id"
    ).fetchall()
    for (
        tid, agent, sid, scenario_sha, workspace_sha, raw_sha, raw_path, out_sha, out_path,
        denied, rejected, loaded,
    ) in rows:  # fmt: skip
        files = (("raw trace", raw_path, raw_sha), ("output", out_path, out_sha))
        content: dict[str, bytes] = {}
        for label, relative, expected in files:
            path = data / relative
            if not path.is_file():
                problems.append(f"{tid}: {label} {relative} is missing")
                continue
            content[label] = path.read_bytes()
            if hashlib.sha256(content[label]).hexdigest() != expected:
                problems.append(f"{tid}: {label} {relative} does not match its sha256")
        if "raw trace" in content:
            parsed = parse_raw(content["raw trace"].decode("utf-8", "replace"), Agent(agent), who)
            if event_rows(tid, parsed.events) != stored_event_rows(conn, tid):
                problems.append(f"{tid}: stored events differ from its raw file (rebuild)")
            if (parsed.denied, parsed.rejected, int(parsed.recorder_loaded)) != (
                denied, rejected, loaded,
            ):  # fmt: skip
                problems.append(f"{tid}: stored counts differ from its raw file (rebuild)")
        if "output" in content:
            text = content["output"].decode("utf-8", "replace")
            if sanitize(text, who) != text:
                problems.append(f"{tid}: output {out_path} identifies the author")
        scenario = by_id.get(sid)
        if scenario is None:
            problems.append(f"{tid}: scenario {sid} is not in the catalog")
            continue
        if scenario.sha256 != scenario_sha:
            problems.append(f"{tid}: scenario {sid} changed since the run")
        if sid not in rebuilt:
            rebuilt[sid] = rebuilt_sha256(scenario)
        if rebuilt[sid] != workspace_sha:
            problems.append(f"{tid}: workspace of {sid} no longer rebuilds identically")
    (count,) = conn.execute("SELECT count(*) FROM traces").fetchone()
    if count != len(rows):
        problems.append(f"{count - len(rows)} traces have no trace_runs row")
    return problems + _leaks(conn, who)
