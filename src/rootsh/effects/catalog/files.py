"""Filesystem commands: delete, write, copy/move, archives, permissions, reads."""

from __future__ import annotations

import re
from collections.abc import Mapping

from rootsh.effects.catalog.base import (
    HERE,
    Handler,
    Invocation,
    Outcome,
    file_effect,
    files,
    path_of,
    strip_suffix,
    with_suffix,
)
from rootsh.effects.model import EffectKind as K
from rootsh.effects.options import Spec, parse
from rootsh.effects.words import Word

IC = "interpreter_code"


def _recursive(parsed_flags: set[str]) -> bool:
    return bool(parsed_flags & {"r", "R", "recursive", "a", "archive"})


# --- delete ----------------------------------------------------------------


def rm(inv: Invocation) -> Outcome:
    p = parse(inv.args, Spec.of())
    return Outcome(files(K.DELETE, p.positionals, inv, recursive=_recursive(p.flags)))


def plain_delete(inv: Invocation) -> Outcome:
    p = parse(inv.args, Spec.of("m", ["mode"]))
    return Outcome(files(K.DELETE, p.positionals, inv))


def shred(inv: Invocation) -> Outcome:
    p = parse(inv.args, Spec.of("ns", ["iterations", "size", "random-source"]))
    if p.has("u", "remove"):
        return Outcome(files(K.DELETE, p.positionals, inv))
    return Outcome(files(K.WRITE, p.positionals, inv, detail="truncate"))


# --- write -----------------------------------------------------------------


def touch(inv: Invocation) -> Outcome:
    p = parse(inv.args, Spec.of("drt", ["date", "reference"]))
    return Outcome(files(K.WRITE, p.positionals, inv, detail="create"))


def mkdir(inv: Invocation) -> Outcome:
    p = parse(inv.args, Spec.of("m", ["mode", "context"]))
    return Outcome(files(K.WRITE, p.positionals, inv, detail="create"))


def ln(inv: Invocation) -> Outcome:
    p = parse(inv.args, Spec.of("St", ["suffix", "target-directory"]))
    detail = "truncate" if p.has("f", "force") else "create"
    if (directory := p.value("t", "target-directory")) is not None:
        return Outcome(files(K.WRITE, [directory], inv, detail=detail))
    if not p.positionals:
        return Outcome()
    link = p.positionals[-1] if len(p.positionals) > 1 else _basename_here(p.positionals[0])
    return Outcome(files(K.WRITE, [link], inv, detail=detail))


def _basename_here(word: Word) -> Word:
    return Word(word.text.rstrip("/").rsplit("/", 1)[-1], dynamic=word.dynamic)


def tee(inv: Invocation) -> Outcome:
    p = parse(inv.args, Spec.of())
    detail = "append" if p.has("a", "append") else "truncate"
    return Outcome(files(K.WRITE, p.positionals, inv, detail=detail))


def truncate(inv: Invocation) -> Outcome:
    p = parse(inv.args, Spec.of("sr", ["size", "reference"]))
    return Outcome(files(K.WRITE, p.positionals, inv, detail="truncate"))


def install(inv: Invocation) -> Outcome:
    p = parse(inv.args, Spec.of("mogtS", ["mode", "owner", "group", "target-directory", "suffix"]))
    if p.has("d", "directory"):
        return Outcome(files(K.WRITE, p.positionals, inv, detail="create"))
    return _copy_like(inv, p.positionals, p.value("t", "target-directory"), recursive=False)


def dd(inv: Invocation) -> Outcome:
    out = Outcome()
    for word in inv.args:
        key, eq, value = word.text.partition("=")
        if not eq:
            continue
        operand = Word(value, dynamic=word.dynamic)
        if key == "if":
            out.add(file_effect(K.READ, operand, inv))
        elif key == "of":
            out.add(file_effect(K.WRITE, operand, inv, detail="truncate"))
    return out


def format_device(inv: Invocation) -> Outcome:
    p = parse(inv.args, Spec.of("bcCEgGiILmMNOrtTU", ["type", "label"]))
    devices = [w for w in p.positionals if w.text.startswith("/dev/")] or p.positionals[:1]
    return Outcome(files(K.WRITE, devices, inv, detail="truncate"))


_SED_EXEC_OR_WRITE = (
    # an address (optional) followed by the `w` or `e` command
    re.compile(
        r"(?:^|[;\n{}])\s*(?:\d+|\$|/[^/]*/)?(?:,\s*(?:\d+|\$|/[^/]*/))?\s*!?\s*[we](?:\s|$)"
    ),
    # `s///` with the `w` or `e` flag
    re.compile(r"s(.)(?:(?!\1).)*\1(?:(?!\1).)*\1[gpiImM0-9]*[we]"),
)


