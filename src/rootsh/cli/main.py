"""`rootsh` command line."""

from __future__ import annotations

import argparse
import fcntl
import os
import sqlite3
import sys
from collections.abc import Sequence
from pathlib import Path

from rootsh import __version__
from rootsh.collect import keyproxy
from rootsh.collect.runner import (
    CLAUDE_RUN_CAP_USD,
    OPENCODE_MODEL,
    Backend,
    CollectionStopError,
    Host,
    PlannedRun,
    agent_version,
    budget_left,
    collect,
    plan,
)
from rootsh.collect.scenario import CatalogError, load_catalog
from rootsh.collect.store import Identity, is_collected, rebuild
from rootsh.collect.verify import verify
from rootsh.daemon import cli as daemon_cli
from rootsh.events import Agent
from rootsh.install import cli as install_cli
from rootsh.labels import cli as labels_cli
from rootsh.model import cli as model_cli
from rootsh.model.bundle import BundleError
from rootsh.model.data import FrozenDataError
from rootsh.model.evaluate import AlreadyEvaluatedError
from rootsh.storage.db import DatabaseKind, DatabaseKindError, open_database
from rootsh.storage.migrations import MigrationError, MigrationStatus, migrate, status
from rootsh.storage.schema import MIGRATIONS

EXIT_OK, EXIT_FAILED, EXIT_USAGE = 0, 1, 2
NVIDIA_KEY_ENV = "NVIDIA_API_KEY"
_RUNTIME = Path(os.environ.get("XDG_RUNTIME_DIR", "/run/user/0"))
DEFAULT_SOCKET = _RUNTIME / "rootsh" / "rootsh.sock"
DEFAULT_CONTROL = _RUNTIME / "rootsh-control" / "control.sock"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="rootsh", description=__doc__)
    parser.add_argument("--version", action="version", version=f"rootsh {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)

    db = commands.add_parser("db", help="manage rootsh databases")
    db_commands = db.add_subparsers(dest="db_command", required=True)
    for name, text in (
        ("migrate", "apply pending migrations (creates the database if missing)"),
        ("status", "show applied and pending migrations without changing anything"),
    ):
        sub = db_commands.add_parser(name, help=text)
        sub.add_argument("kind", choices=[kind.label for kind in DatabaseKind])
        sub.add_argument("path", type=Path)

    collect = commands.add_parser("collect", help="gold-set trace collection (research)")
    collect_commands = collect.add_subparsers(dest="collect_command", required=True)
    for name, text in (
        ("run", "run planned scenarios in the sandbox and store their traces"),
        ("verify", "check every stored run against its hashes and raw file"),
        ("rebuild", "re-derive stored events and outputs from the raw files"),
        ("plan", "list planned runs and whether each is collected"),
    ):
        sub = collect_commands.add_parser(name, help=text)
        sub.add_argument("--catalog", type=Path, required=True, help="scenario directory")
        sub.add_argument("--db", type=Path, required=True, help="research database")
        sub.add_argument("--data", type=Path, required=True, help="raw trace directory")
        if name == "run":
            sub.add_argument("--agent", choices=[a.value for a in Agent], required=True)
            sub.add_argument("--only", nargs="+", default=[], help="scenario ids")
            sub.add_argument("--limit", type=int, default=None, help="at most N runs")
            sub.add_argument("--toolenv", type=Path, default=None, help="venv offered to agents")
            sub.add_argument(
                "--model", default=OPENCODE_MODEL, help="OpenCode model (NVIDIA NIM, via key proxy)"
            )
    label = commands.add_parser("label", help="label gold-set steps, one key per answer")
    label.add_argument("--db", type=Path, required=True, help="research database")
    label.add_argument(
        "--rubric", type=Path, default=Path("docs/labelling-rubric.md"), help="shown on `?`"
    )
    label.add_argument("--variants", action="store_true", help="the blind tier-2 variant audit")
    label.add_argument(
        "--adjudicate", action="store_true", help="second look at refused R1-audit steps"
    )
    variants = commands.add_parser("variants", help="generate tier-2 variants from final labels")
    variants.add_argument("--db", type=Path, required=True, help="research database")
    variants.add_argument("--catalog", type=Path, required=True, help="scenario directory")
    dataset = commands.add_parser("freeze", help="audit, gate and freeze the labelled dataset")
    dataset.add_argument("--db", type=Path, required=True, help="research database")
    dataset.add_argument("--out", type=Path, required=True, help="manifest directory")
    model = commands.add_parser("model", help="train, or evaluate heldout once (design 06)")
    model.add_argument("model_command", choices=("train", "evaluate"))
    model.add_argument("--db", type=Path, required=True, help="research database")
    model.add_argument("--manifest", type=Path, required=True, help="frozen manifest file")
    model.add_argument("--out", type=Path, required=True, help="model bundle directory")
    daemon = commands.add_parser("daemon", help="run the decision daemon (design 07)")
    daemon.add_argument("--socket", type=Path, default=DEFAULT_SOCKET, help="Unix socket path")
    daemon.add_argument("--db", type=Path, required=True, help="runtime database")
    daemon.add_argument("--bundle", type=Path, required=True, help="model bundle directory")
    daemon.add_argument(
        "--control", type=Path, default=DEFAULT_CONTROL, help="control socket (approve, launches)"
    )
    approve = commands.add_parser("approve", help="answer a pending desktop ask")
    approve.add_argument("id", help="decision id (shown in the agent's block reason)")
    approve.add_argument("answer", choices=("allow", "block"))
    approve.add_argument(
        "--control", type=Path, default=DEFAULT_CONTROL, help="the daemon's control socket"
    )
    run = commands.add_parser("run", help="start an agent under rootsh's kernel layers (design 09)")
    run.add_argument("agent", choices=("claude", "opencode"))
    run.add_argument("--project", type=Path, help="project directory (default: here)")
    run.add_argument(
        "--env", action="append", default=[], metavar="NAME", help="pass one more variable"
    )
    run.add_argument("--socket", type=Path, default=DEFAULT_SOCKET, help="event socket")
    run.add_argument("--control", type=Path, default=DEFAULT_CONTROL, help="control socket")
    run.add_argument("agent_args", nargs="*", help="-- then the agent's own arguments")
    install_cli.add_parsers(commands)
    return parser


def _describe(kind: DatabaseKind, path: Path, current: MigrationStatus) -> str:
    names = {m.version: m.name for m in MIGRATIONS[kind]}

    def listing(versions: tuple[int, ...]) -> str:
        if not versions:
            return "none"
        return ", ".join(f"{v} ({names.get(v, 'unknown')})" for v in versions)

    lines = [
        f"{kind.label} database: {path}",
        f"applied: {listing(current.applied)}",
        f"pending: {listing(current.pending)}",
        f"status: {'ok' if current.ok else 'PROBLEM'}",
        *(f"  - {problem}" for problem in current.problems),
    ]
    return "\n".join(lines)


def _db(command: str, kind: DatabaseKind, path: Path) -> int:
    conn = open_database(path, kind, create=command == "migrate")
    try:
        migrations = MIGRATIONS[kind]
        if command == "migrate":
            applied = migrate(conn, migrations)
            print(
                f"applied now: {', '.join(map(str, applied)) if applied else 'none (up to date)'}"
            )
        current = status(conn, migrations)
        print(_describe(kind, path, current))
        return EXIT_OK if current.ok and not current.pending else EXIT_FAILED
    finally:
        conn.close()


def _open_research(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = open_database(path, DatabaseKind.RESEARCH, create=True)
    migrate(conn, MIGRATIONS[DatabaseKind.RESEARCH])
    return conn


def _collect(args: argparse.Namespace) -> int:
    catalog = load_catalog(args.catalog)
    conn = _open_research(args.db)
    try:
        if args.collect_command == "verify":
            problems = verify(conn, catalog, args.data, Identity.current())
            (count,) = conn.execute("SELECT count(*) FROM trace_runs").fetchone()
            print(f"verified {count} runs: {len(problems)} problems")
            for problem in problems:
                print(f"  - {problem}")
            return EXIT_OK if not problems else EXIT_FAILED
        if args.collect_command == "rebuild":
            with (args.data / "collect.lock").open("w") as lock:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    print("rootsh: a collection is running on this data directory", file=sys.stderr)
                    return EXIT_FAILED
                done = rebuild(conn, args.data, Identity.current())
            print(
                f"rebuilt {done.runs} runs: events {done.events_before} -> {done.events_after} "
                f"({done.events_changed_runs} runs changed), "
                f"{done.outputs_rewritten} outputs re-sanitized"
            )
            return EXIT_OK
        planned = plan(catalog)
        if args.collect_command == "plan":
            for run in planned:
                print(f"{'done' if is_collected(conn, run.trace_id) else 'todo'}  {run.trace_id}")
            return EXIT_OK
        return _collect_run(args, conn, planned)
    finally:
        conn.close()


def _collect_run(
    args: argparse.Namespace, conn: sqlite3.Connection, planned: Sequence[PlannedRun]
) -> int:
    agent = Agent(args.agent)
    only = set(args.only)
    todo = [
        run
        for run in planned
        if run.agent is agent
        and (not only or run.scenario.id in only)
        and not is_collected(conn, run.trace_id)
    ][: args.limit]
    host = Host.detect(args.toolenv.resolve() if args.toolenv else None)
    version = agent_version(agent)
    print(f"{len(todo)} runs to collect with {version}", flush=True)
    args.data.mkdir(parents=True, exist_ok=True)
    # One collector per data directory: two would run the same scenario twice.
    with (args.data / "collect.lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("rootsh: another collection is running on this data directory", file=sys.stderr)
            return EXIT_FAILED
        proxy: keyproxy.KeyProxy | None = None
        backend: Backend | None = None
        if agent is Agent.OPENCODE and todo:
            if not args.model.startswith("nvidia/"):
                print("rootsh: OpenCode collection uses NVIDIA NIM models only", file=sys.stderr)
                return EXIT_FAILED
            api_key = os.environ.get(NVIDIA_KEY_ENV, "")
            if not api_key.strip():
                print(f"rootsh: set {NVIDIA_KEY_ENV} in this shell first", file=sys.stderr)
                return EXIT_FAILED
            proxy = keyproxy.start(api_key)
            backend = Backend(args.model, proxy.base_url, proxy.access_token, proxy.port)
        try:
            return _collect_loop(todo, host, args.data, conn, version, agent, backend)
        finally:
            if proxy is not None:
                stats = proxy.stats
                print(
                    f"key proxy: {stats.forwarded} forwarded, {stats.refused} refused, "
                    f"{stats.dropped} client hang-ups",
                    flush=True,
                )
                proxy.stop()


def _collect_loop(
    todo: Sequence[PlannedRun],
    host: Host,
    data: Path,
    conn: sqlite3.Connection,
    version: str,
    agent: Agent,
    backend: Backend | None,
) -> int:
    for index, run in enumerate(todo, start=1):
        if agent is Agent.CLAUDE_CODE and budget_left(conn) < CLAUDE_RUN_CAP_USD:
            print(f"stopping: Claude Code budget left ${budget_left(conn):.4f}", flush=True)
            return EXIT_FAILED
        try:
            summary = collect(run, host, data, conn, version, backend)
        except CollectionStopError as exc:
            print(f"stopping: {exc}", flush=True)
            return EXIT_FAILED
        print(f"[{index}/{len(todo)}] {summary}", flush=True)
    return EXIT_OK


def _model(args: argparse.Namespace) -> int:
    conn = _open_research(args.db)
    try:
        if args.model_command == "train":
            return model_cli.train(conn, args.manifest, args.out)
        return model_cli.evaluate_heldout(conn, args.manifest, args.out)
    finally:
        conn.close()


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "collect":
            return _collect(args)
        if args.command == "label":
            return labels_cli.main(
                args.db, args.rubric, variants=args.variants, adjudicate_only=args.adjudicate
            )
        if args.command == "variants":
            return labels_cli.generate_variants(args.db, args.catalog)
        if args.command == "freeze":
            return labels_cli.freeze_dataset(args.db, args.out)
        if args.command == "model":
            return _model(args)
        if args.command == "daemon":
            return daemon_cli.serve(args.socket, args.control, args.db, args.bundle)
        if args.command == "run":
            from rootsh import run as run_cli

            extra = args.agent_args[1:] if args.agent_args[:1] == ["--"] else args.agent_args
            agent = "claude_code" if args.agent == "claude" else "opencode"
            return run_cli.main(agent, args.project, extra, args.env, args.socket, args.control)
        if args.command == "approve":
            return daemon_cli.approve(args.control, args.id, args.answer)
        if args.command in ("install", "uninstall", "doctor"):
            return install_cli.run(args)
        return _db(args.db_command, DatabaseKind.from_label(args.kind), args.path)
    except (AlreadyEvaluatedError, BundleError, FrozenDataError) as exc:
        print(f"rootsh: model error: {exc}", file=sys.stderr)
        return EXIT_FAILED
    except CatalogError as exc:
        print(f"rootsh: catalog error: {exc}", file=sys.stderr)
        return EXIT_FAILED
    except (DatabaseKindError, MigrationError, sqlite3.Error) as exc:
        print(f"rootsh: error: {exc}", file=sys.stderr)
        return EXIT_FAILED
