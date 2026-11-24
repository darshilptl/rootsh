# ADR-0001: BPF-LSM intent scope (K4) is a stretch goal

**Status:** Accepted · 2026-11-18

## Context

K4 would compile the user's request into a per-session kernel policy that BPF-LSM enforces for the agent's process tree. It is the strongest kernel contribution but adds C/eBPF work and its own failure modes.

## Decision

K4 is a **stretch goal**. The core kernel layer is K1–K3 (Landlock, AppArmor, seccomp). K4 is attempted after P9, and only because P0 showed a BPF-LSM program loads and enforces on this host (`docs/phases/00-verification.md` §3.5).

## Consequences

P0's first BPF-LSM run locked files machine-wide through a prefix-match bug, root included. Any K4 rule needs exact matching, tests before loading, a detach path that cannot match its own rule, and a proven unload on shutdown.
