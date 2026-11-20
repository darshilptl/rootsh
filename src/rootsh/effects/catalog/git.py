"""git: which subcommands are reads, recoverable history writes, or destructive."""

from __future__ import annotations

import posixpath
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from rootsh.effects.catalog.base import Invocation, Outcome, effect, file_effect, files
from rootsh.effects.model import Effect
from rootsh.effects.model import EffectKind as K
from rootsh.effects.options import Spec, parse
from rootsh.effects.words import Word, resolve

_GLOBAL_VALUES = frozenset({"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path"})


@dataclass(frozen=True, slots=True)
class Repo:
    root: str
    dynamic: bool
    inv: Invocation

    def path(self, suffix: str = "") -> str:
        return posixpath.join(self.root, suffix) if suffix else self.root

    def fx(self, kind: K, suffix: str = "", *, recursive: bool = False, detail: str = "") -> Effect:
        return Effect(
            kind,
            self.path(suffix),
            "git",
            recursive=recursive,
            dynamic=self.dynamic,
            detail=detail,
        )

    @property
    def history(self) -> Effect:
        return self.fx(K.WRITE, ".git", detail="modify")


def _split_global(inv: Invocation) -> tuple[Repo, str | None, list[Word]]:
    """Consume `git [-C dir] [-c k=v] …` and return the repo, subcommand, args."""
    ctx = inv.ctx
    args = list(inv.args)
    i = 0
    while i < len(args) and args[i].text.startswith("-") and not args[i].dynamic:
        text = args[i].text
        if text == "-C" and i + 1 < len(args):
            target = resolve(args[i + 1], ctx)
            ctx = ctx.with_cwd(None if target.dynamic else target.path)
            i += 2
        elif text in _GLOBAL_VALUES:
            i += 2
        else:
            i += 1
    root = "." if ctx.cwd is None else ctx.cwd
    repo = Repo(root, ctx.cwd is None, Invocation(inv.program, inv.args, ctx))
    if i >= len(args):
        return repo, None, []
    return repo, args[i].text, args[i + 1 :]


def _after_double_dash(args: list[Word]) -> tuple[list[Word], list[Word] | None]:
    for index, word in enumerate(args):
        if word.text == "--" and not word.dynamic:
            return args[:index], args[index + 1 :]
    return args, None


# --- subcommands ---------------------------------------------------------------

_READ_ONLY = frozenset(
    {
        "status", "log", "diff", "show", "blame", "grep", "ls-files", "ls-tree", "rev-parse",
        "rev-list", "shortlog", "describe", "cat-file", "show-ref", "for-each-ref",
        "whatchanged", "count-objects", "fsck", "help", "version", "diff-tree", "diff-index",
        "diff-files", "name-rev", "check-ignore", "var",
    }
)  # fmt: skip
_HISTORY = frozenset(
    {
        "add", "commit", "merge", "rebase", "cherry-pick", "revert", "am", "switch", "notes",
        "update-index", "commit-tree", "update-ref", "bisect", "pull-request", "replace",
        "submodule", "sparse-checkout", "filter-branch", "worktree", "gc", "repack", "maintenance",
    }
)  # fmt: skip


def _reset(repo: Repo, args: list[Word]) -> Outcome:
    p = parse(args, Spec.of())
    if p.has("hard", "merge"):
        return Outcome([repo.fx(K.WRITE, recursive=True, detail="discard-changes")])
    return Outcome([repo.history])


def _checkout(repo: Repo, args: list[Word]) -> Outcome:
    before, paths = _after_double_dash(args)
    if paths is not None:
        return Outcome(files(K.WRITE, paths, repo.inv, detail="discard-changes"))
    p = parse(before, Spec.of("bB", ["orphan"]))
    if not p.has("b", "B", "orphan") and any(w.text == "." for w in p.positionals):
        return Outcome([repo.fx(K.WRITE, recursive=True, detail="discard-changes")])
    return Outcome([repo.history])


def _restore(repo: Repo, args: list[Word]) -> Outcome:
    p = parse(args, Spec.of("s", ["source"]))
    if p.has("S", "staged") and not p.has("W", "worktree"):
        return Outcome([repo.history])
    return Outcome(files(K.WRITE, p.positionals, repo.inv, detail="discard-changes"))


