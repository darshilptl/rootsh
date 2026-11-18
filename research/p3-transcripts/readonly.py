"""Is a shell command read-only? Rootsh's own effect extractor decides.

Used by both recorders so agents can explore (`ls`, `cat`, `find`) while
anything that writes, deletes, executes unknown code, or touches the
network stays blocked. Exit 0 = read-only, 1 = not.

Usage: readonly.py <cwd> < command
"""

from __future__ import annotations

import sys
from pathlib import Path

from rootsh.effects import Context, EffectKind, analyze_shell


def is_read_only(command: str, cwd: str) -> bool:
    analysis = analyze_shell(command, Context(cwd, str(Path.home())))
    return not analysis.opaque and all(e.kind is EffectKind.READ for e in analysis.effects)


if __name__ == "__main__":
    sys.exit(0 if is_read_only(sys.stdin.read(), sys.argv[1]) else 1)
