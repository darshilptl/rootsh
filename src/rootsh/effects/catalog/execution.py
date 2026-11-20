"""Wrappers, shells, interpreters, package managers, and build tools."""

from __future__ import annotations

from collections.abc import Mapping

from rootsh.effects.catalog.base import (
    HERE,
    Handler,
    Invocation,
    Outcome,
    effect,
    file_effect,
    files,
    path_of,
)
from rootsh.effects.model import EffectKind as K
from rootsh.effects.options import Spec, parse
from rootsh.effects.words import Word, resolve

SX, PK, IC, SC = "script_exec", "package_install", "interpreter_code", "stdin_code"
STDIN = Word("<stdin>", dynamic=True)


def _stdin_code(inv: Invocation) -> Outcome:
    return Outcome([effect(K.EXEC, inv.program, inv, detail="stdin-code")], opaque=[SC])


def _script(inv: Invocation, script: Word) -> Outcome:
    out = Outcome(opaque=[SX])
    out.add(file_effect(K.EXEC, script, inv, detail="script"))
    return out


def _code(inv: Invocation) -> Outcome:
    return Outcome([effect(K.EXEC, inv.program, inv, detail="code")], opaque=[IC])


# --- privilege and plain wrappers ----------------------------------------------

_PRIVILEGE_SPECS: Mapping[str, Spec] = {
    "sudo": Spec.of(
        "ughpCTDrtU",
        ["user", "group", "host", "prompt", "close-from", "chdir", "role", "type", "other-user"],
        stop=True,
    ),
    "doas": Spec.of("uC", stop=True),
    "pkexec": Spec.of("", ["user"], stop=True),
}


def privileged(inv: Invocation) -> Outcome:
    p = parse(inv.args, _PRIVILEGE_SPECS[inv.program])
    if p.has(
        "l",
        "list",
        "v",
        "validate",
        "k",
        "K",
        "reset-timestamp",
        "remove-timestamp",
        "V",
        "version",
    ):
        return Outcome()
    user = p.value("u", "user")
    out = Outcome(
        [
            effect(
                K.PRIVILEGE,
                user.text if user else "root",
                inv,
                detail=inv.program,
                dynamic=bool(user and user.dynamic),
            )
        ]
    )
    if p.has("e", "edit"):  # sudoedit: edit the operands as the target user
        out.effects += files(K.WRITE, p.positionals, inv, detail="modify")
        return out
    if not p.positionals:  # `sudo -i`, `sudo -s`: an interactive root shell
        stdin = _stdin_code(inv)
        out.effects += stdin.effects
        out.opaque += stdin.opaque
        return out
    ctx = inv.ctx
    if (directory := p.value("D", "chdir")) is not None:
        target = resolve(directory, ctx)
        ctx = ctx.with_cwd(None if target.dynamic else target.path)
    out.nested.append((p.positionals, ctx))
    return out


def su(inv: Invocation) -> Outcome:
    p = parse(
        inv.args, Spec.of("csgG", ["command", "shell", "group", "supp-group", "session-command"])
    )
    users = [w for w in p.positionals if w.text != "-"]
    user = users[0] if users else None
    out = Outcome(
        [
            effect(
                K.PRIVILEGE,
                user.text if user else "root",
                inv,
                detail="su",
                dynamic=bool(user and user.dynamic),
            )
        ]
    )
    command = p.value("c", "command", "session-command")
    if command is None:
        stdin = _stdin_code(inv)
        out.effects += stdin.effects
        out.opaque += stdin.opaque
    elif command.dynamic:
        out.opaque.append(IC)
    else:
        out.code.append((command.text, inv.ctx))
    return out


def wrapper(spec: Spec, *, skip: int = 0, output: tuple[str, ...] = ()) -> Handler:
    """Runs its operands as a command; `skip` leading operands are its own (a duration)."""

    def handler(inv: Invocation) -> Outcome:
        p = parse(inv.args, spec)
        out = Outcome()
        if output and (log := p.value(*output)) is not None:
            out.add(file_effect(K.WRITE, log, inv, detail="truncate"))
        command = p.positionals[skip:]
        if command:
            out.nested.append((command, inv.ctx))
        return out

    return handler


