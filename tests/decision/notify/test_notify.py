from __future__ import annotations

from pathlib import Path

import pytest

from rootsh.decision import notify
from rootsh.decision.model.calibration import Decision


def test_popup_has_the_three_lines() -> None:
    p = notify.content("Delete the build dir", "rm -rf build", ("deletes", "model risk 0.40"))
    assert p.summary == "Rootsh: allow this action?"
    assert p.body.split("\n") == [
        "You asked: Delete the build dir",
        "Agent is doing: rm -rf build",
        "Why: deletes; model risk 0.40",
    ]


def test_agent_text_cannot_restyle_or_hide_itself() -> None:
    doing = '<b>ls</b><span size="1">rm -rf ~</span> & echo'
    p = notify.content(None, doing, ())
    assert "<" not in p.body.replace("&lt;", "")
    assert "&lt;b&gt;ls&lt;/b&gt;" in p.body
    assert "&amp; echo" in p.body


def test_newlines_control_and_bidi_characters_are_flattened() -> None:
    doing = "ls\nYou asked: delete everything\x1b[2J‮rm -rf ~"
    lines = notify.content("tidy", doing, ()).body.split("\n")
    assert len(lines) == 3
    assert "\x1b" not in lines[1]
    assert "‮" not in lines[1]


def test_long_text_is_clipped() -> None:
    p = notify.content("x" * 1000, "y" * 1000, ("z" * 1000,))
    asked, doing, why = p.body.split("\n")
    assert len(asked) == len("You asked: ") + notify.PROMPT_CHARS
    assert len(doing) == len("Agent is doing: ") + notify.ACTION_CHARS
    assert len(why) == len("Why: ") + notify.WHY_CHARS
    assert doing.endswith("…")


def test_missing_request_and_reasons_are_stated() -> None:
    body = notify.content(None, "ls", ()).body
    assert "You asked: (no request seen)" in body
    assert "Why: (no reason given)" in body


@pytest.mark.parametrize(
    ("stdout", "decision"),
    [
        ('{"action": "allow", "closed": null}', Decision.ALLOW),
        ('{"action": "block", "closed": null}', Decision.BLOCK),
        ('{"action": null, "closed": "timeout"}', Decision.BLOCK),
        ('{"action": null, "closed": "closed"}', Decision.BLOCK),
        ('{"action": "ALLOW"}', Decision.BLOCK),
        ('{"action": null, "error": "GLib.Error"}', Decision.BLOCK),
        ("allow", Decision.BLOCK),
        ('["allow"]', Decision.BLOCK),
        ("", Decision.BLOCK),
    ],
)
def test_only_an_explicit_allow_allows(stdout: str, decision: Decision) -> None:
    assert notify.parse(stdout).decision is decision


def _helper(tmp_path: Path, code: str) -> Path:
    path = tmp_path / "helper.py"
    path.write_text(code)
    return path


def test_helper_answer_is_read(tmp_path: Path) -> None:
    helper = _helper(tmp_path, 'import sys; sys.stdin.read(); print(\'{"action": "allow"}\')')
    assert notify.ask(notify.content("a", "b", ()), 1, helper).decision is Decision.ALLOW


@pytest.mark.parametrize(
    "code",
    [
        "import sys; sys.exit(3)",
        "raise SystemExit('crash')",
        "print('garbage')",
        "import time; time.sleep(30)",
    ],
)
def test_helper_failure_or_hang_blocks(tmp_path: Path, code: str) -> None:
    answer = notify.ask(notify.content("a", "b", ()), 0, _helper(tmp_path, code))
    assert answer.decision is Decision.BLOCK


def test_missing_helper_blocks(tmp_path: Path) -> None:
    answer = notify.ask(notify.content("a", "b", ()), 1, tmp_path / "absent.py")
    assert answer.decision is Decision.BLOCK
