"""The runtime never imports research code (docs/decisions/0019).

`analysis`, `decision`, `enforcement` and `storage` are what runs on the
host; `research` (collection, labelling, training) may import them, never the
reverse. Imports under `if TYPE_CHECKING:` are type hints only and allowed.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "rootsh"
RUNTIME = ("analysis", "decision", "enforcement", "storage")


def _runtime_imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text())
    hidden: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and "TYPE_CHECKING" in ast.unparse(node.test):
            hidden |= {id(n) for child in node.body for n in ast.walk(child)}
    found = []
    for node in ast.walk(tree):
        if id(node) in hidden:
            continue
        if isinstance(node, ast.ImportFrom) and node.module:
            found.append(node.module)
        elif isinstance(node, ast.Import):
            found += [a.name for a in node.names]
    return found


def test_runtime_packages_never_import_research() -> None:
    bad = [
        f"{path.relative_to(SRC)} imports {name}"
        for group in RUNTIME
        for path in sorted((SRC / group).rglob("*.py"))
        for name in _runtime_imports(path)
        if name == "rootsh.research" or name.startswith("rootsh.research.")
    ]
    assert bad == []


def test_every_group_exists() -> None:
    assert all((SRC / g / "__init__.py").is_file() for g in (*RUNTIME, "research"))
