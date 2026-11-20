"""Compilers, linters, docs and infra tools, databases, and name lookups.

These are the programs the P4 gold traces used that the catalog did not know
(results 04 §6). Each handler covers the shapes seen there plus the
destructive forms of the same program; options were checked against each
program's `--help` or manual [DOC].
"""

from __future__ import annotations

import posixpath
import re
from collections.abc import Mapping
from urllib.parse import urlsplit

from rootsh.effects.catalog.base import (
    HERE,
    Handler,
    Invocation,
    Outcome,
    effect,
    file_effect,
    files,
)
from rootsh.effects.model import Effect
from rootsh.effects.model import EffectKind as K
from rootsh.effects.options import Parsed, Spec, parse
from rootsh.effects.words import Word

SX = "script_exec"
PK = "package_install"

# --- C compilers -----------------------------------------------------------------

# gcc/clang take single-dash long options (`-Wall`, `-std=c11`), so GNU option
# parsing would split them into letters; these are the ones whose value is the
# next word.
_CC_VALUE = frozenset(
    {"-o", "-I", "-L", "-D", "-U", "-x", "-MF", "-MT", "-MQ", "-include", "-isystem",
     "-iquote", "-idirafter", "-imacros", "-Xlinker", "-Xassembler", "-Xpreprocessor"}
)  # fmt: skip
_CC_NO_OUTPUT = frozenset({"-E", "-M", "-MM", "-fsyntax-only", "--version", "-v", "--help"})


def compiler(inv: Invocation) -> Outcome:
    """Reads its sources and writes one output file; it does not run them."""
    output: Word | None = None
    sources: list[Word] = []
    flags: set[str] = set()
    args = list(inv.args)
    i = 0
    while i < len(args):
        word = args[i]
        text = word.text
        if word.dynamic or not text.startswith("-") or text == "-":
            if text != "-":
                sources.append(word)
        elif text in _CC_VALUE:
            if text == "-o" and i + 1 < len(args):
                output = args[i + 1]
            i += 1
        elif text.startswith("-o") and len(text) > 2:
            output = Word(text[2:], dynamic=word.dynamic, glob=word.glob)
        else:
            flags.add(text)
        i += 1
    out = Outcome(files(K.READ, sources, inv))
    if output is not None:
        out.add(file_effect(K.WRITE, output, inv, detail="create"))
    elif not flags & _CC_NO_OUTPUT and sources:
        if "-c" in flags or "-S" in flags:
            suffix = ".s" if "-S" in flags else ".o"
            for source in sources:
                stem = posixpath.splitext(posixpath.basename(source.text))[0]
                out.add(file_effect(K.WRITE, Word(stem + suffix), inv, detail="create"))
        else:
            out.add(file_effect(K.WRITE, Word("a.out"), inv, detail="create"))
    return out


# --- SQLite ------------------------------------------------------------------------

_SQLITE_VALUE = frozenset(
    {"-cmd", "-init", "-separator", "-newline", "-nullvalue", "-vfs", "-mmap", "-maxsize",
     "-pagecache", "-lookaside", "-heap"}
)  # fmt: skip
_SQLITE_READ_DOTS = frozenset(
    {".schema", ".tables", ".dump", ".indexes", ".indices", ".headers", ".mode", ".databases",
     ".show", ".help", ".fullschema", ".dbinfo", ".width", ".timer", ".stats", ".eqp"}
)  # fmt: skip
_SQL_READ = re.compile(r"^\s*(?:select|explain|values)\b", re.IGNORECASE)
# `.shell`/`.system` run commands, `load_extension` loads native code.
_SQL_CODE = re.compile(r"(?:^|\s)\.(?:shell|system)\b|load_extension", re.IGNORECASE)


def _sql_read_only(sql: str) -> bool:
    statements = [s for s in re.split(r";", sql) if s.strip()]
    for statement in statements:
        first = statement.strip().split(None, 1)[0].lower()
        if first.startswith("."):
            if first not in _SQLITE_READ_DOTS:
                return False
        elif not _SQL_READ.match(statement):
            return False
    return bool(statements)


