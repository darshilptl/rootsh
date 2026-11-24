"""Processes, services, scheduled jobs, and commands with no host effect."""

from __future__ import annotations

from collections.abc import Mapping

from rootsh.analysis.effects.catalog.base import Handler, Invocation, Outcome, effect, files
from rootsh.analysis.effects.model import EffectKind as K
from rootsh.analysis.effects.options import Spec, parse


def _signal(spec: Spec) -> Handler:
    def handler(inv: Invocation) -> Outcome:
        p = parse(inv.args, spec)
        if p.has("l", "L", "list", "table"):
            return Outcome()
        return Outcome(
            [
                effect(K.PROCESS, w.text, inv, detail="signal", dynamic=w.dynamic)
                for w in p.positionals
            ]
        )

    return handler


_UNIT_ACTIONS: Mapping[str, str] = {
    "start": "start", "restart": "start", "reload": "start", "try-restart": "start",
    "reload-or-restart": "start", "try-reload-or-restart": "start", "condrestart": "start",
    "stop": "stop", "kill": "stop",
    "enable": "enable", "reenable": "enable", "link": "enable", "preset": "enable",
    "disable": "disable", "mask": "disable",
}  # fmt: skip
_POWER = frozenset({"poweroff", "reboot", "halt", "kexec", "suspend", "hibernate", "hybrid-sleep"})


def systemctl(inv: Invocation) -> Outcome:
    p = parse(
        inv.args,
        Spec.of("tpHMnos", ["type", "property", "host", "machine", "signal", "lines", "output"]),
    )
    if not p.positionals:
        return Outcome()
    action, units = p.positionals[0].text, p.positionals[1:]
    if action in _POWER:
        return Outcome([effect(K.PROCESS, "system", inv, detail="power")])
    detail = _UNIT_ACTIONS.get(action)
    if detail is None:
        return Outcome()
    return Outcome(
        [effect(K.PROCESS, u.text, inv, detail=detail, dynamic=u.dynamic) for u in units]
    )


def service(inv: Invocation) -> Outcome:
    if len(inv.args) < 2:
        return Outcome()
    unit, action = inv.args[0], inv.args[1].text
    detail = _UNIT_ACTIONS.get(action)
    if detail is None:
        return Outcome()
    return Outcome([effect(K.PROCESS, unit.text, inv, detail=detail, dynamic=unit.dynamic)])


def power(inv: Invocation) -> Outcome:
    p = parse(inv.args, Spec.of())
    if p.has("c", "help", "show"):  # `shutdown -c` cancels a pending shutdown
        return Outcome()
    return Outcome([effect(K.PROCESS, "system", inv, detail="power")])


def crontab(inv: Invocation) -> Outcome:
    p = parse(inv.args, Spec.of("u", ["user"]))
    if p.has("l", "list"):
        return Outcome()
    user = p.value("u", "user")
    name = user.text if user else inv.ctx.env.get("USER")
    target = f"/var/spool/cron/crontabs/{name if name else '$USER'}"
    dynamic = name is None or bool(user and user.dynamic)
    out = Outcome(files(K.READ, p.positionals, inv))
    out.effects.append(effect(K.WRITE, target, inv, detail="config", dynamic=dynamic))
    return out


def at(inv: Invocation) -> Outcome:
    p = parse(inv.args, Spec.of("qfMt"))
    if p.has("l", "d", "r", "c"):  # list / delete / cat jobs
        return Outcome()
    return Outcome([effect(K.WRITE, "/var/spool/cron/atjobs", inv, detail="config")])


def no_effect(inv: Invocation) -> Outcome:
    return Outcome()


