"""Desktop ask channel (docs/phases/07-daemon.md §3).

`content()` builds what the pop-up shows; `ask()` runs the D-Bus helper with
the system Python (the project venv has no `gi`) and reads one JSON line.
Anything but a clear "allow" from the user is a block.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from rootsh.decision.model.calibration import Decision

SUMMARY = "Rootsh: allow this action?"
SYSTEM_PYTHON = "/usr/bin/python3"
HELPER = Path(__file__).with_name("dbus_ask.py")
ASK_TIMEOUT_S = 60
PROMPT_CHARS = 200
ACTION_CHARS = 300
WHY_CHARS = 300
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f​-‏‪-‮⁦-⁩]")


@dataclass(frozen=True, slots=True)
class Popup:
    summary: str
    body: str


def _clean(text: str, limit: int) -> str:
    """One line, no control or bidi characters, clipped, markup-escaped.
    GNOME renders body markup, so agent-chosen text could restyle or hide
    itself without escaping; bidi overrides could reorder what is shown."""
    flat = _CONTROL.sub(" ", " ".join(text.split()))
    clipped = flat if len(flat) <= limit else flat[: limit - 1] + "…"
    return clipped.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def content(asked: str | None, doing: str, why: tuple[str, ...]) -> Popup:
    lines = (
        f"You asked: {_clean(asked, PROMPT_CHARS) if asked else '(no request seen)'}",
        f"Agent is doing: {_clean(doing, ACTION_CHARS)}",
        f"Why: {_clean('; '.join(why), WHY_CHARS) if why else '(no reason given)'}",
    )
    return Popup(SUMMARY, "\n".join(lines))


@dataclass(frozen=True, slots=True)
class Answer:
    decision: Decision
    detail: str  # what the helper reported, for the decision log


def parse(stdout: str) -> Answer:
    """The helper's single JSON line -> allow only on an explicit "allow"."""
    try:
        reply = json.loads(stdout)
    except ValueError:
        return Answer(Decision.BLOCK, "helper output unreadable")
    if not isinstance(reply, dict):
        return Answer(Decision.BLOCK, "helper output unreadable")
    if reply.get("action") == "allow":
        return Answer(Decision.ALLOW, "user clicked Allow")
    if reply.get("action") == "block":
        return Answer(Decision.BLOCK, "user clicked Block")
    return Answer(Decision.BLOCK, f"no answer ({reply.get('closed') or reply.get('error')})")


def ask(popup: Popup, timeout_s: int = ASK_TIMEOUT_S, helper: Path = HELPER) -> Answer:
    request = json.dumps({"summary": popup.summary, "body": popup.body, "timeout": timeout_s})
    try:
        done = subprocess.run(  # noqa: S603 - fixed interpreter and helper, no shell
            [SYSTEM_PYTHON, "-I", str(helper)],
            input=request,
            capture_output=True,
            text=True,
            timeout=timeout_s + 5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return Answer(Decision.BLOCK, f"helper failed: {type(exc).__name__}")
    if done.returncode != 0:
        return Answer(Decision.BLOCK, f"helper exited {done.returncode}")
    return parse(done.stdout)