def sqlite3(inv: Invocation) -> Outcome:
    """`sqlite3 [options] [DB [SQL ...]]`. With no SQL operand it reads SQL
    from stdin, which may write and, through `.shell`, run commands."""
    readonly = False
    commands: list[Word] = []
    init: Word | None = None
    operands: list[Word] = []
    args = list(inv.args)
    i = 0
    while i < len(args):
        word = args[i]
        if not word.dynamic and word.text.startswith("-") and not operands:
            option = "-" + word.text.lstrip("-")
            if option in _SQLITE_VALUE and i + 1 < len(args):
                if option == "-cmd":
                    commands.append(args[i + 1])
                elif option == "-init":
                    init = args[i + 1]
                i += 1
            elif option in ("-readonly", "-safe"):
                readonly = True
            elif option in ("-version", "-help"):
                return Outcome()
        else:
            operands.append(word)
        i += 1
    db, sql = (operands[0], operands[1:]) if operands else (None, [])
    out = Outcome()
    if init is not None:
        out.add(file_effect(K.READ, init, inv))
        out.opaque.append(SX)
    texts = [w.text for w in [*commands, *sql]]
    if any(w.dynamic for w in [*commands, *sql]) or any(_SQL_CODE.search(t) for t in texts):
        out.opaque.append("interpreter_code")
    in_memory = db is None or (not db.dynamic and db.text in ("", ":memory:"))
    if not sql:
        if db is not None and not in_memory:
            out.add(file_effect(K.WRITE, db, inv, detail="modify"))
        out.opaque.append("stdin_code")
        return out
    if db is None or in_memory:
        return out
    writes = not readonly and not all(_sql_read_only(t) for t in texts)
    kind, detail = (K.WRITE, "modify") if writes else (K.READ, "")
    out.add(file_effect(kind, db, inv, detail=detail))
    return out


# --- PostgreSQL clients ----------------------------------------------------------------


def _pg_target(uri: str | None, host: str | None, port: str | None, dbname: str | None) -> str:
    """`postgres://host:port/db` without credentials; host defaults to the
    local socket, reported as localhost."""
    if uri is not None:
        parts = urlsplit(uri)
        netloc = parts.hostname or "localhost"
        if parts.port is not None:
            netloc = f"{netloc}:{parts.port}"
        return f"postgres://{netloc}{parts.path}"
    target = f"postgres://{host or 'localhost'}:{port or '5432'}"
    return f"{target}/{dbname}" if dbname else target


_PSQL = Spec.of("cdfhpUvoLPTFR", ["command", "dbname", "file", "host", "port", "username",
                                  "set", "variable", "output", "log-file", "pset"])  # fmt: skip
_PG_ISREADY = Spec.of("dhpUt", ["dbname", "host", "port", "username", "timeout"])


def _pg_connect(inv: Invocation, p: Parsed, uri: str | None) -> Effect:
    host, port, db = p.value("h", "host"), p.value("p", "port"), p.value("d", "dbname")
    if uri is None and db is not None and "://" in db.text:
        uri = db.text
    target = _pg_target(
        uri,
        host.text if host else None,
        port.text if port else None,
        db.text if db else None,
    )
    return effect(K.NETWORK, target, inv, detail="connect")


def psql(inv: Invocation) -> Outcome:
    """Connects to a server; any SQL other than a plain query, a script, or
    stdin may change the database, and `\\!` runs shell commands."""
    p = parse(inv.args, _PSQL)
    if p.has("V", "version", "help"):
        return Outcome()
    uri = next((w.text for w in p.positionals if "://" in w.text), None)
    connect = _pg_connect(inv, p, uri)
    out = Outcome([connect])
    if p.has("l", "list"):
        return out
    commands = p.all_values("c", "command")
    script = p.value("f", "file")
    if script is not None:
        out.add(file_effect(K.READ, script, inv))
    read_only = (
        script is None
        and bool(commands)
        and all(not w.dynamic and _SQL_READ.match(w.text) and "\\!" not in w.text for w in commands)
    )
    if not read_only:
        out.effects.append(effect(K.EXEC, connect.target, inv, detail="remote"))
        out.opaque.append("remote_exec")
    output = p.value("o", "output")
    if output is not None:
        out.add(file_effect(K.WRITE, output, inv, detail="create"))
    return out


def pg_isready(inv: Invocation) -> Outcome:
    p = parse(inv.args, _PG_ISREADY)
    if p.has("V", "version", "help"):
        return Outcome()
    return Outcome([_pg_connect(inv, p, None)])


# --- docs, lint, infra ------------------------------------------------------------------


def _build(inv: Invocation) -> Outcome:
    return Outcome([effect(K.EXEC, inv.program, inv, detail="build")], opaque=[SX])


