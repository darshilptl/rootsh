"""Apply K2 (check), K1 and K3 to this process, then exec the agent (design 09 §5).

Usage: python -I -m rootsh.kernel.confine <spec-json> -- <command...>

The spec is written by the launcher (`rootsh.run`); it is an argument, not
the environment, which the confined tree can change. Every failure exits
126 before the command runs.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

from rootsh.kernel import apparmor, landlock, seccomp

EXIT_REFUSED = 126


@dataclass(frozen=True, slots=True)
class Spec:
    read_write: tuple[str, ...]
    read_only: tuple[str, ...]
    connect_ports: tuple[int, ...] = (443,)
    bind_ports: tuple[int, ...] = ()
    apparmor_label: str | None = None  # required prefix of our label, or None

    def dump(self) -> str:
        return json.dumps(asdict(self), sort_keys=True)

    @classmethod
    def load(cls, text: str) -> Spec:
        raw = json.loads(text)
        if not isinstance(raw, Mapping):
            raise ValueError("spec is not an object")
        label = raw.get("apparmor_label")
        spec = cls(
            tuple(str(p) for p in raw["read_write"]),
            tuple(str(p) for p in raw["read_only"]),
            tuple(int(p) for p in raw["connect_ports"]),
            tuple(int(p) for p in raw["bind_ports"]),
            None if label is None else str(label),
        )
        for path in (*spec.read_write, *spec.read_only):
            if not path.startswith("/"):
                raise ValueError(f"relative rule path {path!r}")
        return spec


def apply(spec: Spec) -> list[str]:
    """Every layer, in order; returns one line per layer for the launch log."""
    lines = []
    label = apparmor.current()
    if spec.apparmor_label is not None:
        if not label.startswith(spec.apparmor_label):
            raise OSError(0, f"K2 expected AppArmor {spec.apparmor_label}, have {label!r}")
        lines.append(f"K2 {label}")
    abi = landlock.apply(
        landlock.Rules(
            [Path(p) for p in spec.read_write],
            [Path(p) for p in spec.read_only],
            spec.connect_ports,
            spec.bind_ports,
        )
    )
    lines.append(f"K1 landlock abi {abi}")
    seccomp.apply()
    lines.append(f"K3 seccomp {len(seccomp.DENIED)} syscalls + ioctl TIOCSTI/TIOCLINUX")
    return lines


def main(argv: Sequence[str]) -> int:
    if len(argv) < 3 or argv[1] != "--":
        print("usage: python -m rootsh.kernel.confine <spec-json> -- <command...>", file=sys.stderr)
        return EXIT_REFUSED
    try:
        lines = apply(Spec.load(argv[0]))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"rootsh confine: refusing to start: {exc}", file=sys.stderr)
        return EXIT_REFUSED
    if os.environ.get("ROOTSH_CONFINE_VERBOSE") == "1":
        for line in lines:
            print(f"rootsh confine: {line}", file=sys.stderr)
    command = list(argv[2:])
    try:
        os.execvp(command[0], command)  # noqa: S606 - the confined agent command
    except OSError as exc:
        print(f"rootsh confine: cannot start {command[0]}: {exc}", file=sys.stderr)
    return EXIT_REFUSED


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
