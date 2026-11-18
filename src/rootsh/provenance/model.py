"""Where each critical argument of an action came from (design doc §2)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from rootsh.effects import EffectKind


class Source(StrEnum):
    USER = "user"  # a prompt the user typed
    WORKSPACE = "workspace"  # output of a listing tool: what is on disk
    CONTENT = "content"  # any other tool output: files, web pages, command output
    OWN = "own"  # a target the agent itself wrote earlier in the session


class Relation(StrEnum):
    EXACT = "exact"
    INSIDE = "inside"  # the target is under a mentioned path
    BROADER = "broader"  # a mentioned path is under the target
    HOST = "host"  # network: only the host was mentioned
    NAME = "name"  # only the target's final name appears as a word


# Strongest first; ties go to the latest event.
STRENGTH: Mapping[Relation, int] = {
    Relation.EXACT: 5,
    Relation.INSIDE: 4,
    Relation.BROADER: 3,
    Relation.HOST: 2,
    Relation.NAME: 1,
}


@dataclass(frozen=True, slots=True)
class Match:
    source: Source
    relation: Relation
    event_seq: int  # index into the history
    same_turn: bool
    mention: str

    def beats(self, other: Match) -> bool:
        mine, theirs = STRENGTH[self.relation], STRENGTH[other.relation]
        return mine > theirs or (mine == theirs and self.event_seq > other.event_seq)


@dataclass(frozen=True, slots=True)
class ArgumentProvenance:
    target: str
    kind: EffectKind
    dynamic: bool
    best: Mapping[Source, Match]

    @property
    def fabricated(self) -> bool:
        return not self.best

    @property
    def content_only(self) -> bool:
        return set(self.best) == {Source.CONTENT}


@dataclass(frozen=True, slots=True)
class Provenance:
    arguments: tuple[ArgumentProvenance, ...]