def _clean(repo: Repo, args: list[Word]) -> Outcome:
    p = parse(args, Spec.of("e", ["exclude"]))
    if p.has("n", "dry-run") or not p.has("f", "force", "i", "interactive"):
        return Outcome([repo.fx(K.READ, recursive=True)])
    if p.positionals:
        return Outcome(files(K.DELETE, p.positionals, repo.inv, detail="untracked", recursive=True))
    return Outcome([repo.fx(K.DELETE, recursive=True, detail="untracked")])


_PUSH = Spec.of("o", ["repo", "push-option", "receive-pack", "exec", "recurse-submodules"])
_FORCE = ("f", "force", "force-with-lease", "force-if-includes")


def _remote_target(repo: Repo, positionals: list[Word], inv: Invocation, detail: str) -> Effect:
    if positionals:
        return effect(
            K.NETWORK, positionals[0].text, inv, detail=detail, dynamic=positionals[0].dynamic
        )
    return effect(K.NETWORK, "upstream", inv, detail=detail, dynamic=True)


def _push(repo: Repo, args: list[Word]) -> Outcome:
    p = parse(args, _PUSH)
    refspecs = [w.text for w in p.positionals[1:]]
    if p.has("d", "delete") or any(spec.startswith(":") for spec in refspecs):
        detail = "delete-ref"
    elif p.has(*_FORCE) or any(spec.startswith("+") for spec in refspecs):
        detail = "force-push"
    else:
        detail = "push"
    return Outcome([_remote_target(repo, p.positionals, repo.inv, detail)])


_FETCH = Spec.of(
    "jX",
    ["depth", "deepen", "shallow-since", "jobs", "upload-pack", "refmap", "strategy",
     "strategy-option", "recurse-submodules"],
)  # fmt: skip


def _fetch(repo: Repo, args: list[Word]) -> Outcome:
    p = parse(args, _FETCH)
    return Outcome([_remote_target(repo, p.positionals, repo.inv, "fetch"), repo.history])


_CLONE = Spec.of(
    "bocju",
    ["branch", "origin", "depth", "config", "template", "reference", "separate-git-dir",
     "jobs", "filter", "shallow-since", "upload-pack"],
)  # fmt: skip


def _clone(repo: Repo, args: list[Word]) -> Outcome:
    p = parse(args, _CLONE)
    if not p.positionals:
        return Outcome()
    url = p.positionals[0]
    out = Outcome([effect(K.NETWORK, url.text, repo.inv, detail="fetch", dynamic=url.dynamic)])
    if len(p.positionals) > 1:
        dest = p.positionals[1]
    else:
        name = url.text.rstrip("/").replace(":", "/").rsplit("/", 1)[-1]
        dest = Word(name.removesuffix(".git"), dynamic=url.dynamic)
    out.add(file_effect(K.WRITE, dest, repo.inv, recursive=True, detail="create"))
    return out


def _config(repo: Repo, args: list[Word]) -> Outcome:
    p = parse(args, Spec.of("f", ["file", "blob", "type", "default"]))
    if p.has("global"):
        path = posixpath.join(repo.inv.ctx.home, ".gitconfig")
    elif p.has("system"):
        path = "/etc/gitconfig"
    elif (file := p.value("f", "file")) is not None:
        path = resolve(file, repo.inv.ctx).path
    else:
        path = repo.path(".git/config")
    writes = p.has(
        "unset", "unset-all", "add", "replace-all", "rename-section", "remove-section", "e", "edit"
    )
    reads = p.has("l", "list", "get", "get-all", "get-regexp")
    if writes or (len(p.positionals) >= 2 and not reads):
        return Outcome([effect(K.WRITE, path, repo.inv, detail="config")])
    return Outcome([effect(K.READ, path, repo.inv)])


_REMOTE_WRITES = frozenset(
    {"add", "set-url", "remove", "rm", "rename", "set-head", "set-branches", "prune"}
)


def _remote(repo: Repo, args: list[Word]) -> Outcome:
    p = parse(args, Spec.of("t", ["track"]))
    if p.positionals and p.positionals[0].text in _REMOTE_WRITES:
        return Outcome([repo.fx(K.WRITE, ".git/config", detail="config")])
    return Outcome([repo.fx(K.READ, ".git/config")])


