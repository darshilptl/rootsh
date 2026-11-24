"""Gold-set scenarios: loading and validating the catalog (design 04 §3)."""

from __future__ import annotations

import hashlib
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any

from rootsh.analysis.events import canonical_json


class Intent(StrEnum):
    BENIGN = "benign"
    INJECTION = "injection"
    HALLUCINATION = "hallucination"
    DEVIATION = "deviation"


class Vector(StrEnum):
    NONE = "none"
    README = "readme"
    AGENTS_MD = "agents_md"
    SOURCE_COMMENT = "source_comment"
    SCRIPT_OUTPUT = "script_output"
    LOG_FILE = "log_file"
    CONFIG_FILE = "config_file"
    ISSUE_FILE = "issue_file"
    DOCS = "docs"


class Aim(StrEnum):
    NONE = "none"
    DESTROY = "destroy"
    TAMPER = "tamper"
    EXFILTRATE = "exfiltrate"
    PERSIST = "persist"
    ESCALATE = "escalate"
    DEFENSE_TAMPER = "defense_tamper"


class CatalogError(ValueError):
    """The scenario catalog is malformed; nothing may be collected from it."""


type Files = Mapping[str, str]


@dataclass(frozen=True, slots=True)
class Scenario:
    id: str
    repository: str
    git: bool
    intent: Intent
    vector: Vector
    aim: Aim
    prompt: str
    claude: bool
    repeat: bool
    files: Files  # repository files with the scenario overlay applied
    dirty: Files  # written after the commit

    @property
    def sha256(self) -> str:
        """Identity of everything that defines the run, for `verify`."""
        body = {
            "id": self.id,
            "repository": self.repository,
            "git": self.git,
            "intent": self.intent,
            "vector": self.vector,
            "aim": self.aim,
            "prompt": self.prompt,
            "claude": self.claude,
            "repeat": self.repeat,
            "files": dict(self.files),
            "dirty": dict(self.dirty),
        }
        return hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()


_SCENARIO_KEYS = frozenset(
    {"id", "intent", "vector", "aim", "prompt", "claude", "repeat", "files", "dirty"}
)


def check_relative(path: str) -> str:
    """A workspace path must stay inside the workspace."""
    pure = PurePosixPath(path)
    if not path or pure.is_absolute() or ".." in pure.parts or pure.parts[0] == ".git":
        raise CatalogError(f"path {path!r} must be relative, inside the workspace, outside .git")
    return path


def _files(raw: Any, where: str) -> dict[str, str]:
    if raw is None:
        return {}
    if not isinstance(raw, dict) or not all(isinstance(v, str) for v in raw.values()):
        raise CatalogError(f"{where}: files must map paths to strings")
    return {check_relative(k): v for k, v in raw.items()}


def _enum[E: StrEnum](kind: type[E], raw: Any, where: str) -> E:
    try:
        return kind(raw)
    except ValueError:
        raise CatalogError(f"{where}: {raw!r} is not a valid {kind.__name__}") from None


def _flag(raw: dict[str, Any], key: str, where: str) -> bool:
    value = raw.get(key, False)
    if not isinstance(value, bool):
        raise CatalogError(f"{where}: {key} must be true or false")
    return value


def parse_repository(doc: Mapping[str, Any], source: str) -> tuple[Scenario, ...]:
    repo = doc.get("repository")
    if not isinstance(repo, dict) or not isinstance(repo.get("name"), str):
        raise CatalogError(f"{source}: [repository] with a name is required")
    name: str = repo["name"]
    git = _flag(repo, "git", source)
    base = _files(repo.get("files"), source)
    scenarios = doc.get("scenario")
    if not isinstance(scenarios, list) or not scenarios:
        raise CatalogError(f"{source}: at least one [[scenario]] is required")

    out = []
    for raw in scenarios:
        sid = raw.get("id") if isinstance(raw, dict) else None
        if not isinstance(sid, str) or not sid.startswith(f"{name}-"):
            raise CatalogError(f"{source}: scenario id {sid!r} must start with {name!r}-")
        where = f"{source}:{sid}"
        unknown = set(raw) - _SCENARIO_KEYS
        if unknown:
            raise CatalogError(f"{where}: unknown keys {sorted(unknown)}")
        prompt = raw.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            raise CatalogError(f"{where}: prompt is required")
        intent = _enum(Intent, raw.get("intent"), where)
        vector = _enum(Vector, raw.get("vector"), where)
        aim = _enum(Aim, raw.get("aim"), where)
        if (intent is Intent.INJECTION) == (vector is Vector.NONE):
            raise CatalogError(f"{where}: an injection needs a vector; nothing else has one")
        if (vector is Vector.NONE) != (aim is Aim.NONE):
            raise CatalogError(f"{where}: vector and aim are set together")
        out.append(
            Scenario(
                id=sid,
                repository=name,
                git=git,
                intent=intent,
                vector=vector,
                aim=aim,
                prompt=prompt,
                claude=_flag(raw, "claude", where),
                repeat=_flag(raw, "repeat", where),
                files=MappingProxyType(base | _files(raw.get("files"), where)),
                dirty=MappingProxyType(_files(raw.get("dirty"), where)),
            )
        )
    return tuple(out)


def load_catalog(directory: Path) -> tuple[Scenario, ...]:
    """Every scenario in `directory/*.toml`, in file then declaration order."""
    paths = sorted(directory.glob("*.toml"))
    if not paths:
        raise CatalogError(f"no *.toml scenario files in {directory}")
    scenarios: list[Scenario] = []
    for path in paths:
        try:
            doc = tomllib.loads(path.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as exc:
            raise CatalogError(f"{path.name}: {exc}") from exc
        scenarios.extend(parse_repository(doc, path.name))
    seen: set[str] = set()
    for scenario in scenarios:
        if scenario.id in seen:
            raise CatalogError(f"duplicate scenario id {scenario.id!r}")
        seen.add(scenario.id)
    return tuple(scenarios)
