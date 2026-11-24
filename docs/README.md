# Rootsh docs: where to find the answer

Rootsh is a defense layer for host-level coding agents (Claude Code,
OpenCode) on Ubuntu. Before each tool call it works out what the action
would do and where its arguments came from, then allows, asks or blocks.
`rootsh run` adds kernel containment underneath.

**Status:** v0.12.0. Phases P0–P11 are done, and P12 (audit and minimal
layout) is done. Next: the demo rehearsal, then the dissertation.
`AGENTS.md` §7 has the handoff.

## Start here

| I want… | Open |
|---|---|
| every number in the report | [report-tables.md](report-tables.md) (generated: `uv run python research/report_tables.py`) |
| why something was decided | [decisions/](decisions/README.md) (ADR index, with statuses) |
| how a phase was designed, and what it measured | [phases/](#phases): one file per phase, the design part then the results part |
| the original plan and build order | [plan.md](plan.md) |
| the labelling rubric | [rubric.md](rubric.md) |
| what is in `data/` | [../data/README.md](../data/README.md) (on the author's machine only) |

## By topic

| Topic | Phase doc | ADRs | Code | Script → report-tables § |
|---|---|---|---|---|
| Feasibility checks (hooks, Landlock, D-Bus) | [00](phases/00-verification.md) | 0001, 0003 | — | — |
| Event model, the two databases | [01](phases/01-events-storage.md) | 0009→0019 | `analysis/events.py`, `storage/` | — |
| What a command does (effects), host state | [02](phases/02-effects.md) | 0006, 0007 | `analysis/effects/`, `analysis/hoststate/` | `effects_robustness.py` → §8 |
| Where arguments came from (provenance) | [03](phases/03-provenance.md) | — | `analysis/provenance/` | — |
| Gold set: 192 sandboxed agent runs | [04](phases/04-trace-harness.md) | 0002, 0008 | `research/collect/` | `gold_report.py` → §1 |
| Labels, agreement, variants, freeze | [05](phases/05-labelling.md) | 0004, 0010–0014 | `research/labels/` | `label_plan.py`, `label_agreement.py`, `label_audits.py` → §2–4 |
| Model: LightGBM, calibration, conformal | [06](phases/06-model.md) | 0015 | `decision/model/`, `research/training/` | `model_report.py` → §5 |
| Policy order, daemon, ask channel | [07](phases/07-daemon.md) | 0016 | `decision/policy.py`, `enforcement/daemon/`, `decision/notify/` | `policy_burden.py` → §6, `policy_latency.py` → §10 |
| Agent hooks, installer, doctor | [08](phases/08-hooks-install.md) | 0003, 0017 | `enforcement/hooks/`, `enforcement/install/` | `hook_gate.py` (live), `hook_gate_report.py` → §9 |
| `rootsh run`, kernel layers K1–K3 | [09](phases/09-kernel-run.md) | 0018 | `enforcement/run/`, `enforcement/kernel/` | kernel tests → §11, `k2_profile.py` |
| Layer ablation and attack replay | [10](phases/10-ablation.md) | — | — | `ablation.py` → §7 |
| One generator for every table | [11](phases/11-report-tables.md) | — | — | `report_tables.py` |
| Audit and this layout | [12](phases/12-cleanup.md) | 0019 | `tests/test_layers.py` | — |

Code paths are under `src/rootsh/`, and scripts under `research/`.

## Phases

`phases/NN-name.md` joins two documents that used to be separate. The design
part was committed before any number was measured; the commit hashes in each
file prove it. The results part follows. In older text, "design 07 §2" means
the Design part of `phases/07-daemon.md`, §2, and "results 07 §3" means its
Results part.

## Files removed in v0.12.0

Removed files can still be read at tag `v0.11.0`. The docs cite them as
`v0.11.0:<path>`; read one with `git show v0.11.0:<path>`. They are:

- the P0 probe scripts (`research/p0-verification/`);
- the P3 transcript recorders;
- the old-project audit scripts;
- the raw P0/P2/P7/P8 evidence files in `docs/results/data/`;
- the separate `docs/design/` and `docs/results/` files.

`docs/phases/12-cleanup.md` §3 has the full old → new map.
