from __future__ import annotations

import random
from datetime import UTC, datetime

import pytest

from rootsh.effects import Analysis, Context, Effect, EffectKind, analyze_event, analyze_shell
from rootsh.effects.catalog import CATALOG, lookup
from rootsh.effects.model import check_opaque
from rootsh.effects.options import Spec, parse
from rootsh.effects.shell import MAX_EFFECTS, MAX_NESTING
from rootsh.effects.words import Word
from rootsh.events import Agent, AgentEvent, EventKind, Tool
from support.effects_corpus import load, run

CTX = Context("/w", "/home/u", {"USER": "u"})


def analyze(command: str, ctx: Context = CTX) -> Analysis:
    return analyze_shell(command, ctx)


# --- corpus gate ---------------------------------------------------------------


def test_hand_labelled_corpus_matches_exactly() -> None:
    # The P2 gate (>= 95%) was met on the first run; after the recorded fixes the
    # corpus matches fully, so any miss now is a regression.
    results = run()
    misses = [(r.entry["id"], r.entry.get("cmd"), r.diff) for r in results if not r.matched]
    assert len(results) >= 150
    assert misses == []


def test_corpus_context_is_the_documented_one() -> None:
    ctx, entries = load()
    assert (ctx.cwd, ctx.home, dict(ctx.env)) == ("/w", "/home/u", {"USER": "u"})
    assert len({e["id"] for e in entries}) == len(entries)


# --- words and paths ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("command", "target"),
    [
        ("rm 'a b'", "/w/a b"),
        ("rm a\\ b", "/w/a b"),
        ("rm $'a\\tb'", "/w/a\tb"),
        ('rm "$HOME/x"', "/home/u/x"),
        ("rm ${HOME}/x", "/home/u/x"),
        ("rm ~/x", "/home/u/x"),
        ("rm ./a/../b", "/w/b"),
        ("rm //etc", "/etc"),
    ],
)
def test_word_evaluation(command: str, target: str) -> None:
    (effect,) = analyze(command).effects
    assert effect.target == target
    assert not effect.dynamic


def test_unknown_variables_and_tilde_users_are_dynamic() -> None:
    for command in ("rm ${X:-/tmp}", "rm ~bob/x", "rm $1"):
        (effect,) = analyze(command).effects
        assert effect.dynamic, command


def test_relative_paths_are_dynamic_without_a_cwd() -> None:
    (effect,) = analyze("rm x", Context(None, "/home/u")).effects
    assert (effect.target, effect.dynamic) == ("x", True)


def test_special_files_have_no_effect() -> None:
    assert analyze("echo x > /dev/null; cat /dev/stdin < /dev/tty").effects == ()


# --- options -------------------------------------------------------------------------


def test_option_parsing() -> None:
    words = [Word(t) for t in ("-abv", "1", "--long=x", "--flag", "pos", "--", "-not-an-option")]
    p = parse(words, Spec.of("v", ["other"]))
    assert {"a", "b", "flag"} <= p.flags
    assert p.value("v") == Word("1")
    assert p.value("long") == Word("x")
    assert [w.text for w in p.positionals] == ["pos", "-not-an-option"]


def test_option_parsing_stops_at_first_operand_when_asked() -> None:
    p = parse(
        [Word("-u"), Word("bob"), Word("rm"), Word("-rf"), Word("x")], Spec.of("u", stop=True)
    )
    assert [w.text for w in p.positionals] == ["rm", "-rf", "x"]


# --- vocabularies -----------------------------------------------------------------


def test_effect_rejects_unknown_details_and_misplaced_dest() -> None:
    with pytest.raises(ValueError, match="detail"):
        Effect(EffectKind.WRITE, "/x", "p", detail="explode")
    with pytest.raises(ValueError, match="dest"):
        Effect(EffectKind.DELETE, "/x", "p", dest="/y")
    with pytest.raises(ValueError, match="dest"):
        Effect(EffectKind.MOVE, "/x", "p")


def test_opaque_reasons_are_a_closed_set() -> None:
    assert check_opaque("unknown_program:foo") == "unknown_program:foo"
    with pytest.raises(ValueError, match="unknown opaque"):
        check_opaque("looks_scary")


def test_versioned_interpreters_and_mkfs_resolve_by_pattern() -> None:
    for name in ("python3.12", "python3", "pip3.11", "mkfs.xfs", "mkfs"):
        assert lookup(name) is not None, name
    assert lookup("pythonista") is None
    assert "git" in CATALOG


# --- limits and robustness ------------------------------------------------------------


def test_nested_shells_stop_at_the_depth_limit() -> None:
    command = "rm a"
    for _ in range(MAX_NESTING + 2):
        command = "bash -c " + "'" + command.replace("'", "'\\''") + "'"
    analysis = analyze(command)
    assert "nested_depth" in analysis.opaque


def test_effect_limit() -> None:
    analysis = analyze("touch " + " ".join(f"f{i}" for i in range(MAX_EFFECTS + 5)))
    assert len(analysis.effects) == MAX_EFFECTS
    assert "effect_limit" in analysis.opaque