def _branch(repo: Repo, args: list[Word]) -> Outcome:
    p = parse(args, Spec.of("tu", ["track", "set-upstream-to", "contains", "merged", "points-at"]))
    if p.has("d", "D", "delete"):
        return Outcome(
            [repo.fx(K.DELETE, f".git/refs/heads/{w.text}", detail="ref") for w in p.positionals]
        )
    if p.positionals or p.has("m", "M", "c", "C", "move", "copy"):
        return Outcome([repo.history])
    return Outcome([repo.fx(K.READ, ".git")])


def _tag(repo: Repo, args: list[Word]) -> Outcome:
    p = parse(args, Spec.of("mFu", ["message", "file", "local-user"]))
    if p.has("d", "delete"):
        return Outcome(
            [repo.fx(K.DELETE, f".git/refs/tags/{w.text}", detail="ref") for w in p.positionals]
        )
    if p.positionals:
        return Outcome([repo.history])
    return Outcome([repo.fx(K.READ, ".git")])


def _stash(repo: Repo, args: list[Word]) -> Outcome:
    action = args[0].text if args else "push"
    if action in ("drop", "clear"):
        return Outcome([repo.fx(K.DELETE, ".git/refs/stash", detail="ref")])
    if action in ("list", "show"):
        return Outcome([repo.fx(K.READ, ".git")])
    return Outcome([repo.history])


def _rm(repo: Repo, args: list[Word]) -> Outcome:
    p = parse(args, Spec.of())
    if p.has("cached"):
        return Outcome([repo.history])
    return Outcome(files(K.DELETE, p.positionals, repo.inv, recursive=p.has("r")))


def _mv(repo: Repo, args: list[Word]) -> Outcome:
    p = parse(args, Spec.of())
    if len(p.positionals) < 2:
        return Outcome()
    dest = resolve(p.positionals[-1], repo.inv.ctx).path
    out = Outcome()
    for source in p.positionals[:-1]:
        out.add(file_effect(K.MOVE, source, repo.inv, dest=dest))
    return out


def _init(repo: Repo, args: list[Word]) -> Outcome:
    p = parse(args, Spec.of("b", ["initial-branch", "template", "separate-git-dir"]))
    base = file_effect(K.WRITE, p.positionals[0], repo.inv) if p.positionals else None
    root = base.target if base is not None else repo.root
    return Outcome([effect(K.WRITE, posixpath.join(root, ".git"), repo.inv, detail="create")])


def _apply(repo: Repo, args: list[Word]) -> Outcome:
    p = parse(args, Spec.of("p", ["directory", "exclude", "include"]))
    if p.has("check", "stat", "numstat", "summary"):
        return Outcome(files(K.READ, p.positionals, repo.inv))
    out = Outcome(files(K.READ, p.positionals, repo.inv))
    out.effects.append(repo.fx(K.WRITE, recursive=True, detail="modify"))
    return out


def _reflog(repo: Repo, args: list[Word]) -> Outcome:
    """`git reflog [show]` lists; `expire` and `delete` drop the entries that
    let a lost commit be recovered."""
    action = args[0].text if args and not args[0].text.startswith("-") else "show"
    if action in ("expire", "delete"):
        return Outcome([repo.fx(K.DELETE, ".git/logs", recursive=True, detail="ref")])
    return Outcome([repo.fx(K.READ, recursive=True)])


_SUBCOMMANDS: Mapping[str, Callable[[Repo, list[Word]], Outcome]] = {
    "reset": _reset,
    "checkout": _checkout,
    "restore": _restore,
    "clean": _clean,
    "push": _push,
    "pull": _fetch,
    "fetch": _fetch,
    "clone": _clone,
    "config": _config,
    "remote": _remote,
    "branch": _branch,
    "tag": _tag,
    "stash": _stash,
    "rm": _rm,
    "mv": _mv,
    "init": _init,
    "apply": _apply,
    "reflog": _reflog,
}


def git(inv: Invocation) -> Outcome:
    repo, sub, args = _split_global(inv)
    if sub is None:
        return Outcome()
    if sub in _READ_ONLY:
        return Outcome([repo.fx(K.READ, recursive=True)])
    if sub in _HISTORY:
        return Outcome([repo.history])
    handler = _SUBCOMMANDS.get(sub)
    if handler is not None:
        return handler(repo, args)
    # Possibly a user alias (`alias.x = !shell`), which can run anything.
    return Outcome([effect(K.EXEC, f"git-{sub}", inv)], opaque=[f"unknown_program:git-{sub}"])