def command(inv: Invocation) -> Outcome:
    p = parse(inv.args, Spec.of(stop=True))
    if p.has("v", "V") or not p.positionals:
        return Outcome()
    return Outcome(nested=[(p.positionals, inv.ctx)])


def env(inv: Invocation) -> Outcome:
    ctx = inv.ctx
    args = list(inv.args)
    out = Outcome()
    i = 0
    while i < len(args):
        word = args[i]
        text = word.text
        if word.dynamic:
            break
        if text in ("-u", "--unset", "-C", "--chdir", "-S", "--split-string") and i + 1 < len(args):
            value = args[i + 1]
            if text in ("-C", "--chdir"):
                target = resolve(value, ctx)
                ctx = ctx.with_cwd(None if target.dynamic else target.path)
            elif text in ("-S", "--split-string"):
                if value.dynamic:
                    out.opaque.append(IC)
                else:
                    out.code.append((value.text, ctx))
            i += 2
        elif text.startswith("-") or ("=" in text and not text.startswith("=")):
            i += 1  # a flag (-i, -0) or a NAME=VALUE assignment
        else:
            break
    if i < len(args):
        out.nested.append((args[i:], ctx))
    return out


_XARGS = Spec.of(
    "IdELnPsa",
    ["delimiter", "max-args", "max-procs", "max-chars", "arg-file"],
    stop=True,
)
_XARGS_VALUES = frozenset({"-I", "-d", "-E", "-L", "-n", "-P", "-s", "-a"})


def _xargs_options(args: tuple[Word, ...]) -> list[Word]:
    """Rewrite GNU's optionally-attached options before the command starts:
    `-i[R]`/`--replace[=R]` -> `-I R` (R defaults to `{}`); `-e[EOF]`,
    `-l[N]`, `--eof[=EOF]`, `--max-lines[=N]` are dropped (no effect here)."""
    out: list[Word] = []
    i = 0
    while i < len(args):
        word = args[i]
        text = word.text
        if word.dynamic or not text.startswith("-") or text == "--":
            return [*out, *args[i:]]
        if text.startswith("-i") or text.startswith("--replace"):
            marker = text.partition("=")[2] if text.startswith("--") else text[2:]
            out += [Word("-I"), Word(marker or "{}")]
        elif not text.startswith(("-e", "-l", "--eof", "--max-lines")):
            out.append(word)
            if text in _XARGS_VALUES and i + 1 < len(args):
                out.append(args[i + 1])
                i += 1
        i += 1
    return out


def xargs(inv: Invocation) -> Outcome:
    p = parse(_xargs_options(inv.args), _XARGS)
    out = Outcome(files(K.READ, p.all_values("a", "arg-file"), inv))
    command = p.positionals or [Word("echo")]
    marker = p.value("I")
    if marker is not None and marker.text:
        command = [
            Word(w.text.replace(marker.text, STDIN.text), dynamic=True)
            if marker.text in w.text
            else w
            for w in command
        ]
    else:
        command = [*command, STDIN]
    out.nested.append((command, inv.ctx))
    return out


_FIND_EXEC = frozenset({"-exec", "-execdir", "-ok", "-okdir"})
_FIND_WRITES = frozenset({"-fprint", "-fprint0", "-fprintf", "-fls"})


