# ADR-0003: Require a one-time root install

**Status:** Accepted · 2026-11-18

## Context

The defense must protect itself from the agent it watches, and the agent runs as the user.

## Decision

`sudo rootsh install` is required once. It creates root-owned state, a system service, Claude Code managed settings (`/etc/claude-code/managed-settings.json`, 0644), and loads AppArmor profiles.

## Consequences

Self-protection is impossible without it: P0 showed managed hooks survive a user's `disableAllHooks`, and Landlock/AppArmor keep the agent away from Rootsh's own files.