def mkdocs(inv: Invocation) -> Outcome:
    """`build`/`serve` load the project's plugins and hooks (Python): a build.
    `gh-deploy` also pushes the site."""
    p = parse(inv.args, Spec.of("fdar", ["config-file", "site-dir", "dev-addr", "remote-branch",
                                         "remote-name", "message", "theme"]))  # fmt: skip
    sub = p.positionals[0].text if p.positionals else None
    if sub not in ("build", "serve", "gh-deploy"):
        return Outcome()
    out = _build(inv)
    site = p.value("d", "site-dir")
    if sub != "serve":
        out.add(file_effect(K.WRITE, site or Word("site"), inv, recursive=True, detail="create"))
    if sub == "gh-deploy":
        out.effects.append(effect(K.NETWORK, "upstream", inv, detail="push", dynamic=True))
    return out


_RUFF = Spec.of("", ["config", "select", "ignore", "extend-select", "line-length",
                     "target-version", "output-format", "exclude", "extend-exclude",
                     "per-file-ignores", "cache-dir", "stdin-filename"])  # fmt: skip


def ruff(inv: Invocation) -> Outcome:
    """`check` reads; `check --fix` and `format` (without --check/--diff)
    rewrite files in place."""
    p = parse(inv.args, _RUFF)
    if not p.positionals:
        return Outcome()
    sub, paths = p.positionals[0].text, p.positionals[1:] or [HERE]
    if sub == "check":
        writes = p.has("fix", "unsafe-fixes") and not p.has("diff", "no-fix")
    elif sub == "format":
        writes = not p.has("check", "diff")
    else:
        return Outcome()
    kind, detail = (K.WRITE, "modify") if writes else (K.READ, "")
    return Outcome(files(kind, paths, inv, recursive=True, detail=detail))


_TF_READ = frozenset({"validate", "show", "output", "graph", "providers", "state-list", "console"})
_TF_REMOTE = frozenset({"plan", "apply", "destroy", "import", "refresh", "taint", "untaint"})


def terraform(inv: Invocation) -> Outcome:
    """`plan`/`apply`/`destroy` drive cloud APIs through provider plugins;
    `init` downloads them."""
    words = [w.text for w in inv.args if not w.text.startswith("-")]
    sub = words[0] if words else None
    if sub is None or sub in ("version", "help"):
        return Outcome()
    if sub == "init":
        return Outcome(
            [
                effect(K.EXEC, "terraform", inv, detail="package-install"),
                effect(K.NETWORK, "registry:terraform", inv, detail="fetch"),
            ],
            opaque=[PK],
        )
    if sub == "fmt":
        check = any(w.text in ("-check", "--check", "-diff") for w in inv.args)
        kind, detail = (K.READ, "") if check else (K.WRITE, "modify")
        return Outcome(files(kind, [HERE], inv, recursive=True, detail=detail))
    if sub in _TF_READ:
        return Outcome(files(K.READ, [HERE], inv, recursive=True))
    if sub in _TF_REMOTE:
        return Outcome(
            [effect(K.EXEC, f"terraform-{sub}", inv, detail="remote")], opaque=["remote_exec"]
        )
    return Outcome(
        [effect(K.EXEC, f"terraform-{sub}", inv)], opaque=[f"unknown_program:terraform-{sub}"]
    )


def pipx(inv: Invocation) -> Outcome:
    p = parse(inv.args, Spec.of("", ["python", "spec", "index-url", "pip-args", "suffix"]))
    sub = p.positionals[0].text if p.positionals else None
    if sub in ("install", "upgrade", "upgrade-all", "inject", "run", "reinstall", "reinstall-all"):
        return Outcome(
            [
                effect(K.EXEC, "pipx", inv, detail="package-install"),
                effect(K.NETWORK, "registry:pip", inv, detail="fetch"),
            ],
            opaque=[PK],
        )
    if sub in ("uninstall", "uninstall-all", "uninject"):
        return Outcome([effect(K.EXEC, "pipx", inv, detail="package-install")], opaque=[PK])
    return Outcome()


# --- services, keys, name lookups ---------------------------------------------------


def nginx(inv: Invocation) -> Outcome:
    p = parse(inv.args, Spec.of("cgpes"))
    if p.has("v", "V", "h", "?"):
        return Outcome()
    config = p.value("c") or Word("/etc/nginx/nginx.conf")
    if p.has("t", "T"):
        return Outcome(files(K.READ, [config], inv))
    signal = p.value("s")
    if signal is not None:
        return Outcome([effect(K.PROCESS, "nginx", inv, detail="signal", dynamic=signal.dynamic)])
    out = Outcome(files(K.READ, [config], inv))
    out.effects.append(effect(K.PROCESS, "nginx", inv, detail="start"))
    return out


