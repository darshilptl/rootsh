# ADR-0008: A tiered dataset: real gold set, grounded generated training data

**Status:** Accepted · 2026-11-18

## Context

Large live agent collection in VMs is too costly for the author's RAM and Claude Code budget, and volume alone proves little.

## Decision

Three tiers. (1) A gold set of a few hundred short scenarios run on the real Claude Code and OpenCode, in Landlock/bwrap sandboxes rather than VMs, one run at a time; this is the evaluation set. (2) A training set generated offline as controlled variants of real recorded steps (different prompt, target, or argument source), at near-zero token cost. (3) Public attack benchmarks inform scenario design, not data.

## Consequences

The headline result is generated-to-real: train mostly on tier 2, evaluate on tier 1. Most real runs use OpenCode free models; a smaller Claude Code slice keeps the Claude Code claim real. Exact counts and budget are fixed in the P4 design.
