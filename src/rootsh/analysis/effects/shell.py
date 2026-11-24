"""Shell analysis on tree-sitter-bash (design doc §3).

Walks the syntax tree statically: nothing is executed. The working directory
is threaded through `cd`; subshells and pipeline stages get their own copy;
every branch of a conditional or loop is analysed; command and process
substitutions are analysed because they run.
"""

from __future__ import annotations

import posixpath

import tree_sitter_bash
from tree_sitter import Language, Node, Parser

from rootsh.analysis.effects.catalog import Invocation, known, lookup
from rootsh.analysis.effects.model import Analysis, Context, Effect, EffectKind, check_opaque
from rootsh.analysis.effects.options import Spec, parse
from rootsh.analysis.effects.words import (
    SUBSTITUTIONS,
    Word,
    evaluate,
    find_substitutions,
    is_special_file,
    resolve,
)

MAX_NESTING = 4  # bash -c 'bash -c "…"' levels
MAX_EFFECTS = 1_000
MAX_TREE_DEPTH = 200  # statement nesting inside one script

_PARSER = Parser(Language(tree_sitter_bash.language()))
_SYSTEM_BIN = frozenset(
    {"/bin", "/usr/bin", "/usr/local/bin", "/sbin", "/usr/sbin", "/usr/local/sbin"}
)
_BLOCKS = frozenset({"program", "compound_statement", "do_group", "negated_command"})
_BRANCHING = frozenset(
    {
        "if_statement", "elif_clause", "else_clause", "while_statement", "for_statement",
        "c_style_for_statement", "case_statement", "case_item",
    }
)  # fmt: skip
_QUIET = frozenset({"variable_assignment", "variable_assignments", "test_command"})
_REDIRECTS = frozenset({"file_redirect", "heredoc_redirect", "herestring_redirect"})
STATEMENTS = (
    _BLOCKS
    | _BRANCHING
    | _QUIET
    | _REDIRECTS
    | {
        "command", "list", "pipeline", "subshell", "redirected_statement", "function_definition",
        "declaration_command", "unset_command", "comment", "ERROR",
    }
)  # fmt: skip
_WRITE_TRUNCATE = frozenset({">", ">|", "&>", ">&"})
_WRITE_APPEND = frozenset({">>", "&>>"})


