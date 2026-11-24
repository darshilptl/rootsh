"""Which sensitivity classes a path falls into (design doc §5)."""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Rule:
    cls: str
    prefix: str | None = None  # a path and everything under it; "~" = home
    name: str | None = None  # an fnmatch pattern on the final component
    segment: tuple[str, ...] | None = None  # consecutive components anywhere

    def expanded_prefix(self, home: str) -> str | None:
        if self.prefix is None:
            return None
        return home + self.prefix[1:] if self.prefix.startswith("~") else self.prefix


def _prefixes(cls: str, *paths: str) -> list[Rule]:
    return [Rule(cls, prefix=p) for p in paths]


def _names(cls: str, *patterns: str) -> list[Rule]:
    return [Rule(cls, name=p) for p in patterns]


def _segments(cls: str, *segments: tuple[str, ...]) -> list[Rule]:
    return [Rule(cls, segment=s) for s in segments]


RULES: tuple[Rule, ...] = (
    *_prefixes("secret", "~/.ssh", "~/.aws", "~/.gnupg", "~/.kube", "~/.netrc",
               "~/.git-credentials", "~/.config/gh", "~/.docker/config.json"),
    *_names("secret", ".env", ".env.*", "*.pem", "*.key", "id_rsa*", "id_ed25519*", "id_ecdsa*"),
    *_segments("secret", ("secrets",), ("credentials",)),
    *_segments("vcs_internal", (".git",)),
    *_segments("data_store", ("migrations",)),
    *_names("data_store", "*.db", "*.sqlite", "*.sqlite3", "*.sql"),
    *_segments("ci", (".github", "workflows")),
    *_names("ci", ".gitlab-ci.yml"),
    *_prefixes("persistence", "~/.bashrc", "~/.zshrc", "~/.profile", "~/.bash_profile",
               "~/.config/autostart", "~/.config/systemd/user", "/var/spool/cron"),
    *_segments("persistence", (".git", "hooks")),
    *_prefixes("agent_config", "~/.claude", "~/.config/opencode"),
    *_segments("agent_config", (".claude",), (".opencode",)),
    *_names("agent_config", "CLAUDE.md", "AGENTS.md"),
    *_prefixes("rootsh", "/etc/rootsh", "/var/lib/rootsh", "/opt/rootsh", "/etc/claude-code"),
    *_prefixes("system", "/etc", "/usr", "/boot", "/bin", "/sbin", "/lib", "/lib32", "/lib64",
               "/libx32", "/dev", "/proc", "/sys"),
)  # fmt: skip

CLASSES = frozenset(rule.cls for rule in RULES)


def _under(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip("/") + "/")


def _has_segment(parts: list[str], segment: tuple[str, ...]) -> bool:
    width = len(segment)
    return any(tuple(parts[i : i + width]) == segment for i in range(len(parts) - width + 1))


def classify(path: str, home: str) -> frozenset[str]:
    """Classes of `path` itself (an absolute, normalized path)."""
    parts = [p for p in path.split("/") if p]
    name = parts[-1] if parts else ""
    found: set[str] = set()
    for rule in RULES:
        prefix = rule.expanded_prefix(home)
        if (
            (prefix is not None and _under(path, prefix))
            or (rule.name is not None and fnmatch.fnmatchcase(name, rule.name))
            or (rule.segment is not None and _has_segment(parts, rule.segment))
        ):
            found.add(rule.cls)
    return frozenset(found)


def contained_prefix_classes(directory: str, home: str) -> frozenset[str]:
    """Classes of known sensitive locations *inside* `directory` (no walking):
    `rm -rf ~` touches ~/.ssh even if the walk is capped before reaching it."""
    return frozenset(
        rule.cls
        for rule in RULES
        if (prefix := rule.expanded_prefix(home)) is not None
        and prefix != directory
        and _under(prefix, directory)
    )
