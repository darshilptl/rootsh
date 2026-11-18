"""trace(): where each critical argument of an action came from (design doc §2-§4)."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from urllib.parse import urlsplit

from rootsh.effects import Analysis, Context, EffectKind, analyze_event
from rootsh.events import AgentEvent, EventKind
from rootsh.provenance.mentions import Mentions, extract
from rootsh.provenance.model import (
    ArgumentProvenance,
    Match,
    Provenance,
    Relation,
    Source,
)
from rootsh.provenance.sources import event_context, source_of, written_paths

MIN_NAME = 3  # shorter final names (`db`, `sh`) match too much to mean anything
_PATH_KINDS = frozenset({EffectKind.WRITE, EffectKind.DELETE, EffectKind.PERMISSION})


@dataclass(frozen=True, slots=True)
class Argument:
    target: str
    kind: EffectKind
    dynamic: bool


def critical_arguments(analysis: Analysis) -> list[Argument]:
    """Effect targets the agent chose (design doc §2), in order, de-duplicated."""
    found: dict[tuple[str, EffectKind], Argument] = {}

    def add(target: str, kind: EffectKind, dynamic: bool) -> None:
        found.setdefault((target, kind), Argument(target, kind, dynamic))

    for effect in analysis.effects:
        if effect.kind in _PATH_KINDS or effect.kind is EffectKind.EXEC:
            add(effect.target, effect.kind, effect.dynamic)
        elif effect.kind is EffectKind.MOVE:
            add(effect.target, EffectKind.MOVE, effect.dynamic)
            if effect.dest is not None:
                add(effect.dest, EffectKind.WRITE, effect.dynamic)
        elif effect.kind is EffectKind.NETWORK:
            implicit = effect.target.startswith("registry:") or (
                effect.target == "upstream" and effect.dynamic
            )
            if not implicit:
                add(effect.target, EffectKind.NETWORK, effect.dynamic)
    return list(found.values())


# --- relations -----------------------------------------------------------------


def _ancestor(parent: str, child: str) -> bool:
    return child.startswith(parent.rstrip("/") + "/")


def _path_relation(target: str, paths: frozenset[str], home: str) -> tuple[Relation, str] | None:
    if target in paths:
        return Relation.EXACT, target
    inside = [m for m in paths if m not in ("/", home) and _ancestor(m, target)]
    if inside:
        return Relation.INSIDE, max(inside, key=len)
    broader = [m for m in paths if _ancestor(target, m)]
    if broader:
        return Relation.BROADER, min(broader, key=len)
    return None


def _name_relation(target: str, words: frozenset[str]) -> tuple[Relation, str] | None:
    name = target.rstrip("/").rsplit("/", 1)[-1].lower()
    if len(name) >= MIN_NAME and not any(c in name for c in "$<`") and name in words:
        return Relation.NAME, name
    return None


def _network_relation(target: str, mentions: Mentions) -> tuple[Relation, str] | None:
    if "://" in target:
        if target.rstrip("/") in mentions.urls:
            return Relation.EXACT, target
        host = (urlsplit(target).hostname or "").lower()
        return (Relation.HOST, host) if host in mentions.hosts else None
    host, _, port = target.partition(":")
    if port:
        if target in mentions.text:
            return Relation.EXACT, target
        return (Relation.HOST, host) if host.lower() in mentions.hosts else None
    if "." in target:
        return (Relation.EXACT, target) if target.lower() in mentions.hosts else None
    return _name_relation(target, mentions.words)  # a git remote name


def relate(argument: Argument, mentions: Mentions, home: str) -> tuple[Relation, str] | None:
    if argument.kind is EffectKind.NETWORK:
        return _network_relation(argument.target, mentions)
    if argument.dynamic or not argument.target.startswith("/"):
        return _name_relation(argument.target, mentions.words)
    return _path_relation(argument.target, mentions.paths, home) or _name_relation(
        argument.target, mentions.words
    )


def _own_relation(target: str, written_path: str) -> Relation | None:
    if target == written_path:
        return Relation.EXACT
    if _ancestor(written_path, target):
        return Relation.INSIDE
    if _ancestor(target, written_path):
        return Relation.BROADER
    return None


# --- trace -----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Searched:
    index: int
    source: Source
    mentions: Mentions


@dataclass(frozen=True, slots=True)
class _Written:
    index: int  # the tool_pre that wrote it
    path: str


class Session:
    """One agent session, indexed incrementally: each event is analysed once
    when it arrives, so tracing an action only matches against the index."""

    def __init__(self, ctx: Context) -> None:
        self._ctx = ctx
        self._count = 0
        self._turn_start: int | None = None
        self._searched: list[_Searched] = []
        self._written: list[_Written] = []
        # tool_call_id -> (pre index, its analysis): reused by the matching post,
        # which carries the same input, and used to confirm the call's writes.
        self._calls: dict[str, tuple[int, Analysis]] = {}

    def add(self, event: AgentEvent) -> None:
        index = self._count
        self._count += 1
        if event.kind is EventKind.PROMPT:
            self._turn_start = index
            self._search(index, Source.USER, event)
            return
        call = event.tool_call_id or ""
        if event.kind is EventKind.TOOL_PRE:
            self._calls[call] = (index, analyze_event(event, event_context(event, self._ctx)))
            return
        known = self._calls.pop(call, None)
        analysis = known[1] if known else analyze_event(event, event_context(event, self._ctx))
        if event.kind is EventKind.TOOL_POST and known is not None:
            self._written += [_Written(known[0], path) for path in written_paths(analysis)]
        source = source_of(event, analysis)
        if source is not None:
            self._search(index, source, event)

    def _search(self, index: int, source: Source, event: AgentEvent) -> None:
        if event.text:
            cwd = event_context(event, self._ctx).cwd
            mentions = extract(event.text, cwd=cwd, home=self._ctx.home)
            self._searched.append(_Searched(index, source, mentions))

    def _same_turn(self, index: int) -> bool:
        return self._turn_start is not None and index >= self._turn_start

    def _candidates(self, argument: Argument) -> Iterator[Match]:
        for item in self._searched:
            found = relate(argument, item.mentions, self._ctx.home)
            if found is not None:
                relation, mention = found
                yield Match(item.source, relation, item.index, self._same_turn(item.index), mention)
        if argument.kind is EffectKind.NETWORK or argument.dynamic:
            return
        for write in self._written:
            own = _own_relation(argument.target, write.path)
            if own is not None:
                yield Match(Source.OWN, own, write.index, self._same_turn(write.index), write.path)

    def trace(self, action: AgentEvent) -> Provenance:
        """Where each critical argument of `action` came from. Pure: no I/O."""
        if action.kind is not EventKind.TOOL_PRE:
            raise ValueError("trace() takes a tool_pre event")
        analysis = analyze_event(action, event_context(action, self._ctx))
        arguments = []
        for argument in critical_arguments(analysis):
            best: dict[Source, Match] = {}
            for match in self._candidates(argument):
                current = best.get(match.source)
                if current is None or match.beats(current):
                    best[match.source] = match
            arguments.append(
                ArgumentProvenance(argument.target, argument.kind, argument.dynamic, best)
            )
        return Provenance(tuple(arguments))


def trace(action: AgentEvent, history: Sequence[AgentEvent], ctx: Context) -> Provenance:
    """Convenience: index `history` from scratch, then trace `action`."""
    session = Session(ctx)
    for event in history:
        session.add(event)
    return session.trace(action)
