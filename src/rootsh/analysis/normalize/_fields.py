"""Typed field access on untrusted hook payloads."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from rootsh.analysis.events import JSON, EventParseError


def require_object(payload: object) -> Mapping[str, Any]:
    if not isinstance(payload, Mapping) or not all(isinstance(key, str) for key in payload):
        raise EventParseError("payload must be a JSON object")
    return payload


def require_str(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise EventParseError(f"field {key!r} must be a non-empty string")
    return value


def optional_str(payload: Mapping[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise EventParseError(f"field {key!r} must be a string")
    return value


def require_text(payload: Mapping[str, Any], key: str) -> str:
    """A string that may be empty (an empty prompt is still a prompt)."""
    value = payload.get(key)
    if not isinstance(value, str):
        raise EventParseError(f"field {key!r} must be a string")
    return value


def require_mapping(payload: Mapping[str, Any], key: str) -> Mapping[str, JSON]:
    value = payload.get(key)
    if not isinstance(value, Mapping):
        raise EventParseError(f"field {key!r} must be an object")
    return require_object(value)