def sed(inv: Invocation) -> Outcome:
    p = parse(inv.args, Spec.of("efl", ["expression", "file", "line-length"]))
    scripts = p.all_values("e", "expression")
    operands = p.positionals
    if not scripts and not p.has("f", "file"):
        scripts, operands = operands[:1], operands[1:]
    out = Outcome(files(K.READ, p.all_values("f", "file"), inv))
    if p.has("i", "in-place"):
        out.effects += files(K.WRITE, operands, inv, detail="modify")
    else:
        out.effects += files(K.READ, operands, inv)
    if any(w.dynamic or any(rx.search(w.text) for rx in _SED_EXEC_OR_WRITE) for w in scripts):
        out.opaque.append(IC)
    return out


# --- copy / move ---------------------------------------------------------------


def _copy_like(
    inv: Invocation, operands: list[Word], target_dir: Word | None, *, recursive: bool
) -> Outcome:
    if target_dir is not None:
        sources, dest = operands, target_dir
    elif len(operands) >= 2:
        sources, dest = operands[:-1], operands[-1]
    else:
        return Outcome()
    out = Outcome(files(K.READ, sources, inv, recursive=recursive))
    out.add(file_effect(K.WRITE, dest, inv, recursive=recursive, detail="truncate"))
    return out


def cp(inv: Invocation) -> Outcome:
    p = parse(inv.args, Spec.of("tS", ["target-directory", "suffix", "backup"]))
    return _copy_like(
        inv, p.positionals, p.value("t", "target-directory"), recursive=_recursive(p.flags)
    )


def mv(inv: Invocation) -> Outcome:
    p = parse(inv.args, Spec.of("tS", ["target-directory", "suffix", "backup"]))
    target_dir = p.value("t", "target-directory")
    if target_dir is not None:
        sources, dest = p.positionals, target_dir
    elif len(p.positionals) >= 2:
        sources, dest = p.positionals[:-1], p.positionals[-1]
    else:
        return Outcome()
    destination = path_of(dest, inv)
    out = Outcome()
    for source in sources:
        out.add(file_effect(K.MOVE, source, inv, dest=destination))
    return out


# --- archives ---------------------------------------------------------------------

_TAR_SPEC = Spec.of("fCTXbHLN", ["file", "directory", "files-from", "exclude", "exclude-from"])


def tar(inv: Invocation) -> Outcome:
    args = list(inv.args)
    if args and not args[0].dynamic and not args[0].text.startswith("-"):
        args[0] = Word("-" + args[0].text)  # old-style bundled first argument: `tar czf`
    p = parse(args, _TAR_SPEC)
    archive = p.value("f", "file")
    archive = None if archive is not None and archive.text == "-" else archive
    out = Outcome()
    if p.has("c", "create", "r", "append", "u", "update"):
        if archive is not None:
            out.add(file_effect(K.WRITE, archive, inv, detail="truncate"))
        out.effects += files(K.READ, p.positionals, inv, recursive=True)
    elif p.has("x", "extract", "get"):
        if archive is not None:
            out.add(file_effect(K.READ, archive, inv))
        dest = p.value("C", "directory") or HERE
        out.add(file_effect(K.WRITE, dest, inv, recursive=True, detail="truncate"))
    elif archive is not None:  # list / diff
        out.add(file_effect(K.READ, archive, inv))
    return out


def zip_(inv: Invocation) -> Outcome:
    p = parse(inv.args, Spec.of("xibntP", ["exclude", "include"]))
    if not p.positionals:
        return Outcome()
    archive, members = p.positionals[0], p.positionals[1:]
    out = Outcome()
    out.add(file_effect(K.WRITE, archive, inv, detail="truncate"))
    out.effects += files(K.READ, members, inv, recursive=_recursive(p.flags))
    return out


def unzip(inv: Invocation) -> Outcome:
    p = parse(inv.args, Spec.of("dxP"))
    if not p.positionals:
        return Outcome()
    out = Outcome()
    out.add(file_effect(K.READ, p.positionals[0], inv))
    if not p.has("l", "t", "v", "p"):
        dest = p.value("d") or HERE
        out.add(file_effect(K.WRITE, dest, inv, recursive=True, detail="truncate"))
    return out


_COMPRESS_EXT: Mapping[str, str] = {
    "gzip": ".gz", "gunzip": ".gz", "bzip2": ".bz2", "bunzip2": ".bz2",
    "xz": ".xz", "unxz": ".xz", "zstd": ".zst", "unzstd": ".zst",
}  # fmt: skip
_DECOMPRESSORS = frozenset({"gunzip", "bunzip2", "unxz", "unzstd"})