def test_deeply_nested_input_does_not_crash() -> None:
    for command in (
        "(" * 5000 + "ls" + ")" * 5000,
        "echo " + "$(" * 3000 + "x" + ")" * 3000,
        "$((" * 2000,
    ):
        analysis = analyze(command)
        assert isinstance(analysis, Analysis)


def test_never_raises_on_truncated_corpus_or_random_bytes() -> None:
    rng = random.Random(42)
    _, entries = load()
    commands = [e["cmd"] for e in entries if "cmd" in e]
    samples = [cmd[:cut] for cmd in commands for cut in range(len(cmd) + 1)]
    alphabet = "abc -|&;<>()$`'\"\\{}[]*?~=!#\n\t/."
    samples += [
        "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 80))) for _ in range(3000)
    ]
    samples += [
        bytes(rng.getrandbits(8) for _ in range(40)).decode("utf-8", "replace") for _ in range(500)
    ]
    for sample in samples:
        analysis = analyze(sample)
        if analysis.parse_error:
            assert "parse_error" in analysis.opaque, sample


# --- events ----------------------------------------------------------------------------


def _event(tool: Tool, raw: str, args: dict[str, object]) -> AgentEvent:
    return AgentEvent(
        agent=Agent.CLAUDE_CODE,
        kind=EventKind.TOOL_PRE,
        session_id="s",
        turn_id="t",
        tool_call_id="c",
        tool=tool,
        tool_raw=raw,
        args=args,  # type: ignore[arg-type]
        text=None,
        truncated=False,
        cwd="/w",
        received_at=datetime(2026, 11, 18, tzinfo=UTC),
        raw_sha256="0" * 64,
    )


def test_analyze_event_routes_by_tool() -> None:
    shell = analyze_event(_event(Tool.SHELL, "Bash", {"command": "rm -rf x"}), CTX)
    assert shell.effects[0].kind is EffectKind.DELETE
    write = analyze_event(_event(Tool.WRITE, "Write", {"file_path": "rel.txt"}), CTX)
    assert write.effects[0].target == "/w/rel.txt"
    bad = analyze_event(_event(Tool.SHELL, "Bash", {"command": 3}), CTX)
    assert bad.opaque == ("tool:Bash",)


def test_prompt_events_have_no_effects() -> None:
    prompt = AgentEvent(
        agent=Agent.OPENCODE,
        kind=EventKind.PROMPT,
        session_id="s",
        turn_id="t",
        tool_call_id=None,
        tool=None,
        tool_raw=None,
        args={},
        text="hi",
        truncated=False,
        cwd=None,
        received_at=datetime(2026, 11, 18, tzinfo=UTC),
        raw_sha256="0" * 64,
    )
    with pytest.raises(ValueError, match="tool events"):
        analyze_event(prompt, CTX)


@pytest.mark.parametrize(
    ("command", "programs", "targets"),
    [
        ("xargs -i cp {} backup/", ("xargs", "cp"), {"<stdin>", "/w/backup"}),
        ("xargs -iX cp X backup/", ("xargs", "cp"), {"<stdin>", "/w/backup"}),
        ("xargs --replace=@ rm @", ("xargs", "rm"), {"<stdin>"}),
        ("xargs -0 -l -i basename {}", ("xargs", "basename"), set()),
        ("xargs ls -l", ("xargs", "ls"), {"<stdin>"}),
    ],
)
def test_xargs_optionally_attached_options(
    command: str, programs: tuple[str, ...], targets: set[str]
) -> None:
    # Found by the NL2Bash run: `-i` swallowed the command as its value.
    analysis = analyze(command)
    assert analysis.programs == programs
    assert {e.target for e in analysis.effects} == targets


# --- regressions found in the P0-P2 audit ------------------------------------------


def test_cd_inside_a_subshell_applies_in_order_and_does_not_leak() -> None:
    analysis = analyze("(cd x; rm y); rm z")
    assert [e.target for e in analysis.effects] == ["/w/x/y", "/w/z"]


def test_sudoedit_is_a_privileged_write_not_a_program() -> None:
    analysis = analyze("sudo -e /etc/hosts")
    assert analysis.programs == ("sudo",)
    assert [(e.kind, e.target, e.detail) for e in analysis.effects] == [
        (EffectKind.PRIVILEGE, "root", "sudo"),
        (EffectKind.WRITE, "/etc/hosts", "modify"),
    ]


@pytest.mark.parametrize(
    ("command", "reads"), [("dpkg -r nginx", []), ("dpkg -i ./a.deb", ["/w/a.deb"])]
)
def test_dpkg_reads_only_package_files(command: str, reads: list[str]) -> None:
    analysis = analyze(command)
    assert [e.target for e in analysis.effects if e.kind is EffectKind.READ] == reads


@pytest.mark.parametrize(
    ("command", "target"),
    [
        ("nc -l 8080", ":8080"),
        ("nc -lvp 4444", ":4444"),
        ("socat TCP-LISTEN:9000,fork EXEC:/bin/sh", ":9000"),
    ],
)
def test_listening_sockets_are_network_effects(command: str, target: str) -> None:
    (effect,) = analyze(command).effects
    assert (effect.kind, effect.target, effect.detail) == (EffectKind.NETWORK, target, "listen")
