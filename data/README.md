# data/: local research data (git-ignored, except this file)

Nothing here is in git. Each item is kept because it meets one rule
(ADR-0019):

1. it runs the product;
2. a report number is reproduced from it;
3. the dissertation cites it.

Deleting any item breaks the rule it is listed under.

| Item | Size | What it is | Made by | Read by | Rule |
|---|---|---|---|---|---|
| `research.db` | 2.9 MB | The research database: 192 runs, events, labels, variants, freeze, `model_runs` | `rootsh collect`, `rootsh label`, `rootsh freeze`, `rootsh model` | every script in `research/` | 2 |
| `freeze/manifest-a357bba6c02c.json` | — | The frozen dataset's manifest; the dissertation cites its SHA-256 | `rootsh freeze` (P5) | `policy_burden.py`, `ablation.py`, `rootsh model` | 2, 3 |
| `freeze/heldout-a357bba6c02c.sealed.json` | — | The heldout labels, sealed; read once by the P6 evaluator | `rootsh freeze` (P5) | `rootsh model evaluate` (never rerun) | 3 |
| `traces/` | 2.9 MB, 192 files | The raw hook-event trace of each gold run | `rootsh collect run` (P4) | `rootsh collect verify`/`rebuild`, `gold_report.py` (§1) | 2 |
| `outputs/` | 4.0 MB, 192 files | Each run's agent stdout | `rootsh collect run` (P4) | `rootsh collect verify`, `gold_report.py` (§1) | 2 |
| `model/` | 188 KB | P6 bundle `ca33283a3dde`: 5 model files and `bundle.json` (hash-checked) | `rootsh model train` (P6) | the daemon, `rootsh install`, `model_report.py`, `policy_*.py`, `ablation.py` | 1, 2 |
| `gate/` | 208 KB | Live hook-gate results and runtime DBs (run 1 plus the final runs) | `research/hook_gate.py` (P8) | `hook_gate_report.py` (§9) | 2, 3 |
| `nl2bash/all.cm` | 572 KB | The NL2Bash corpus, 12,607 commands (with its LICENSE) | downloaded in P2 | `effects_robustness.py` (§8) | 2 |

§ numbers are sections of `docs/report-tables.md`.

**Paths stored inside the DB:** trace and output paths are stored relative
to this folder (`traces/…`), so they survived the v0.12.0 move from
`data/gold/`. The `freezes` row still records
`data/gold/freeze/manifest-…`. The frozen DB is never edited, so that row
keeps the old path, and the file is now at `freeze/`.

**Removed in v0.12.0** (they met no rule):
- the pre-migration DB snapshots;
- the dropped assistant-label files (kappa is read from the DB);
- the collection logs;
- the P5/P6 first-run printouts: P6's are copied into `docs/phases/06`, and
  P5's are regenerated in `docs/report-tables.md` §3–4;
- the gate `report.md` (regenerated in §9).
