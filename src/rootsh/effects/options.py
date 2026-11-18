"""GNU-style option parsing over evaluated words."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from rootsh.effects.words import Word


@dataclass(frozen=True, slots=True)
class Spec:
    """Which options take a value. Unknown options are treated as flags."""

    short_values: frozenset[str] = frozenset()
    long_values: frozenset[str] = frozenset()
    stop_at_positional: bool = False  # POSIX style: options end at the first operand

    @staticmethod
    def of(short: str = "", long: Sequence[str] = (), *, stop: bool = False) -> Spec:
        return Spec(frozenset(short), frozenset(long), stop)


@dataclass(slots=True)
class Parsed:
    flags: set[str] = field(default_factory=set)
    values: dict[str, list[Word]] = field(default_factory=dict)
    positionals: list[Word] = field(default_factory=list)

    def has(self, *names: str) -> bool:
        return any(name in self.flags or name in self.values for name in names)

    def value(self, *names: str) -> Word | None:
        for name in names:
            if self.values.get(name):
                return self.values[name][-1]
        return None

    def all_values(self, *names: str) -> list[Word]:
        return [word for name in names for word in self.values.get(name, [])]


def parse(args: Sequence[Word], spec: Spec) -> Parsed:
    parsed = Parsed()
    words = list(args)
    i = 0
    while i < len(words):
        word = words[i]
        text = word.text
        i += 1
        if word.dynamic or text in ("-", "") or not text.startswith("-"):
            parsed.positionals.append(word)
            if spec.stop_at_positional:
                parsed.positionals.extend(words[i:])
                break
            continue
        if text == "--":
            parsed.positionals.extend(words[i:])
            break
        if text.startswith("--"):
            name, eq, inline = text[2:].partition("=")
            if eq:
                parsed.values.setdefault(name, []).append(Word(inline))
            elif name in spec.long_values and i < len(words):
                parsed.values.setdefault(name, []).append(words[i])
                i += 1
            else:
                parsed.flags.add(name)
            continue
        for offset, char in enumerate(text[1:], start=1):
            if char in spec.short_values:
                rest = text[offset + 1 :]
                if rest:
                    parsed.values.setdefault(char, []).append(Word(rest))
                elif i < len(words):
                    parsed.values.setdefault(char, []).append(words[i])
                    i += 1
                break
            parsed.flags.add(char)
    return parsed
