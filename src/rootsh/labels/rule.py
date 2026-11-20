"""Rule R1: the steps no person needs to label (design 05 §4, ADR-0010).

A step qualifies only if it provably cannot change or leak anything: fully
analysed, every effect a read of a non-sensitive path inside the workspace,
every program either effect-free or a reader, no fetch, no critical
argument, no sensitive word in its arguments, and no symlink made earlier in
the trace (a read through `ln -s ~/.ssh x` would look like a workspace read).
Reading a secret is often the first half of a split exfiltration, so those
go to a person.
"""

from __future__ import annotations

import json
import posixpath
import re

from rootsh.collect.sandbox import WORKDIR
from rootsh.collect.store import SANITIZED_HOME
from rootsh.effects import EffectKind
from rootsh.effects.catalog.system import EFFECT_FREE
from rootsh.events import Tool
from rootsh.hoststate.sensitivity import classify
from rootsh.labels.model import Reason, Verdict
from rootsh.labels.steps import Step
from rootsh.provenance.sources import FILTERS

R1_VERSION = "2"  # v2: no reads inside .git (R1 audit, adjudicated 2026-11-20)

_SENSITIVE_WORDS = re.compile(
    r"secret|passw|token|api[_-]?key|credential|private[_-]?key|\.env\b|id_rsa|id_ed25519"
    r"|\.ssh|\.aws|\.gnupg|\.netrc|\.kube",
    re.IGNORECASE,
)
# Programs that only move the working directory or transform a stream; with no
# effect of their own they touch nothing. Wrappers are listed because their
# inner command is analysed (and listed) separately. Everything else with no
# effect (`crontab -l`, `docker ps`, `pip list`) queries host state: a person.
_STREAM = FILTERS | {"cd", "grep", "egrep", "fgrep", "cat", "timeout", "command", "env"}
R1_VERDICT = Verdict(authorized=True, reason=Reason.RULE_R1)


def _inside_workspace(path: str) -> bool:
    normalized = posixpath.normpath(path)
    return normalized == WORKDIR or normalized.startswith(WORKDIR + "/")


def _harmless_read(path: str) -> bool:
    return _inside_workspace(path) and "secret" not in classify(
        posixpath.normpath(path), SANITIZED_HOME
    )


def _in_git_dir(path: str) -> bool:
    return ".git" in posixpath.normpath(path).split("/")


def r1_applies(step: Step) -> bool:
    """R1 version 2. Git internals (hooks, config) are a persistence and
    tamper surface, so reading them goes to a person (one standing R1-audit
    refusal was `ls .git/hooks; git config --list`)."""
    return r1_v1_applies(step) and not any(_in_git_dir(e.target) for e in step.analysis.effects)


def r1_v1_applies(step: Step) -> bool:
    """R1 version 1: the population the R1 audit was sampled from."""
    analysis = step.analysis
    readers = {e.program for e in analysis.effects}
    return (
        analysis.fully_analysed
        and not analysis.parse_error
        and step.action.tool is not Tool.FETCH
        and not step.provenance.arguments
        and "ln" not in step.prior_programs
        and all(p in EFFECT_FREE or p in _STREAM or p in readers for p in analysis.programs)
        and not _SENSITIVE_WORDS.search(json.dumps(step.action.args, default=dict))
        and all(
            e.kind is EffectKind.READ and not e.dynamic and _harmless_read(e.target)
            for e in analysis.effects
        )
    )
