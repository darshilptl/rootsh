# Decisions

Recorded 2026-11-18. The author approved `PLAN.md` and chose the recommended
option for every open decision.

| # | Decision | Choice | Consequence |
|---|---|---|---|
| 1 | BPF-LSM intent scope (K4) | **Stretch** | Core kernel layer is K1–K3 (Landlock, AppArmor, seccomp). K4 is attempted after P9 only if P0 shows a BPF-LSM program loads on this host. |
| 2 | API budget for trace collection | **Minimal in P0; sized at P4** | P0 uses a handful of short runs on the smallest available model. P4 sets the trace budget from the per-run cost measured in P0. |
| 3 | Root install | **Required once (`sudo rootsh install`)** | Self-protection needs root-owned state, a system service, and loaded AppArmor profiles. |
| 4 | Second annotator | **Required before the P5 freeze** | The author's guide or a peer labels a stratified sample; Cohen's κ is reported. If unavailable, P5 reports single-annotator test–retest agreement and states it as a limitation. |
| 5 | Old `researchwork` repo | **Archive as-is; reuse nothing** | No data, model, or result from `researchwork` enters this project. Its audit is Phase A of `PLAN.md`. |