class _Walker:
    def __init__(self) -> None:
        self.effects: list[Effect] = []
        self.programs: list[str] = []
        self.opaque: dict[str, None] = {}  # insertion-ordered set
        self.parse_error = False
        self.functions: set[str] = set()
        self.nesting = 0
        self.depth = 0

    # --- bookkeeping ---------------------------------------------------------

    def add(self, effect: Effect) -> None:
        if len(self.effects) >= MAX_EFFECTS:
            self.flag("effect_limit")
        else:
            self.effects.append(effect)

    def flag(self, reason: str) -> None:
        self.opaque[check_opaque(reason)] = None

    def mark_parse_error(self) -> None:
        self.parse_error = True
        self.flag("parse_error")

    def result(self) -> Analysis:
        return Analysis(
            tuple(self.effects), tuple(self.programs), tuple(sorted(self.opaque)), self.parse_error
        )

    # --- sources ---------------------------------------------------------------

    def source(self, text: str, ctx: Context) -> None:
        if self.nesting >= MAX_NESTING:
            self.flag("nested_depth")
            return
        root = _PARSER.parse(text.encode("utf-8")).root_node
        if root.has_error:
            self.mark_parse_error()
        self.nesting += 1
        try:
            self.mixed(root, ctx)
        finally:
            self.nesting -= 1

    # --- statements ---------------------------------------------------------------

    def mixed(self, node: Node, ctx: Context) -> Context:
        """Children that are statements run in order; words only get their substitutions."""
        for child in node.named_children:
            if child.type in STATEMENTS:
                ctx = self.statement(child, ctx)
            else:
                self.substitutions(child, ctx)
        return ctx

    def statement(self, node: Node, ctx: Context) -> Context:
        if self.depth >= MAX_TREE_DEPTH:
            self.flag("nested_depth")
            return ctx
        self.depth += 1
        try:
            return self._statement(node, ctx)
        finally:
            self.depth -= 1

    def _statement(self, node: Node, ctx: Context) -> Context:
        kind = node.type
        if kind == "command":
            return self.command(node, ctx)
        if kind in ("list", *_BLOCKS):
            return self.mixed(node, ctx)
        if kind == "subshell":
            self.mixed(node, ctx)  # statements inside run in order; nothing leaks out
            return ctx
        if kind == "pipeline":
            for child in node.named_children:  # each stage runs in its own subshell
                if child.type in STATEMENTS:
                    self.statement(child, ctx)
                else:
                    self.substitutions(child, ctx)
            return ctx
        if kind in _BRANCHING:
            after = self.mixed(node, ctx)
            # A cd inside a branch may or may not have happened.
            return ctx if after.cwd == ctx.cwd else ctx.with_cwd(None)
        if kind == "redirected_statement":
            body = node.child_by_field_name("body")
            after = self.statement(body, ctx) if body is not None else ctx
            # tree-sitter-bash hangs `a && b > f` on the whole list, but bash
            # binds the redirect to `b`, after any cd in `a`. A real compound
            # (`{ cd x; b; } > f`) opens f first, in the original directory.
            at = after if body is not None and body.type == "list" else ctx
            for redirect in node.children_by_field_name("redirect"):
                self.redirect(redirect, at)
            return after
        if kind in _REDIRECTS:
            self.redirect(node, ctx)
            return ctx
        if kind == "function_definition":
            name = node.child_by_field_name("name")
            if name is not None:
                self.functions.add((name.text or b"").decode())
            body = node.child_by_field_name("body")
            if body is not None:
                self.statement(body, ctx)
            return ctx
        if kind == "declaration_command" and node.children:
            self.programs.append(node.children[0].type)
            self.substitutions(node, ctx)
            return ctx
        if kind == "unset_command":
            self.programs.append("unset")
            self.substitutions(node, ctx)
            return ctx
        if kind in _QUIET:
            self.substitutions(node, ctx)
            return ctx
        if kind == "comment":
            return ctx
        if kind == "ERROR":
            self.mark_parse_error()
            return self.mixed(node, ctx)
        self.flag(f"unsupported_syntax:{kind}")
        self.substitutions(node, ctx)
        return ctx

    def substitutions(self, node: Node, ctx: Context) -> None:
        subs = (node,) if node.type in SUBSTITUTIONS else find_substitutions(node)
        for sub in subs:
            self.statement_block(sub, ctx)

    def statement_block(self, sub: Node, ctx: Context) -> None:
        """The inside of `$(…)` / `<(…)` runs in a subshell."""
        self.depth += 1
        try:
            if self.depth > MAX_TREE_DEPTH:
                self.flag("nested_depth")
                return
            self.mixed(sub, ctx)
        finally:
            self.depth -= 1

    def word_substitutions(self, words: list[Word], ctx: Context) -> None:
        for word in words:
            for sub in word.substitutions:
                self.statement_block(sub, ctx)

    # --- commands ---------------------------------------------------------------

    def command(self, node: Node, ctx: Context) -> Context:
        for child in node.named_children:
            if child.type == "variable_assignment":
                self.substitutions(child, ctx)
        name_node = node.child_by_field_name("name")
        after = ctx
        if name_node is not None:
            name = evaluate(name_node, ctx)
            args = [evaluate(arg, ctx) for arg in node.children_by_field_name("argument")]
            after = self.invoke(name, args, ctx)
        for child in node.named_children:
            if child.type in _REDIRECTS:
                self.redirect(child, ctx)
        return after

    def invoke(self, name: Word, args: list[Word], ctx: Context) -> Context:
        if not name.text:  # a missing name from parse-error recovery: nothing runs
            self.word_substitutions(args, ctx)
            return ctx
        if name.dynamic:
            self.programs.append(name.text)
            self.flag("dynamic_command")
            self.word_substitutions([name, *args], ctx)
            return ctx
        program = name.text
        if "/" in program:
            directory, base = posixpath.split(program)
            if directory in _SYSTEM_BIN and known(base):
                program = base
            else:
                return self.run_path(name, args, ctx)
        self.programs.append(program)
        if program == "cd":
            self.word_substitutions(args, ctx)
            return self.cd(args, ctx)
        if program in self.functions:
            self.word_substitutions(args, ctx)
            return ctx
        handler = lookup(program)
        if handler is None:
            self.add(Effect(EffectKind.EXEC, program, program))
            self.flag(f"unknown_program:{program}")
            self.word_substitutions(args, ctx)
            return ctx
        outcome = handler(Invocation(program, tuple(args), ctx))
        for effect in outcome.effects:
            self.add(effect)
        for reason in outcome.opaque:
            self.flag(reason)
        forwarded: set[int] = set()
        for argv, inner_ctx in outcome.nested:
            forwarded.update(id(word) for word in argv)
            self.invoke(argv[0], list(argv[1:]), inner_ctx)
        for code, code_ctx in outcome.code:
            self.source(code, code_ctx)
        self.word_substitutions([w for w in args if id(w) not in forwarded], ctx)
        return ctx

    def run_path(self, name: Word, args: list[Word], ctx: Context) -> Context:
        """`./run.sh`, `bin/tool`: executing a file whose behavior we cannot see."""
        target = resolve(name, ctx)
        self.programs.append(target.path)
        self.add(
            Effect(
                EffectKind.EXEC, target.path, target.path, dynamic=target.dynamic, detail="script"
            )
        )
        self.flag("script_exec")
        self.word_substitutions(args, ctx)
        return ctx

    def cd(self, args: list[Word], ctx: Context) -> Context:
        p = parse(args, Spec.of())
        if not p.positionals:
            return ctx.with_cwd(ctx.home)
        destination = p.positionals[0]
        if destination.text == "-":
            return ctx.with_cwd(None)
        target = resolve(destination, ctx)
        return ctx.with_cwd(None if target.dynamic else target.path)

    # --- redirects ---------------------------------------------------------------

    def redirect(self, node: Node, ctx: Context) -> None:
        if node.type == "heredoc_redirect":
            for child in node.named_children:
                if child.type == "file_redirect":
                    self.redirect(child, ctx)
                elif child.type == "heredoc_body":
                    self.substitutions(child, ctx)
            return
        if node.type != "file_redirect":
            self.substitutions(node, ctx)
            return
        destination = node.child_by_field_name("destination")
        if destination is None:
            return
        word = evaluate(destination, ctx)
        self.word_substitutions([word], ctx)
        operator = next((c.type for c in node.children if not c.is_named), "")
        if operator in (">&", "<&") and (word.text.isdigit() or word.text == "-"):
            return  # descriptor duplication or close
        if operator in _WRITE_TRUNCATE:
            kind, detail = EffectKind.WRITE, "truncate"
        elif operator in _WRITE_APPEND:
            kind, detail = EffectKind.WRITE, "append"
        elif operator == "<":
            kind, detail = EffectKind.READ, ""
        elif operator == "<>":
            kind, detail = EffectKind.WRITE, "modify"
        else:
            return
        target = resolve(word, ctx)
        if word.pipe or is_special_file(target):
            return
        self.add(
            Effect(
                kind,
                target.path,
                "redirect",
                dynamic=target.dynamic,
                glob=target.glob,
                detail=detail,
            )
        )


def analyze_shell(command: str, ctx: Context) -> Analysis:
    """What running `command` in `ctx` would do, without running it."""
    walker = _Walker()
    root = _PARSER.parse(command.encode("utf-8")).root_node
    if root.has_error:
        walker.mark_parse_error()
    walker.mixed(root, ctx)
    return walker.result()
