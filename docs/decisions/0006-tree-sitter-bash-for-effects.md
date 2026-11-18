# ADR-0006: Parse shell with tree-sitter-bash

**Status:** Accepted · 2026-11-18

## Context

The effect extractor needs a bash parser that handles real syntax and never crashes.

## Decision

tree-sitter-bash 0.25.1 (MIT). In P0 it scored 30/32 on a hand-labelled corpus at 0.02 ms per parse; bashlex scored 28/32, is GPLv3+, and fails on `$((…))`, `case`, `[[ ]]`.

## Consequences

Malformed input becomes `opaque` (`has_error`), never a crash: 0 crashes over 12,607 NL2Bash commands in P2.
