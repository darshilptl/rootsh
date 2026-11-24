"""Handler protocol and helpers shared by the command catalog."""

from __future__ import annotations

import posixpath
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from rootsh.analysis.effects.model import Context, Effect, EffectKind
from rootsh.analysis.effects.words import Word, is_special_file, resolve


@dataclass(frozen=True, slots=True)
class Invocation:
    program: str
    args: tuple[Word, ...]
    ctx: Context


@dataclass(slots=True)
class Outcome:
    effects: list[Effect] = field(default_factory=list)
    opaque: list[str] = field(default_factory=list)
    # Inner commands to analyse as if typed directly (wrappers like sudo, xargs).
    nested: list[tuple[list[Word], Context]] = field(default_factory=list)
    # Literal shell source to re-parse one nesting level deeper (bash -c '...').
    code: list[tuple[str, Context]] = field(default_factory=list)

    def add(self, effect: Effect | None) -> None:
        if effect is not None:
            self.effects.append(effect)


Handler = Callable[[Invocation], Outcome]


def file_effect(
    kind: EffectKind,
    word: Word,
    inv: Invocation,
    *,
    recursive: bool = False,
    detail: str = "",
    dest: str | None = None,
    program: str | None = None,
) -> Effect | None:
    """An effect on a path argument; None for pipes and special files."""
    if word.pipe:
        return None
    target = resolve(word, inv.ctx)
    if is_special_file(target):
        return None
    return Effect(
        kind,
        target.path,
        program or inv.program,
        recursive=recursive or word.subtree,
        dynamic=target.dynamic,
        glob=target.glob,
        detail=detail,
        dest=dest,
    )


def effect(
    kind: EffectKind, target: str, inv: Invocation, *, detail: str = "", dynamic: bool = False
) -> Effect:
    """An effect on a non-path target (URL, host, program, process, user)."""
    return Effect(kind, target, inv.program, dynamic=dynamic, detail=detail)


def files(
    kind: EffectKind,
    words: Iterable[Word],
    inv: Invocation,
    *,
    recursive: bool = False,
    detail: str = "",
) -> list[Effect]:
    found = (file_effect(kind, w, inv, recursive=recursive, detail=detail) for w in words)
    return [item for item in found if item is not None]


HERE = Word(".")  # the working directory, for commands that default to it


def path_of(word: Word, inv: Invocation) -> str:
    return resolve(word, inv.ctx).path


def with_suffix(word: Word, suffix: str) -> Word:
    return Word(word.text + suffix, dynamic=word.dynamic, glob=word.glob)


def strip_suffix(word: Word, suffix: str) -> Word:
    text = word.text[: -len(suffix)] if word.text.endswith(suffix) else word.text
    return Word(text, dynamic=word.dynamic, glob=word.glob)


# user@host:path (scp/rsync). Not a URL, not a local path with a colon after a slash.
_REMOTE = re.compile(r"^(?:[^@/:\s]+@)?(?P<host>\[[^\]]+\]|[^@/:\s]+):")


def remote_host(word: Word) -> str | None:
    if word.dynamic or word.text.startswith(("/", "./", "../", "~")) or "://" in word.text:
        return None
    match = _REMOTE.match(word.text)
    return match.group("host") if match else None


def host_of(destination: str) -> str:
    """`user@host` -> `host`."""
    return destination.rsplit("@", 1)[-1]


def url_basename(url: str) -> str:
    name = posixpath.basename(urlsplit(url).path.rstrip("/"))
    return name or "index.html"
