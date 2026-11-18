"""Compare bash parsers for the effect extractor (P2).

For each corpus entry the expected output is hand-labelled bash semantics:
the simple-command names that would run (in source order, including inside
substitutions, loops, and functions) and the file-redirection targets. An
entry with `commands: null` is malformed and must be flagged, not guessed.

Usage: <venv>/bin/python parser_bench.py corpus.json
"""

from __future__ import annotations

import json
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import bashlex
import tree_sitter_bash
from tree_sitter import Language, Node, Parser


@dataclass(frozen=True)
class Parsed:
    ok: bool
    commands: list[str]
    redirects: list[str]


def _unquote(text: str) -> str:
    return text[1:-1] if len(text) >= 2 and text[0] == text[-1] and text[0] in "'\"" else text


# --- tree-sitter -----------------------------------------------------------

_TS_PARSER = Parser(Language(tree_sitter_bash.language()))


def _is_fd_duplication(redirect: Node, destination: str) -> bool:
    # `2>&1` / `<&-` duplicate or close a descriptor; `&> file` and
    # `>& file` (non-numeric target) still write a file.
    operators = {child.type for child in redirect.children if not child.is_named}
    return bool(operators & {">&", "<&"}) and (destination.isdigit() or destination == "-")


def _ts_walk(node: Node, commands: list[str], redirects: list[str]) -> None:
    if node.type == "command":
        name = node.child_by_field_name("name")
        if name is not None:
            commands.append(_unquote(name.text.decode()))
    elif node.type == "file_redirect":
        destination = node.child_by_field_name("destination")
        if destination is not None and not _is_fd_duplication(node, destination.text.decode()):
            redirects.append(_unquote(destination.text.decode()))
    for child in node.children:
        _ts_walk(child, commands, redirects)


def parse_tree_sitter(cmd: str) -> Parsed:
    tree = _TS_PARSER.parse(cmd.encode())
    commands: list[str] = []
    redirects: list[str] = []
    _ts_walk(tree.root_node, commands, redirects)
    return Parsed(not tree.root_node.has_error, commands, redirects)


# --- bashlex ---------------------------------------------------------------


class _BashlexCollector(bashlex.ast.nodevisitor):
    def __init__(self) -> None:
        self.commands: list[str] = []
        self.redirects: list[str] = []

    def visitcommand(self, node, parts):  # noqa: ANN001 - bashlex API
        for part in parts:
            if part.kind == "word":
                self.commands.append(part.word)
                break
            if part.kind != "assignment":
                break

    def visitredirect(self, node, input, type, output, heredoc):  # noqa: A002, ANN001 - bashlex API
        if type in {">", ">>", "<"} and getattr(output, "kind", None) == "word":
            self.redirects.append(output.word)


def parse_bashlex(cmd: str) -> Parsed:
    try:
        trees = bashlex.parse(cmd)
    except Exception:  # bashlex signals unsupported syntax with assorted errors
        return Parsed(False, [], [])
    collector = _BashlexCollector()
    for tree in trees:
        collector.visit(tree)
    return Parsed(True, collector.commands, collector.redirects)


# --- scoring ---------------------------------------------------------------


def score(parse: Callable[[str], Parsed], corpus: list[dict[str, object]]) -> dict[str, object]:
    correct, misses = 0, []
    started = time.perf_counter()
    for entry in corpus:
        result = parse(str(entry["cmd"]))
        if entry["commands"] is None:
            good = not result.ok
        else:
            good = result.ok and result.commands == entry["commands"] and result.redirects == entry["redirects"]
        if good:
            correct += 1
        else:
            misses.append({"cmd": entry["cmd"], "ok": result.ok, "commands": result.commands, "redirects": result.redirects})
    elapsed_ms = (time.perf_counter() - started) * 1000
    return {
        "correct": correct,
        "total": len(corpus),
        "mean_parse_ms": round(elapsed_ms / len(corpus), 3),
        "misses": misses,
    }


def main(corpus_path: Path) -> int:
    corpus = json.loads(corpus_path.read_text(encoding="utf-8"))
    report = {
        "tree_sitter_bash": score(parse_tree_sitter, corpus),
        "bashlex": score(parse_bashlex, corpus),
    }
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1])))
