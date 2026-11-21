"""`rootsh install | uninstall | doctor` (design 08 §4-§5)."""

from __future__ import annotations

import argparse
import getpass
import os
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

from rootsh.install import (
    InstallError,
    Layout,
    Systemctl,
    install_system,
    install_user,
    uninstall_system,
    uninstall_user,
)
from rootsh.install.doctor import doctor

REPO = Path(__file__).resolve().parents[3]
DEFAULT_BUNDLE = REPO / "data" / "models" / "p6-a357bba6c02c"


def add_parsers(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    install = commands.add_parser("install", help="install hooks, plugin and daemon (design 08)")
    install.add_argument(
        "--system", action="store_true", help="the root step: Claude Code managed settings"
    )
    install.add_argument(
        "--bundle", type=Path, default=DEFAULT_BUNDLE, help="model bundle to install"
    )
    uninstall = commands.add_parser("uninstall", help="revert what install did")
    uninstall.add_argument("--system", action="store_true", help="the root step")
    uninstall.add_argument(
        "--force", action="store_true", help="also remove files changed since install"
    )
    uninstall.add_argument("--keep-db", action="store_true", help="keep the runtime decisions DB")
    check = commands.add_parser("doctor", help="check every installed layer, live")
    for sub in (install, uninstall, check):
        sub.add_argument("--user", help="whose install (default: you, or SUDO_USER under sudo)")
        sub.add_argument("--root", type=Path, default=Path("/"), help="re-root all paths (tests)")


def systemctl(args: Sequence[str]) -> int:
    return subprocess.run(["systemctl", *args], check=False).returncode  # noqa: S603, S607


def _layout(args: argparse.Namespace) -> Layout:
    name = args.user or os.environ.get("SUDO_USER") or getpass.getuser()
    # sys.executable, not resolved: the venv's python, which finds rootsh.
    return Layout.for_user(name, Path(sys.executable).absolute(), args.root.resolve())


def run(args: argparse.Namespace) -> int:
    layout = _layout(args)
    live: Systemctl | None = systemctl if layout.root == Path("/") else None
    try:
        if args.command == "doctor":
            checks = doctor(layout, live)
            for check in checks:
                print(check.line())
            return 1 if any(c.ok is False for c in checks) else 0
        if args.command == "install":
            lines = (
                install_system(layout) if args.system else install_user(layout, args.bundle, live)
            )
        elif args.system:
            lines = uninstall_system(layout)
        else:
            lines = uninstall_user(layout, live, force=args.force, keep_db=args.keep_db)
    except InstallError as exc:
        print(f"rootsh {args.command}: {exc}", file=sys.stderr)
        return 1
    for line in lines:
        print(line)
    return 1 if any(line.startswith("NOT REMOVED") for line in lines) else 0