# Builtins and utilities that only print or compute. (`cd` is handled by the
# walker because it changes the context, not the host.)
EFFECT_FREE = frozenset(
    {
        "echo", "printf", "pwd", "true", "false", "test", "[", ":", "date", "whoami", "id",
        "uname", "which", "type", "basename", "dirname", "seq", "sleep", "export", "unset",
        "alias", "unalias", "read", "ps", "df", "free", "uptime", "hostname", "printenv",
        "wait", "jobs", "fg", "bg", "hash", "set", "shopt", "history", "clear", "tput", "stty",
        "locale", "nproc", "lscpu", "lsblk", "groups", "logname", "tty", "arch", "getconf",
        "yes", "expr", "let", "declare", "local", "readonly", "typeset", "trap", "umask",
        "ulimit", "return", "exit", "break", "continue", "shift", "getopts", "times",
        "compgen", "complete", "builtin", "caller", "w", "who", "cal", "man", "info", "help",
        "apropos", "whatis", "tldr", "pgrep", "top", "htop", "lsof", "tr",
        "bc", "dc", "factor", "numfmt", "users", "last",
        "lsmod", "lspci", "lsusb", "ss", "netstat", "vmstat",
        "iostat", "dmesg", "pidof", "tabs",
    }
)  # fmt: skip

# --- accounts, file capabilities, mounts (P4 gold traces: injected escalations) ---
# Options from `usermod --help` (shadow-utils) and `mount --help` (util-linux)
# on the host, and setcap's usage line (libcap) [RUN].

_USERMOD = Spec.of(
    "cdefgGlpPRsuvVwWZ",
    ["comment", "home", "expiredate", "inactive", "gid", "groups", "login", "password",
     "prefix", "root", "shell", "uid", "add-subuids", "del-subuids", "add-subgids",
     "del-subgids", "selinux-user", "selinux-range"],
)  # fmt: skip


def usermod(inv: Invocation) -> Outcome:
    """Changes an account's groups, shell, password or UID: a permission
    change on the account itself (target: the login)."""
    p = parse(inv.args, _USERMOD)
    if p.has("h", "help") or not p.positionals:
        return Outcome()
    login = p.positionals[-1]
    return Outcome([effect(K.PERMISSION, login.text, inv, dynamic=login.dynamic)])


_SETCAP_FLAGS = frozenset({"-f", "-q", "-v", "-h", "--license"})


def setcap(inv: Invocation) -> Outcome:
    """`setcap [opts] (caps|-r|-) FILE [(caps|-r|-) FILE ...]`: each FILE's
    capabilities change. `-v` only verifies."""
    args = list(inv.args)
    verify = False
    while args and not args[0].dynamic and args[0].text in _SETCAP_FLAGS | {"-n"}:
        option = args.pop(0).text
        if option in ("-h", "--license"):
            return Outcome()
        verify = verify or option == "-v"
        if option == "-n" and args:
            args.pop(0)
    if verify:
        return Outcome()
    return Outcome(files(K.PERMISSION, args[1::2], inv))


_MOUNT = Spec.of(
    "TOoNtLU",
    ["fstab", "options", "test-opts", "types", "source", "target", "target-prefix",
     "namespace", "label", "uuid", "map-groups", "map-users", "options-mode",
     "options-source"],
)  # fmt: skip


def mount(inv: Invocation) -> Outcome:
    """Listing (`mount`, `mount -l`, `mount -t ext4`) and a dry run (`-f`) have
    no effect; otherwise the mount point's contents change."""
    p = parse(inv.args, _MOUNT)
    if p.has("h", "help", "V", "version", "f", "fake"):
        return Outcome()
    if p.has("a", "all"):
        return Outcome([effect(K.WRITE, "/", inv, detail="config")])
    targets = p.values.get("target") or p.positionals[-1:]
    return Outcome(files(K.WRITE, targets, inv, recursive=True, detail="config"))


CATALOG: Mapping[str, Handler] = {
    "usermod": usermod,
    "setcap": setcap,
    "mount": mount,
    "kill": _signal(Spec.of("sn", ["signal"])),
    "pkill": _signal(Spec.of("sugUGPtF", ["signal", "euid", "uid", "group", "parent", "terminal"])),
    "killall": _signal(Spec.of("suoyn", ["signal", "user", "older-than", "younger-than"])),
    "systemctl": systemctl,
    "service": service,
    **dict.fromkeys(("shutdown", "reboot", "poweroff", "halt"), power),
    "crontab": crontab,
    "at": at,
    "batch": at,
    **dict.fromkeys(EFFECT_FREE, no_effect),
}
