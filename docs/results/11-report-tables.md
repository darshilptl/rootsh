# P11 results: one generator for every report table

Script: `research/report/tables.py`. Output: `docs/results/report-tables.md`
(generated; do not edit by hand). Gate (closing plan item 2): every report
table (dataset, model, burden, ablation, kernel, latency) comes from one
command, with no hand-typed numbers.

## 1. What the generator does

- Copies `data/gold/research.db` to a temporary directory; every script
  reads the copy. The real DB was not written (mtime still 2026-11-20).
- Runs, unchanged, the phase scripts that already own each table, and
  inlines their stdout verbatim with the command that reproduces it:

| § | table | script | phase |
|---|---|---|---|
| 1 | runs, cost, tool calls, critical arguments, verify | `research/gold/report.py` | P4 |
| 2 | labelling plan (1,764 steps) | `research/labels/plan.py` | P5 |
| 3 | agreement (assistant kappa; test-retest empty, ADR-0013) | `research/labels/agreement.py` | P5 |
| 4 | variant audit, splits, shortcut audits | `research/labels/audits.py` | P5 |
| 5 | model validation + the single P6 heldout run (stored rows) | `research/model/report.py` | P6 |
| 6 | policy burden on validation | `research/policy/burden.py` | P7 |
| 7 | layer ablation C0–C3 + kernel static estimate | `research/ablation/ablation.py` | P10 |
| 8 | decision latency, large repository | `research/policy/latency.py` | P7 |
| 9 | kernel adversarial tests (pytest summary) | `tests/run`, `tests/collect/test_sandbox_security.py` | P8–P10 |

- Heldout is never scored again: §5 reads the five `model_runs` heldout rows
  written once in P6; §6 and §7 load the manifest with heldout excluded.
- §8 and §9 are fresh measurements on this machine and vary run to run;
  everything else is deterministic from the frozen DB and bundle.

## 2. Checked against earlier results [RUN]

§1–§7 and §9 reproduce the numbers already reported in results 04–10
(192 runs, 1,764 tool calls, heldout AUC 0.631, P7 asks 333 per 1k and 9
false allows, ablation V/R tables, kernel tests 52 passed, 2 K2 skipped).

## 3. Fix found while generating, with reason

1. **Latency script was stale since P9.** The first P11 run showed every
   latency decision as `failure:block` at ~0.1 ms: P9 made the daemon block
   tool calls from unregistered launches, and `latency.py` (written in P7)
   never registered one, so it measured the refusal, not a decision.
   `latency.py` now registers a launch from its own non-agent cgroup and
   sends events as a peer inside `rootsh-agent-<id>.scope`, the same path
   `rootsh run` uses. After the fix the outcomes match P7 (`cat` →
   read_only allow, `rm` → model block) and file decisions stay under
   20 ms p99; see report-tables §8 for the numbers of the committed run.
   The stale run was discarded; nothing else was rerun or changed.

## 4. Checks

ruff, ruff format, mypy strict and the full pytest (616 passed, 2 K2
skipped) pass.