_GPG_LIST = frozenset(
    {"--list-keys", "--list-public-keys", "--list-secret-keys", "--fingerprint",
     "--list-sigs", "--check-sigs", "-k", "-K", "--version", "--help", "--list-packets"}
)  # fmt: skip
_GPG_CHANGE = frozenset(
    {"--import", "--delete-key", "--delete-keys", "--delete-secret-key", "--delete-secret-keys",
     "--delete-secret-and-public-key", "--gen-key", "--generate-key", "--full-generate-key",
     "--full-gen-key", "--edit-key", "--sign-key", "--lsign-key", "--quick-generate-key",
     "--quick-gen-key", "--quick-add-key", "--quick-add-uid", "--quick-revoke-uid",
     "--quick-set-expire", "--recv-keys", "--refresh-keys", "--import-ownertrust",
     "--update-trustdb", "--gen-revoke", "--generate-revocation"}
)  # fmt: skip


_GPG = Spec.of(
    "oruf",
    ["output", "recipient", "local-user", "homedir", "default-key", "passphrase",
     "passphrase-file", "keyserver", "trust-model", "cipher-algo", "digest-algo",
     "compress-algo", "status-fd", "logger-fd", "pinentry-mode"],
)  # fmt: skip


def gpg(inv: Invocation) -> Outcome:
    """Every operation reads the keyring (secret keys included for
    `--list-secret-keys`, signing and decryption); key management writes it."""
    texts = {w.text for w in inv.args if not w.dynamic}
    if texts & {"--version", "--help", "-h"}:
        return Outcome()
    home_word = inv.ctx.env.get("GNUPGHOME")
    home = Word(home_word) if home_word else Word(posixpath.join(inv.ctx.home, ".gnupg"))
    if texts & _GPG_CHANGE:
        out = Outcome(files(K.WRITE, [home], inv, recursive=True, detail="config"))
        if texts & {"--recv-keys", "--refresh-keys"}:
            out.effects.append(effect(K.NETWORK, "keyserver", inv, detail="fetch", dynamic=True))
        return out
    out = Outcome(files(K.READ, [home], inv, recursive=True))
    if texts & _GPG_LIST:
        return out
    p = parse(inv.args, _GPG)
    output = p.value("o", "output")
    if output is not None:
        out.add(file_effect(K.WRITE, output, inv, detail="create"))
    out.effects += files(K.READ, p.positionals, inv)
    return out


_GETENT_FILES: Mapping[str, str] = {
    "passwd": "/etc/passwd", "group": "/etc/group", "shadow": "/etc/shadow",
    "gshadow": "/etc/gshadow", "services": "/etc/services", "protocols": "/etc/protocols",
    "networks": "/etc/networks", "ethers": "/etc/ethers", "rpc": "/etc/rpc",
    "aliases": "/etc/aliases", "netgroup": "/etc/netgroup", "initgroups": "/etc/group",
}  # fmt: skip
_GETENT_HOSTS = frozenset({"hosts", "ahosts", "ahostsv4", "ahostsv6"})


def getent(inv: Invocation) -> Outcome:
    """Host lookups may query DNS, which leaves the host (as `dig` does);
    other databases are local files."""
    p = parse(inv.args, Spec.of("s", ["service"]))
    if not p.positionals:
        return Outcome()
    database, keys = p.positionals[0].text, p.positionals[1:]
    if database in _GETENT_HOSTS:
        out = Outcome(files(K.READ, [Word("/etc/hosts")], inv))
        out.effects += [
            effect(K.NETWORK, key.text, inv, detail="connect", dynamic=key.dynamic) for key in keys
        ]
        return out
    path = _GETENT_FILES.get(database)
    return Outcome(files(K.READ, [Word(path)], inv)) if path else Outcome()


def apt_cache(inv: Invocation) -> Outcome:
    """Queries the local package cache; only `gencache` writes it."""
    if any(w.text == "gencache" for w in inv.args):
        cache = Word("/var/cache/apt")
        return Outcome(files(K.WRITE, [cache], inv, recursive=True, detail="modify"))
    return Outcome()


CATALOG: Mapping[str, Handler] = {
    **dict.fromkeys(("cc", "gcc", "clang", "c++", "g++", "clang++", "tcc"), compiler),
    "sqlite3": sqlite3,
    "psql": psql,
    "pg_isready": pg_isready,
    "mkdocs": mkdocs,
    "ruff": ruff,
    "terraform": terraform,
    "pipx": pipx,
    "nginx": nginx,
    "gpg": gpg,
    "gpg2": gpg,
    "getent": getent,
    "apt-cache": apt_cache,
}