def compress(inv: Invocation) -> Outcome:
    p = parse(inv.args, Spec.of("S", ["suffix"]))
    ext = _COMPRESS_EXT[inv.program]
    keep, to_stdout = p.has("k", "keep"), p.has("c", "stdout", "to-stdout")
    decompress = inv.program in _DECOMPRESSORS or p.has("d", "decompress", "uncompress")
    out = Outcome()
    for source in p.positionals:
        if to_stdout or p.has("t", "test", "l", "list"):
            out.add(file_effect(K.READ, source, inv))
            continue
        result = strip_suffix(source, ext) if decompress else with_suffix(source, ext)
        if keep:
            out.add(file_effect(K.READ, source, inv))
        else:
            out.add(file_effect(K.DELETE, source, inv))
        out.add(file_effect(K.WRITE, result, inv, detail="truncate"))
    return out


# --- permissions ---------------------------------------------------------------------


# A symbolic or octal mode such as `-x`, `u+w`, `go-rwx,a+X` or `0755`. Checked
# before option parsing so `chmod -x file` is not read as an unknown flag `-x`.
_MODE = re.compile(r"(?:[ugoa]*[-+=][rwxXstugo]*)(?:,[ugoa]*[-+=][rwxXstugo]*)*|[0-7]{1,4}")


def chmod_like(inv: Invocation) -> Outcome:
    args = list(inv.args)
    mode_removed = False
    if inv.program == "chmod":
        index = next(
            (i for i, w in enumerate(args) if not w.dynamic and _MODE.fullmatch(w.text)), None
        )
        if index is not None:
            del args[index]
            mode_removed = True
    p = parse(args, Spec.of("", ["reference", "from"]))
    first_is_mode_or_owner = not mode_removed and not p.has("reference")
    targets = p.positionals[1:] if first_is_mode_or_owner else p.positionals
    return Outcome(files(K.PERMISSION, targets, inv, recursive=_recursive(p.flags)))


def chattr(inv: Invocation) -> Outcome:
    p = parse(inv.args, Spec.of("vpP"))
    targets = [w for w in p.positionals if not w.text.startswith(("+", "="))]
    return Outcome(files(K.PERMISSION, targets, inv, recursive=p.has("R")))


def setfacl(inv: Invocation) -> Outcome:
    p = parse(inv.args, Spec.of("mMxX", ["modify", "modify-file", "remove", "remove-file"]))
    return Outcome(files(K.PERMISSION, p.positionals, inv, recursive=p.has("R", "recursive")))


# --- read-only ---------------------------------------------------------------------


_NO_OPTIONS = Spec.of()


def reader(
    spec: Spec = _NO_OPTIONS,
    *,
    recursive: bool = False,
    default_cwd: bool = False,
) -> Handler:
    """Reads its operands; with `default_cwd`, no operands means the working directory."""

    def handler(inv: Invocation) -> Outcome:
        p = parse(inv.args, spec)
        operands = p.positionals
        if not operands and default_cwd:
            operands = [HERE]
        return Outcome(files(K.READ, operands, inv, recursive=recursive))

    return handler


def grep(inv: Invocation) -> Outcome:
    p = parse(
        inv.args,
        Spec.of("efmABCdD", ["regexp", "file", "max-count", "context", "include", "exclude"]),
    )
    has_pattern_option = p.has("e", "regexp", "f", "file")
    operands = p.positionals if has_pattern_option else p.positionals[1:]
    recursive = p.has("r", "R", "recursive", "dereference-recursive") or inv.program in (
        "rg",
        "ag",
    )
    if not operands and recursive:
        operands = [HERE]
    out = Outcome(files(K.READ, p.all_values("f", "file"), inv))
    out.effects += files(K.READ, operands, inv, recursive=recursive)
    return out


_RG_SPEC = Spec.of("efgtTmABCjM", ["regexp", "file", "glob", "type", "type-not", "max-count"])


def ripgrep(inv: Invocation) -> Outcome:
    p = parse(inv.args, _RG_SPEC)
    has_pattern_option = p.has("e", "regexp", "f", "file")
    operands = (p.positionals if has_pattern_option else p.positionals[1:]) or [HERE]
    out = Outcome(files(K.READ, p.all_values("f", "file"), inv))
    out.effects += files(K.READ, operands, inv, recursive=True)
    return out


_AWK_DANGER = re.compile(r"system\s*\(|\|\s*getline|getline\s*<|print[f]?\b[^;]*[>|]")