def find(inv: Invocation) -> Outcome:
    args = list(inv.args)
    i = 0
    while i < len(args) and not args[i].dynamic:  # leading options: -H -L -P -D opts -O3
        text = args[i].text
        if text in ("-H", "-L", "-P") or (text.startswith("-O") and text[2:].isdigit()):
            i += 1
        elif text == "-D":
            i += 2
        else:
            break
    paths: list[Word] = []
    while i < len(args) and (
        args[i].dynamic or not args[i].text.startswith(("-", "(", "!", ")", ","))
    ):
        paths.append(args[i])
        i += 1
    paths = paths or [HERE]
    expression = args[i:]
    out = Outcome(files(K.READ, paths, inv, recursive=True))
    stands_for = [
        Word(path_of(p, inv), dynamic=True, subtree=True) for p in paths
    ]  # what `{}` expands to: anything under each search path
    j = 0
    while j < len(expression):
        primary = expression[j].text
        if primary == "-delete":
            for each in stands_for:
                out.add(file_effect(K.DELETE, each, inv))
        elif primary in _FIND_WRITES and j + 1 < len(expression):
            out.add(file_effect(K.WRITE, expression[j + 1], inv, detail="truncate"))
            j += 1
        elif primary in _FIND_EXEC:
            end = j + 1
            while end < len(expression) and expression[end].text not in (";", "+"):
                end += 1
            template = expression[j + 1 : end]
            ctx = inv.ctx if primary in ("-exec", "-ok") else inv.ctx.with_cwd(None)
            for each in stands_for:
                command = [
                    Word(w.text.replace("{}", each.text), dynamic=True, subtree=True)
                    if "{}" in w.text
                    else w
                    for w in template
                ]
                if command:
                    out.nested.append((command, ctx))
            j = end
        j += 1
    return out


# --- shells and interpreters -----------------------------------------------------


def shell(inv: Invocation) -> Outcome:
    args: list[Word] = []
    words = list(inv.args)
    i = 0
    while i < len(words):  # drop `+o name` / `+x`: set-option toggles, not operands
        if words[i].text == "+o":
            i += 2
            continue
        if not words[i].text.startswith("+"):
            args.append(words[i])
        i += 1
    p = parse(args, Spec.of("oO", ["rcfile", "init-file"], stop=True))
    if p.has("c"):
        if not p.positionals:
            return Outcome()
        code = p.positionals[0]
        if code.dynamic:
            return _code(inv)
        return Outcome(code=[(code.text, inv.ctx)])
    if p.positionals and p.positionals[0].text != "-" and not p.has("s"):
        return _script(inv, p.positionals[0])
    return _stdin_code(inv)


# Options that print a version or help text. Only with no script and no code
# do they run nothing: `python -v` is verbose, `ruby -v x.rb` runs x.rb.
_INFO = ("version", "help")


def interpreter(
    spec: Spec,
    code_options: tuple[str, ...],
    *,
    in_place: bool = False,
    info: tuple[str, ...] = ("v", "h"),
) -> Handler:
    def handler(inv: Invocation) -> Outcome:
        p = parse(inv.args, spec)
        if p.has(*info, *_INFO) and not p.has(*code_options) and not p.positionals:
            return Outcome()
        if p.has(*code_options):
            out = _code(inv)
            if in_place and p.has("i"):
                out.effects += files(K.WRITE, p.positionals, inv, detail="modify")
            return out
        if p.positionals and p.positionals[0].text != "-":
            return _script(inv, p.positionals[0])
        return _stdin_code(inv)

    return handler


_PIP_INSTALLS = frozenset({"install", "download", "wheel"})


def python(inv: Invocation) -> Outcome:
    p = parse(inv.args, Spec.of("cmWXQ", stop=True))
    if p.has("V", "h", *_INFO) and not p.has("c", "m") and not p.positionals:
        return Outcome()
    if p.has("c"):
        return _code(inv)
    module = p.value("m")
    if module is not None:
        if module.text == "pip" and p.positionals and p.positionals[0].text in _PIP_INSTALLS:
            return _package(inv, "registry:pip", target="pip")
        return Outcome(
            [effect(K.EXEC, module.text, inv, detail="script", dynamic=module.dynamic)], opaque=[SX]
        )
    if p.positionals and p.positionals[0].text != "-":
        return _script(inv, p.positionals[0])
    return _stdin_code(inv)


# --- package managers and build tools ------------------------------------------------


def _package(inv: Invocation, registry: str | None, *, target: str | None = None) -> Outcome:
    out = Outcome(
        [effect(K.EXEC, target or inv.program, inv, detail="package-install")], opaque=[PK]
    )
    if registry is not None:
        out.effects.append(effect(K.NETWORK, registry, inv, detail="fetch"))
    return out


def _build(inv: Invocation) -> Outcome:
    return Outcome([effect(K.EXEC, inv.program, inv, detail="build")], opaque=[SX])


