"""Processes, services, scheduled jobs, and commands with no host effect."""

from __future__ import annotations

from collections.abc import Mapping

from rootsh.effects.catalog.base import Handler, Invocation, Outcome, effect, files
from rootsh.effects.model import EffectKind as K
from rootsh.effects.options import Spec, parse


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

CATALOG: Mapping[str, Handler] = {
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
