# ADR-0017: P8 two-step install, one client for both hooks, sandbox gate

**Status:** Accepted · 2026-11-21 (author approved sandbox + temp-root gate)
· Refines ADR-0003 under ADR-0016.

## Context

ADR-0003 plans one `sudo rootsh install` with a `rootsh` system user and a
root system service. ADR-0016 keeps the daemon an author-user service until
P9. Installing managed hooks on the host gates every Claude Code session,
this development session included.

## Decision

1. **Two steps:** `rootsh install` (author: bundle copy, user unit, OpenCode
   plugin) then `sudo rootsh install --system` (managed settings only).
   Uninstall reverses the order. The system user and `/opt/rootsh` move to
   P9 with self-protection.
2. **One client:** both hooks call `rootsh.daemon.client.decide()`; the
   OpenCode plugin spawns a Python bridge instead of speaking the socket
   protocol in TypeScript.
3. **Manifest-based uninstall:** prior bytes are kept; a file changed since
   install is never overwritten silently.
4. **Gate in the P4 sandbox** with the installer's own hook JSON and plugin
   file, and on a temporary install root. The host install is the author's
   step.

## Consequences

- The host is untouched by P8's verification.
- Until P9, an agent can forge events on the socket and can edit rootsh's
  user-owned files (design 08 §7).
