"""What a piece of text mentions: paths, URLs, hosts, words (design doc §4)."""

from __future__ import annotations

import json
import posixpath
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

# The scheme is bounded: unbounded, every start inside a long run of letters
# rescans the run, which is quadratic (60k chars took 5 s in the daemon, P8).
# Over all 44,089 strings of the 192 gold traces the bound changes no match.
_URL = re.compile(r"[A-Za-z][A-Za-z0-9+.-]{0,31}://[^\s'\"`<>()\[\]{}]+")
_SPLIT = re.compile(r"[\s'\"`<>|;,()\[\]{}=]+")
_FILENAME = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9_.-]*\.[A-Za-z0-9]{1,8}")
_HOST = re.compile(r"(?:[a-z0-9-]+\.)+[a-z]{2,}|\d{1,3}(?:\.\d{1,3}){3}")
_WORD = re.compile(r"[A-Za-z0-9._-]+")
_EDGE = ".:!?*"


@dataclass(frozen=True, slots=True)
class Mentions:
    paths: frozenset[str]  # resolved, normalized absolute paths
    urls: frozenset[str]  # without a trailing "/"
    hosts: frozenset[str]  # lower-cased
    words: frozenset[str]  # lower-cased
    text: str


def searchable(text: str) -> str:
    """Tool responses stored as JSON are searched by their string values."""
    stripped = text.lstrip()
    if not stripped.startswith(("{", "[")):
        return text
    try:
        data = json.loads(stripped)
    except ValueError:
        return text
    strings: list[str] = []
    stack = [data]
    while stack:
        item = stack.pop()
        if isinstance(item, str):
            strings.append(item)
        elif isinstance(item, dict):
            stack.extend(reversed(list(item.values())))
        elif isinstance(item, list):
            stack.extend(reversed(item))
    return "\n".join(strings)


def _normalize(path: str) -> str:
    normalized = posixpath.normpath(path)
    return "/" + normalized.lstrip("/") if normalized.startswith("//") else normalized


def _resolve(token: str, cwd: str | None, home: str) -> str | None:
    if token.startswith("~/"):
        return _normalize(home + token[1:])
    if token.startswith("/"):
        return _normalize(token)
    if cwd is None:
        return None
    return _normalize(posixpath.join(cwd, token))


def _is_path_like(token: str) -> bool:
    return (
        token.startswith(("/", "~/", "./", "../"))
        or "/" in token
        or _FILENAME.fullmatch(token) is not None
    )


def extract(text: str, *, cwd: str | None, home: str) -> Mentions:
    text = searchable(text)
    urls = {match.group(0).rstrip(_EDGE + "/") for match in _URL.finditer(text)}
    rest = _URL.sub(" ", text)  # a URL's path is never a file mention
    hosts = {host for url in urls if (host := (urlsplit(url).hostname or ""))}
    paths: set[str] = set()
    for raw in _SPLIT.split(rest):
        token = raw.lstrip("@").rstrip(_EDGE)
        if not token or "$" in token:
            continue
        lowered = token.lower()
        host = lowered.rsplit("@", 1)[-1].split(":", 1)[0]
        if _HOST.fullmatch(host):
            hosts.add(host)
        if _is_path_like(token) and (resolved := _resolve(token, cwd, home)) is not None:
            paths.add(resolved)
    words = {word.lower().strip(".") for word in _WORD.findall(rest)}
    return Mentions(
        paths=frozenset(paths),
        urls=frozenset(urls),
        hosts=frozenset(hosts),
        words=frozenset(w for w in words if w),
        text=text,
    )
