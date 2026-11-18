"""Network commands: transfers, remote shells, raw connections, lookups."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace

from rootsh.effects.catalog.base import (
    Handler,
    Invocation,
    Outcome,
    effect,
    file_effect,
    files,
    host_of,
    remote_host,
    url_basename,
)
from rootsh.effects.model import EffectKind as K
from rootsh.effects.options import Spec, parse
from rootsh.effects.words import Word

_CURL = Spec.of(
    "odFTXHuAebcDKmwxYyzECrUQ",
    [
        "output", "data", "data-binary", "data-raw", "data-ascii", "data-urlencode", "json",
        "form", "form-string", "upload-file", "request", "header", "user", "user-agent",
        "referer", "cookie", "cookie-jar", "url", "max-time", "connect-timeout", "proxy",
        "cacert", "cert", "key", "config", "write-out", "range", "retry", "output-dir",
    ],
)  # fmt: skip
_CURL_DATA = ("d", "data", "data-binary", "data-ascii", "data-urlencode", "json")
_CURL_SENDS = (*_CURL_DATA, "data-raw", "F", "form", "form-string", "T", "upload-file")


def _at_file(value: Word) -> Word | None:
    """`@file` (curl data) or `name=@file` / `name=<file` (curl form)."""
    text = value.text
    for marker in ("=@", "=<"):
        if marker in text:
            text = text.split(marker, 1)[1]
            break
    else:
        if not text.startswith("@"):
            return None
        text = text[1:]
    text = text.split(";", 1)[0]
    return None if text in ("", "-") else Word(text, dynamic=value.dynamic)


def curl(inv: Invocation) -> Outcome:
    p = parse(inv.args, _CURL)
    out = Outcome()
    for value in p.all_values(*_CURL_DATA, "F", "form"):
        if (source := _at_file(value)) is not None:
            out.add(file_effect(K.READ, source, inv))
    for upload in p.all_values("T", "upload-file"):
        if upload.text != "-":
            out.add(file_effect(K.READ, upload, inv))
    detail = "upload" if p.has(*_CURL_SENDS) else "fetch"
    urls = [*p.all_values("url"), *p.positionals]
    for url in urls:
        out.add(effect(K.NETWORK, url.text, inv, detail=detail, dynamic=url.dynamic))
    output = p.value("o", "output")
    if output is not None and output.text != "-":
        out.add(file_effect(K.WRITE, output, inv, detail="truncate"))
    elif p.has("O", "remote-name"):
        for url in urls:
            out.add(
                file_effect(
                    K.WRITE,
                    Word(url_basename(url.text), dynamic=url.dynamic),
                    inv,
                    detail="truncate",
                )
            )
    return out


_WGET = Spec.of(
    "OoPaetTUQ",
    [
        "output-document", "output-file", "directory-prefix", "post-data", "post-file",
        "body-data", "body-file", "header", "user", "password", "tries", "timeout",
        "user-agent", "method",
    ],
)  # fmt: skip


def wget(inv: Invocation) -> Outcome:
    p = parse(inv.args, _WGET)
    out = Outcome()
    for source in p.all_values("post-file", "body-file"):
        out.add(file_effect(K.READ, source, inv))
    sends = p.has("post-file", "body-file", "post-data", "body-data")
    document = p.value("O", "output-document")
    prefix = p.value("P", "directory-prefix")
    for url in p.positionals:
        out.add(
            effect(
                K.NETWORK, url.text, inv, detail="upload" if sends else "fetch", dynamic=url.dynamic
            )
        )
        if document is None:
            name = url_basename(url.text)
            local = Word(f"{prefix.text}/{name}" if prefix else name, dynamic=url.dynamic)
            out.add(file_effect(K.WRITE, local, inv, detail="truncate"))
    if document is not None and document.text != "-":
        out.add(file_effect(K.WRITE, document, inv, detail="truncate"))
    return out


def _transfer(inv: Invocation, operands: list[Word], *, recursive: bool) -> Outcome:
    """scp / rsync: direction from which side names a remote host."""
    if len(operands) < 2:
        return Outcome()
    sources, dest = operands[:-1], operands[-1]
    out = Outcome()
    dest_host = remote_host(dest)
    if dest_host is not None:
        out.effects += files(
            K.READ, [s for s in sources if remote_host(s) is None], inv, recursive=recursive
        )
        out.add(effect(K.NETWORK, host_of(dest_host), inv, detail="upload"))
        return out
    remote_sources = [h for s in sources if (h := remote_host(s)) is not None]
    for host in dict.fromkeys(remote_sources):
        out.add(effect(K.NETWORK, host_of(host), inv, detail="fetch"))
    out.effects += files(
        K.READ, [s for s in sources if remote_host(s) is None], inv, recursive=recursive
    )
    out.add(file_effect(K.WRITE, dest, inv, recursive=recursive, detail="truncate"))
    return out


def scp(inv: Invocation) -> Outcome:
    p = parse(inv.args, Spec.of("PiocFlSJ"))
    return _transfer(inv, p.positionals, recursive=p.has("r"))


_RSYNC = Spec.of(
    "eTf",
    ["rsh", "exclude", "include", "filter", "rsync-path", "temp-dir", "exclude-from",
     "include-from", "files-from", "backup-dir", "suffix", "chmod", "chown", "max-size"],
)  # fmt: skip


def rsync(inv: Invocation) -> Outcome:
    p = parse(inv.args, _RSYNC)
    recursive = p.has("r", "a", "recursive", "archive")
    out = _transfer(inv, p.positionals, recursive=recursive)
    deletes = any(flag.startswith("delete") for flag in p.flags) or any(
        name.startswith("delete") for name in p.values
    )
    if deletes and p.positionals and remote_host(p.positionals[-1]) is None:
        dest = file_effect(K.DELETE, p.positionals[-1], inv, recursive=True)
        if dest is not None:  # which files go depends on the destination's contents
            out.effects.append(replace(dest, dynamic=True))
    return out


_SSH = Spec.of("bcDEeFIiJLlmOopQRSWw", stop=True)


def ssh(inv: Invocation) -> Outcome:
    p = parse(inv.args, _SSH)
    if not p.positionals:
        return Outcome()
    destination = p.positionals[0]
    host = host_of(destination.text)
    out = Outcome(opaque=["remote_exec"])
    out.add(effect(K.NETWORK, host, inv, detail="connect", dynamic=destination.dynamic))
    out.add(effect(K.EXEC, host, inv, detail="remote", dynamic=destination.dynamic))
    return out


def sftp(inv: Invocation) -> Outcome:
    p = parse(inv.args, Spec.of("bBcDFiloPRsS", stop=True))
    if not p.positionals:
        return Outcome()
    return Outcome([effect(K.NETWORK, host_of(p.positionals[0].text), inv, detail="connect")])


def netcat(inv: Invocation) -> Outcome:
    p = parse(inv.args, Spec.of("psiqwxXecIOT"))
    words = [w.text for w in p.positionals]
    if p.has("l"):  # a listening socket: the classic bind shell or inbound channel
        port = p.value("p")
        listen = port.text if port is not None else (words[-1] if words else "")
        return Outcome([effect(K.NETWORK, f":{listen}", inv, detail="listen", dynamic=not listen)])
    if not words:
        return Outcome()
    target = f"{words[0]}:{words[1]}" if len(words) > 1 else words[0]
    dynamic = any(w.dynamic for w in p.positionals[:2])
    return Outcome([effect(K.NETWORK, target, inv, detail="connect", dynamic=dynamic)])


def socat(inv: Invocation) -> Outcome:
    out = Outcome()
    for word in inv.args:
        kind, _, rest = word.text.partition(":")
        if kind.upper().startswith(("TCP", "UDP", "OPENSSL", "SCTP")) and rest:
            address = rest.split(",", 1)[0]
            listening = "LISTEN" in kind.upper()
            out.add(
                effect(
                    K.NETWORK,
                    f":{address}" if listening else address,
                    inv,
                    detail="listen" if listening else "connect",
                    dynamic=word.dynamic,
                )
            )
    return out


_DNS_TYPES = frozenset({"A", "AAAA", "MX", "TXT", "NS", "ANY", "CNAME", "SOA", "PTR", "SRV", "CAA"})


def lookup(inv: Invocation) -> Outcome:
    """ping, dig, host, nslookup, telnet, ftp: contact the first named host."""
    p = parse(inv.args, Spec.of("ciWwstIpbqfx"))
    hosts = [
        w
        for w in p.positionals
        if not w.text.startswith(("@", "+")) and w.text.upper() not in _DNS_TYPES
    ]
    if not hosts:
        return Outcome()
    target = hosts[0].text
    if inv.program == "telnet" and len(hosts) > 1:
        target = f"{target}:{hosts[1].text}"
    return Outcome([effect(K.NETWORK, target, inv, detail="connect", dynamic=hosts[0].dynamic)])


CATALOG: Mapping[str, Handler] = {
    "curl": curl,
    "wget": wget,
    "scp": scp,
    "rsync": rsync,
    "ssh": ssh,
    "sftp": sftp,
    "nc": netcat,
    "ncat": netcat,
    "netcat": netcat,
    "socat": socat,
    **dict.fromkeys(
        ("ping", "ping6", "dig", "host", "nslookup", "telnet", "ftp", "traceroute"), lookup
    ),
}