def awk(inv: Invocation) -> Outcome:
    p = parse(inv.args, Spec.of("fvF", ["file", "assign", "field-separator"]))
    programs = p.all_values("f", "file")
    operands = p.positionals
    out = Outcome(files(K.READ, programs, inv))
    if not programs:
        source, operands = operands[:1], operands[1:]
        if any(w.dynamic or _AWK_DANGER.search(w.text) for w in source):
            out.opaque.append(IC)
    out.effects += files(K.READ, [w for w in operands if "=" not in w.text], inv)
    return out


def sort(inv: Invocation) -> Outcome:
    p = parse(inv.args, Spec.of("oktTS", ["output", "key", "field-separator", "buffer-size"]))
    out = Outcome(files(K.READ, p.positionals, inv))
    if (output := p.value("o", "output")) is not None:
        out.add(file_effect(K.WRITE, output, inv, detail="truncate"))
    return out


def uniq(inv: Invocation) -> Outcome:
    p = parse(inv.args, Spec.of("fsw", ["skip-fields", "skip-chars", "check-chars"]))
    out = Outcome(files(K.READ, p.positionals[:1], inv))
    out.effects += files(K.WRITE, p.positionals[1:2], inv, detail="truncate")
    return out


def jq(inv: Invocation) -> Outcome:
    out = Outcome()
    words = list(inv.args)
    operands: list[Word] = []
    i = 0
    while i < len(words):
        text = words[i].text
        if text in ("--arg", "--argjson", "--slurpfile", "--rawfile"):
            if text in ("--slurpfile", "--rawfile") and i + 2 < len(words):
                out.add(file_effect(K.READ, words[i + 2], inv))
            i += 3
            continue
        if text in ("-f", "--from-file") and i + 1 < len(words):
            out.add(file_effect(K.READ, words[i + 1], inv))
            i += 2
            continue
        if not text.startswith("-") or words[i].dynamic:
            operands.append(words[i])
        i += 1
    out.effects += files(K.READ, operands[1:], inv)
    return out


_HEAD_TAIL = Spec.of("ncs", ["lines", "bytes", "sleep-interval", "pid"])

CATALOG: Mapping[str, Handler] = {
    "rm": rm,
    "rmdir": plain_delete,
    "unlink": plain_delete,
    "shred": shred,
    "touch": touch,
    "mkdir": mkdir,
    "ln": ln,
    "tee": tee,
    "truncate": truncate,
    "install": install,
    "dd": dd,
    "wipefs": format_device,
    "fdisk": format_device,
    "sfdisk": format_device,
    "parted": format_device,
    "sed": sed,
    "cp": cp,
    "mv": mv,
    "tar": tar,
    "zip": zip_,
    "unzip": unzip,
    **dict.fromkeys(_COMPRESS_EXT, compress),
    "chmod": chmod_like,
    "chown": chmod_like,
    "chgrp": chmod_like,
    "chattr": chattr,
    "setfacl": setfacl,
    "grep": grep,
    "egrep": grep,
    "fgrep": grep,
    "zgrep": grep,
    "rg": ripgrep,
    "ag": ripgrep,
    "awk": awk,
    "gawk": awk,
    "mawk": awk,
    "sort": sort,
    "uniq": uniq,
    "jq": jq,
    "head": reader(_HEAD_TAIL),
    "tail": reader(_HEAD_TAIL),
    "cut": reader(Spec.of("bcdf", ["bytes", "characters", "delimiter", "fields"])),
    "ls": reader(
        Spec.of("IwT", ["ignore", "width", "tabsize", "sort", "format"]), default_cwd=True
    ),
    "du": reader(
        Spec.of("dBtX", ["max-depth", "block-size", "threshold", "exclude"]),
        recursive=True,
        default_cwd=True,
    ),
    "tree": reader(Spec.of("LIPo", ["filelimit"]), recursive=True, default_cwd=True),
    "stat": reader(Spec.of("c", ["format", "printf"])),
    "file": reader(Spec.of("mfF", ["magic-file", "files-from", "separator"])),
    "diff": reader(Spec.of("xXIUCFL", ["exclude", "label", "unified", "context"])),
    "iconv": reader(Spec.of("fto", ["from-code", "to-code", "output"])),
    **dict.fromkeys(
        (
            "cat", "tac", "less", "more", "wc", "cmp", "comm", "strings", "xxd", "od",
            "hexdump", "base64", "base32", "nl", "fold", "fmt", "rev", "column", "paste",
            "join", "md5sum", "sha1sum", "sha224sum", "sha256sum", "sha384sum", "sha512sum",
            "b2sum", "cksum", "shasum", "readlink", "realpath", "zcat", "bzcat", "xzcat",
        ),
        reader(),
    ),
}  # fmt: skip
