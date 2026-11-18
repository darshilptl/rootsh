"""Static evaluation of shell words (design doc §3.1)."""

from __future__ import annotations

import posixpath
from dataclasses import dataclass

from tree_sitter import Node

from rootsh.effects.model import Context

# Node types that run commands when the word is expanded.
SUBSTITUTIONS = frozenset({"command_substitution", "process_substitution"})
SPECIAL_FILES = frozenset({"/dev/null", "/dev/stdout", "/dev/stderr", "/dev/tty", "/dev/stdin"})
_GLOB_CHARS = frozenset("*?[")
_ANSI_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "\\": "\\", "'": "'", '"': '"', "0": "\0"}


@dataclass(frozen=True, slots=True)
class Word:
    text: str
    dynamic: bool = False
    glob: bool = False
    pipe: bool = False  # a process substitution: a pipe, never a file target
    subtree: bool = False  # stands for paths anywhere under it (find -exec {})
    substitutions: tuple[Node, ...] = ()


def _unescape(raw: str) -> tuple[str, bool]:
    """Remove backslash escapes from an unquoted word; report unescaped globs."""
    out: list[str] = []
    glob = False
    chars = iter(raw)
    for char in chars:
        if char == "\\":
            out.append(next(chars, ""))
        else:
            glob = glob or char in _GLOB_CHARS
            out.append(char)
    return "".join(out), glob


def _ansi_c(raw: str) -> str:
    body = raw[2:-1] if raw.startswith("$'") and raw.endswith("'") else raw
    out: list[str] = []
    chars = iter(body)
    for char in chars:
        out.append(_ANSI_ESCAPES.get(next(chars, ""), "") if char == "\\" else char)
    return "".join(out)


def _expansion_name(node: Node) -> str | None:
    """`$X` or `${X}` with nothing else; anything fancier is dynamic."""
    names = [child for child in node.named_children if child.type == "variable_name"]
    if len(names) != 1:
        return None
    if node.type == "expansion" and len(node.named_children) != 1:
        return None
    return _text(names[0])


def _text(node: Node) -> str:
    return (node.text or b"").decode("utf-8", errors="replace")


def evaluate(node: Node, ctx: Context) -> Word:
    """Evaluate one argument node to its static text, flags and substitutions."""
    kind = node.type
    if kind == "word":
        raw = _text(node)
        text, glob = _unescape(raw)
        if raw == "~" or raw.startswith("~/"):
            text = ctx.home + text[1:]
        elif raw.startswith("~"):  # ~user, ~+, ~-: someone else's home or a dir stack entry
            return Word(text, dynamic=True)
        return Word(text, glob=glob)
    if kind in ("number", "string_content", "heredoc_body"):
        return Word(_text(node))
    if kind == "raw_string":
        return Word(_text(node)[1:-1])
    if kind == "ansi_c_string":
        return Word(_ansi_c(_text(node)))
    if kind in ("simple_expansion", "expansion"):
        name = _expansion_name(node)
        value = ctx.variable(name) if name is not None else None
        return Word(_text(node), dynamic=True) if value is None else Word(value)
    if kind in SUBSTITUTIONS:
        return Word(
            _text(node), dynamic=True, pipe=kind == "process_substitution", substitutions=(node,)
        )
    if kind == "command_name":
        return _join([evaluate(c, ctx) for c in node.named_children], glob_allowed=True)
    if kind in ("string", "translated_string"):
        return _join([evaluate(c, ctx) for c in node.named_children], glob_allowed=False)
    if kind == "concatenation":
        return _join([evaluate(c, ctx) for c in node.named_children], glob_allowed=True)
    # Arithmetic, brace expressions, and anything unknown: keep the text, mark dynamic,
    # but still surface substitutions nested inside so they get analysed.
    return Word(_text(node), dynamic=True, substitutions=find_substitutions(node))


def _join(parts: list[Word], *, glob_allowed: bool) -> Word:
    return Word(
        "".join(part.text for part in parts),
        dynamic=any(part.dynamic for part in parts),
        glob=glob_allowed and any(part.glob for part in parts),
        substitutions=tuple(node for part in parts for node in part.substitutions),
    )


def find_substitutions(node: Node) -> tuple[Node, ...]:
    """Outermost command/process substitutions under `node`, in source order.

    Iterative: adversarial input can nest thousands of levels deep.
    """
    found: list[Node] = []
    stack = list(reversed(node.named_children))
    while stack:
        current = stack.pop()
        if current.type in SUBSTITUTIONS:
            found.append(current)
        else:
            stack.extend(reversed(current.named_children))
    return tuple(found)


@dataclass(frozen=True, slots=True)
class Target:
    path: str
    dynamic: bool
    glob: bool


def resolve(word: Word, ctx: Context) -> Target:
    """A path argument as an absolute normalized path where statically known."""
    if word.dynamic:
        return Target(word.text, dynamic=True, glob=False)
    text = word.text
    if text.startswith("/"):
        return Target(_normalize(text), dynamic=False, glob=word.glob)
    if ctx.cwd is None:
        return Target(text, dynamic=True, glob=word.glob)
    return Target(_normalize(posixpath.join(ctx.cwd, text)), dynamic=False, glob=word.glob)


def _normalize(path: str) -> str:
    normalized = posixpath.normpath(path)
    # POSIX keeps a leading "//"; for our purposes it is the same as "/".
    return "/" + normalized.lstrip("/") if normalized.startswith("//") else normalized


def is_special_file(target: Target) -> bool:
    return not target.dynamic and (
        target.path in SPECIAL_FILES or target.path.startswith("/dev/fd/")
    )
