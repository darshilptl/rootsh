"""What an action would do to the host: effects, analysis result, context."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType


class EffectKind(StrEnum):
    READ = "read"
    WRITE = "write"
    DELETE = "delete"
    MOVE = "move"
    PERMISSION = "permission"
    EXEC = "exec"
    NETWORK = "network"
    PRIVILEGE = "privilege"
    PROCESS = "process"


# Closed vocabularies (design doc §2.1 and §2.2); Effect and Analysis reject
# anything else so a typo in a handler fails a test instead of shipping.
DETAILS: Mapping[EffectKind, frozenset[str]] = MappingProxyType(
    {
        EffectKind.READ: frozenset({""}),
        EffectKind.WRITE: frozenset(
            {"create", "truncate", "append", "modify", "discard-changes", "config"}
        ),
        EffectKind.DELETE: frozenset({"", "untracked", "ref"}),
        EffectKind.MOVE: frozenset({""}),
        EffectKind.PERMISSION: frozenset({""}),
        EffectKind.EXEC: frozenset(
            {"", "script", "code", "package-install", "build", "stdin-code", "remote"}
        ),
        EffectKind.NETWORK: frozenset(
            {"fetch", "upload", "push", "force-push", "delete-ref", "connect", "listen"}
        ),
        EffectKind.PRIVILEGE: frozenset({"sudo", "doas", "su", "pkexec"}),
        EffectKind.PROCESS: frozenset({"signal", "stop", "start", "enable", "disable", "power"}),
    }
)

OPAQUE_REASONS = frozenset(
    {
        "parse_error",
        "eval",
        "source",
        "stdin_code",
        "interpreter_code",
        "script_exec",
        "package_install",
        "dynamic_command",
        "unknown_program",
        "remote_exec",
        "nested_depth",
        "effect_limit",
        "unsupported_syntax",
        "tool",
    }
)


def check_opaque(reason: str) -> str:
    if reason.split(":", 1)[0] not in OPAQUE_REASONS:
        raise ValueError(f"unknown opaque reason {reason!r}")
    return reason


@dataclass(frozen=True, slots=True)
class Effect:
    kind: EffectKind
    target: str
    program: str
    recursive: bool = False
    dynamic: bool = False
    glob: bool = False
    detail: str = ""
    dest: str | None = None

    def __post_init__(self) -> None:
        if self.detail not in DETAILS[self.kind]:
            raise ValueError(f"detail {self.detail!r} is not valid for {self.kind}")
        if (self.dest is not None) != (self.kind is EffectKind.MOVE):
            raise ValueError("dest is required for move effects and only for them")


@dataclass(frozen=True, slots=True)
class Analysis:
    effects: tuple[Effect, ...]
    programs: tuple[str, ...]
    opaque: tuple[str, ...]
    parse_error: bool

    def __post_init__(self) -> None:
        for reason in self.opaque:
            check_opaque(reason)

    @property
    def fully_analysed(self) -> bool:
        return not self.opaque


@dataclass(frozen=True, slots=True)
class Context:
    """What is known about the shell an action runs in."""

    cwd: str | None
    home: str
    env: Mapping[str, str] = field(default_factory=dict)

    def variable(self, name: str) -> str | None:
        if name == "HOME":
            return self.home
        if name == "PWD":
            return self.cwd
        return self.env.get(name)

    def with_cwd(self, cwd: str | None) -> Context:
        return Context(cwd, self.home, self.env)