def _subcommand(inv: Invocation) -> tuple[str | None, list[Word]]:
    p = parse(
        inv.args, Spec.of("C", ["prefix", "cwd", "dir", "filter", "project", "manifest-path"])
    )
    if not p.positionals:
        return None, []
    return p.positionals[0].text, p.positionals[1:]


_NODE_INSTALLS = frozenset(
    {
        "install",
        "i",
        "in",
        "isntall",
        "ci",
        "add",
        "update",
        "up",
        "upgrade",
        "rebuild",
        "dlx",
        "create",
        "init",
    }
)
_NODE_READ_ONLY = frozenset(
    {
        "ls",
        "list",
        "view",
        "info",
        "outdated",
        "help",
        "search",
        "whoami",
        "why",
        "explain",
        "audit",
        "config",
        "doctor",
    }
)


def node_package_manager(inv: Invocation) -> Outcome:
    sub, _ = _subcommand(inv)
    if sub is None:
        return _package(inv, "registry:npm") if inv.program == "yarn" else Outcome()
    if sub in _NODE_INSTALLS:
        return _package(inv, "registry:npm")
    if sub in _NODE_READ_ONLY:
        return Outcome()
    return _build(inv)


def pip(inv: Invocation) -> Outcome:
    sub, _ = _subcommand(inv)
    if sub in _PIP_INSTALLS:
        return _package(inv, "registry:pip")
    if sub == "uninstall":
        return _package(inv, None)
    return Outcome()


def uv(inv: Invocation) -> Outcome:
    sub, rest = _subcommand(inv)
    if sub == "run":
        return _build(inv)
    if sub in ("sync", "add", "remove", "lock", "tool") or (
        sub == "pip" and rest and rest[0].text in ("install", "sync", "compile")
    ):
        return _package(inv, "registry:pip")
    return Outcome()


def cargo(inv: Invocation) -> Outcome:
    sub, _ = _subcommand(inv)
    if sub in ("install", "add"):
        return _package(inv, "registry:cargo")
    if sub in ("build", "b", "run", "r", "test", "t", "check", "c", "bench", "clippy", "doc"):
        return _build(inv)
    if sub == "fetch":
        return Outcome([effect(K.NETWORK, "registry:cargo", inv, detail="fetch")])
    return Outcome()


def go(inv: Invocation) -> Outcome:
    sub, rest = _subcommand(inv)
    if sub in ("install", "get"):
        return _package(inv, "registry:go")
    if sub in ("build", "run", "test", "generate", "vet"):
        return _build(inv)
    if sub == "mod" and rest and rest[0].text in ("download", "tidy"):
        return Outcome([effect(K.NETWORK, "registry:go", inv, detail="fetch")])
    return Outcome()


_CONTAINER_RUNS = frozenset({"run", "exec", "start", "create", "compose", "restart", "attach"})


def container(inv: Invocation) -> Outcome:
    sub, _ = _subcommand(inv)
    if sub in _CONTAINER_RUNS:
        return Outcome([effect(K.EXEC, inv.program, inv, detail="script")], opaque=[SX])
    if sub in ("build", "buildx"):
        return _build(inv)
    if sub == "pull":
        return Outcome([effect(K.NETWORK, f"registry:{inv.program}", inv, detail="fetch")])
    if sub == "push":
        return Outcome([effect(K.NETWORK, f"registry:{inv.program}", inv, detail="push")])
    return Outcome()


_SYSTEM_PACKAGE_REGISTRY: Mapping[str, str] = {
    "apt": "registry:apt", "apt-get": "registry:apt", "aptitude": "registry:apt",
    "dnf": "registry:rpm", "yum": "registry:rpm", "zypper": "registry:rpm",
    "snap": "registry:snap", "brew": "registry:brew", "flatpak": "registry:flatpak",
}  # fmt: skip
_SYSTEM_INSTALLS = frozenset(
    {"install", "reinstall", "remove", "purge", "upgrade", "full-upgrade", "dist-upgrade",
     "autoremove", "refresh", "uninstall", "erase", "in", "rm"}
)  # fmt: skip


