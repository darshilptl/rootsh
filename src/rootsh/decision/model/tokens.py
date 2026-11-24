"""Word tokens of an action, shared by the action baseline scorer and the
shortcut audits (P5, P6)."""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from rootsh.research.labels.steps import Step

_TOKEN = re.compile(r"[a-z0-9_.-]+")


def tokens(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


def tool_name(step: Step) -> str:
    return step.action.tool.value if step.action.tool else step.action.tool_raw or "<none>"


def action_tokens(step: Step) -> list[str]:
    args = json.dumps(dict(step.action.args), sort_keys=True, default=str)
    return [tool_name(step), *tokens(args)]
