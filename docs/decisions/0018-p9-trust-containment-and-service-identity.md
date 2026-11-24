# ADR-0018: P9 trust, containment, and service identity

**Status:** Accepted in part · 2026-11-24 (author chose the core split; see
"Scope in P9" at the end) · proposed 2026-11-23 · Supersedes ADR-0003's `rootsh` system-user
choice and the pathname-socket assumption in designs 07–08; refines ADR-0016
and ADR-0017. ADR-0001 remains unchanged: K4 is attempted only after P9.

## Context

P0 measured isolated kernel primitives. P4 measured a bwrap sandbox whose
Landlock rules cover TCP and abstract-UNIX/signal scoping, but no filesystem
access. The current host has Landlock ABI 8. A fresh probe on kernel
7.0.0-44 confirmed that ABI 8 does not stop a confined process connecting to a
pathname UNIX socket; `LANDLOCK_ACCESS_FS_RESOLVE_UNIX` is an ABI-9 feature
[RUN, DOC].

P8's same-UID socket does not authenticate hooks or the daemon. Hook children
inherit the agent's cgroup, mount namespace, Landlock domain and seccomp
filters. Moving hook files or forwarding their JSON through a broker does not
change that. A transparent broker would also replace the approval peer's
identity with the broker's.

A root or `rootsh`-account daemon cannot directly preserve P7's semantics:
`Path.home()`, private-home probes, same-UID hooks/approvals and the author's
desktop D-Bus session are all per-user. Running probes as root would increase
the privilege exposed to hostile repositories.

The host booted with `audit_enabled=0`; auditd and the journal audit socket are
not active [RUN]. Denial reporting therefore cannot be assumed from enforcement
alone.

## Decision

1. **Launcher-owned trust and monotonic decisions.** `rootsh run` registers
   the agent, launch ID, scope, real project, real home and initial headless
   prompt out of band. Events select that launch from the peer's actual cgroup,
   never from a claimed launch ID. Unregistered tool calls block. Hook `cwd`
   never establishes or changes the project. An interactive prompt becomes
   user authority only after explicit out-of-band confirmation; hook prompts
   are otherwise diagnostic only. Model-originated `allow` is clamped to `ask`.

   Decisions compose a **trusted-state baseline** with **deny-only untrusted
   vetoes**. Only launcher/confirmation records create `USER` provenance.
   Unauthenticated `WORKSPACE` and `OWN` never clear fabrication or content
   provenance. Content without a trusted user origin vetoes I2/F2 regardless
   of accompanying workspace/own sources. Untrusted events cannot increase
   `user_backed`, lower model risk, unlock F2, or move any verdict toward
   leniency (`block -> ask -> allow`). F1/F2 remain intrinsic effect/probe
   fast paths with their already-reported authorization limits, not proof of
   prompt intent.

2. **Separate endpoints.** Event/decision and control/approval traffic use
   different root-owned systemd sockets. Only the event socket is visible in
   the agent namespace. `approve` is accepted only on the control socket and
   only from the author outside an agent scope. No generic broker forwards
   approval. Clients verify the expected peer UID and protected socket layout
   as well as the response schema.

3. **Per-user daemon, root-controlled system installation.** The decision
   daemon continues to run as the author, now in a root-installed systemd
   **system** service with `User=<author>` and explicit home/session
   environment. Root owns the code/model deployment, unit/socket definitions,
   profiles and socket parent directories. The service runs with least
   privilege; there is no `rootsh` system user. This preserves home probes,
   notification and UID semantics while protecting endpoint integrity.

4. **ABI-correct containment.** bwrap supplies path and pathname-socket
   visibility. Landlock adds a full ABI-appropriate filesystem allowlist, TCP
   bind/connect restrictions and abstract-UNIX/signal scoping. AppArmor adds
   audited deny-inside-allow rules, including UNIX/D-Bus and peer interactions.
   Seccomp remains a small native-x86-64 escape-relevant denylist, extended to
   the kernel-module operations named in the plan. Every required layer and
   every setup failure prevents agent launch.

5. **Denial observability is a separate gate.** Kernel denials go to a trusted
   root-owned journal reader, a dedicated runtime table and an informational
   user notification. K1 uses `LANDLOCK_RESTRICT_SELF_LOG_NEW_EXEC_ON`, K2
   uses `audit deny`, and K3 uses `SECCOMP_FILTER_FLAG_LOG`; raw syscall
   `EACCES`/`EPERM` is never used to attribute a layer. K1 requires audit to
   be enabled on Linux 7.0. The author must explicitly approve the journald
   audit transition; neither installation nor tests silently enable audit or
   alter boot configuration. Related records are grouped by boot ID and audit
   serial and deduplicated across audit/kmsg transports. A kernel-denial
   pop-up has no misleading retroactive Allow action. Unknown attribution is
   logged and reported as unknown, never assigned to a guessed session.

6. **Protection is per artifact.** Code, interpreters, managed settings and the
   OpenCode shim may be readable/executable but are integrity-protected.
   Runtime DB/WAL/SHM, internal manifests, control sockets and broker state
   are daemon-only. Ordinary project and agent state remain writable as
   intended. Protection includes parent directories and rename/link/truncate
   attempts. The active defense no longer executes from a research checkout's
   `.venv`; research `data/` is never exposed. A checkout's own development
   environment is not confused with the installed defense.

## Consequences

- Direct agent launches are outside the P9 guarantee. With P9 installed,
  unregistered sessions fail closed; agents must be started with `rootsh run`.
- Interactive prompt confirmation is an intentional usability cost and is
  measured, not hidden. Headless prompts registered by the launcher do not pay
  it again.
- Model `allow` is not relied on in P9. This is stricter than P7 and does not
  retrain or re-evaluate a model. Validation-only burden is remeasured and
  heldout remains untouched.
- Third-party agent hooks/plugins and external OpenCode servers are disabled
  by the production launcher unless a later verified design gives them the
  same containment and trust properties. Explicit local MCP processes remain
  descendants of the confined tree.
- System installation and removal are author-run operations. Tests use
  temporary roots and never change host audit, AppArmor or systemd state.
- K1–K3 are the P9 gate. K4, including any per-session BPF-LSM intent map, is
  post-P9 stretch work.

## Scope in P9 (author's choice, 2026-11-24)

Built and tested without root (design 09): `rootsh run` in a
`rootsh-agent-*.scope`; launch registry on a control socket that the agent's
namespace never sees; launch chosen from the peer's cgroup, unregistered tool
calls blocked, project from the launcher (§1, without prompt confirmation);
separate event/control endpoints (§2, same-UID 0600 sockets); K1 full
Landlock allowlist with `LOG_NEW_EXEC_ON`, K3 seccomp with
`SECCOMP_FILTER_FLAG_LOG` (§4); K2 profile rendered and syntax-checked,
loaded by the author's `sudo` system step; per-artifact visibility (§6).

Not built, reported as limits in results 09: the root-installed `User=`
system service and root-owned sockets (§3); the journald/audit denial reader,
runtime denial table and pop-ups (§5); interactive prompt confirmation and the
trusted-baseline/untrusted-veto provenance split (§1, beyond the allow→ask
clamp); disabling third-party OpenCode plugins.
