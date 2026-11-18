# ADR-0007: Read git state through libgit2, never the git CLI

**Status:** Accepted · 2026-11-18

## Context

Probes need to know whether a change can be undone from git. The git CLI runs commands from repository config (`core.fsmonitor`, `filter.<x>.clean`) during `git status`, so a repository the agent controls could run code as Rootsh's user.

## Decision

Git state comes from pygit2 1.21.0 (libgit2 1.9.7, GPLv2 with linking exception), which runs no config-defined external commands.

## Consequences

Tested in P2 against a hostile repository: the probe ran neither command; the control, `git status` from git 2.53.0, ran both.