def system_packages(inv: Invocation) -> Outcome:
    sub, _ = _subcommand(inv)
    if sub in _SYSTEM_INSTALLS:
        return _package(inv, _SYSTEM_PACKAGE_REGISTRY[inv.program])
    if sub == "update":
        return Outcome(
            [effect(K.NETWORK, _SYSTEM_PACKAGE_REGISTRY[inv.program], inv, detail="fetch")]
        )
    return Outcome()


def dpkg(inv: Invocation) -> Outcome:
    p = parse(inv.args, Spec.of("", ["admindir", "root", "instdir"]))
    if p.has("i", "install", "unpack"):  # operands are .deb files
        out = _package(inv, None)
        out.effects += files(K.READ, p.positionals, inv)
        return out
    if p.has("r", "remove", "P", "purge", "configure"):  # operands are package names
        return _package(inv, None)
    return Outcome()


def eval_(inv: Invocation) -> Outcome:
    return Outcome(opaque=["eval"])


def source(inv: Invocation) -> Outcome:
    if not inv.args:
        return Outcome()
    out = Outcome(opaque=["source"])
    out.add(file_effect(K.READ, inv.args[0], inv))
    return out


_BUILD_TOOLS = (
    "make", "gmake", "cmake", "ninja", "gradle", "mvn", "ant", "meson", "bazel", "just",
    "pytest", "py.test", "tox", "nox", "jest", "vitest", "mocha", "rspec", "phpunit", "ctest",
    "deno", "tsc", "webpack", "vite", "rake",
)  # fmt: skip

CATALOG: Mapping[str, Handler] = {
    "sudo": privileged,
    "doas": privileged,
    "pkexec": privileged,
    "su": su,
    "time": wrapper(Spec.of("fo", ["format", "output"], stop=True), output=("o", "output")),
    "nice": wrapper(Spec.of("n", ["adjustment"], stop=True)),
    "nohup": wrapper(Spec.of(stop=True)),
    "timeout": wrapper(Spec.of("sk", ["signal", "kill-after"], stop=True), skip=1),
    "stdbuf": wrapper(Spec.of("ioe", ["input", "output", "error"], stop=True)),
    "ionice": wrapper(Spec.of("cnp", ["class", "classdata", "pid"], stop=True)),
    "exec": wrapper(Spec.of("a", stop=True)),
    "command": command,
    "env": env,
    "xargs": xargs,
    "find": find,
    **dict.fromkeys(("bash", "sh", "zsh", "dash", "ksh", "mksh", "ash"), shell),
    "node": interpreter(
        Spec.of("epr", ["eval", "print", "require", "import", "loader"], stop=True),
        ("e", "p", "eval", "print"),
    ),
    "nodejs": interpreter(
        Spec.of("epr", ["eval", "print", "require"], stop=True), ("e", "p", "eval", "print")
    ),
    "ruby": interpreter(Spec.of("eIrC", stop=True), ("e",)),
    "perl": interpreter(
        Spec.of("eEIMm", stop=True), ("e", "E"), in_place=True, info=("v", "V", "h")
    ),
    "php": interpreter(Spec.of("rdcf", stop=True), ("r",)),
    "lua": interpreter(Spec.of("el", stop=True), ("e",), info=("v",)),
    "Rscript": interpreter(Spec.of("e", stop=True), ("e",)),
    "npm": node_package_manager,
    "pnpm": node_package_manager,
    "yarn": node_package_manager,
    "bun": node_package_manager,
    "npx": lambda inv: _package(inv, "registry:npm"),
    "pnpx": lambda inv: _package(inv, "registry:npm"),
    "bunx": lambda inv: _package(inv, "registry:npm"),
    "uvx": lambda inv: _package(inv, "registry:pip"),
    "pip": pip,
    "pip3": pip,
    "uv": uv,
    "cargo": cargo,
    "go": go,
    "docker": container,
    "podman": container,
    **dict.fromkeys(_SYSTEM_PACKAGE_REGISTRY, system_packages),
    "dpkg": dpkg,
    **dict.fromkeys(_BUILD_TOOLS, _build),
    "eval": eval_,
    "source": source,
    ".": source,
}
