# Decisions

Architecture decision records. One file per decision; a decision is changed only by a new record that supersedes it.

| # | Decision | Status |
|---|---|---|
| [0001](0001-bpf-lsm-intent-scope-is-a-stretch-goal.md) | BPF-LSM intent scope (K4) is a stretch goal | Accepted |
| [0002](0002-trace-budget-from-measured-cost.md) | Size the trace budget from measured cost | Accepted |
| [0003](0003-one-time-root-install.md) | Require a one-time root install | Accepted |
| [0004](0004-second-annotator-before-dataset-freeze.md) | A second annotator before the dataset freeze | Accepted |
| [0005](0005-archive-researchwork.md) | Archive the old researchwork project; reuse nothing | Accepted |
| [0006](0006-tree-sitter-bash-for-effects.md) | Parse shell with tree-sitter-bash | Accepted |
| [0007](0007-git-state-via-libgit2.md) | Read git state through libgit2, never the git CLI | Accepted |
| [0008](0008-tiered-dataset.md) | A tiered dataset: real gold set, grounded generated training data | Accepted |
| [0009](0009-project-layout.md) | Project layout | Accepted |
| [0010](0010-labelling-scope-and-tiered-gates.md) | Labelling scope, rule labels, and shortcut gates per tier | Accepted (item 3 superseded by 0011) |
| [0011](0011-author-test-retest-instead-of-guide.md) | Author test–retest instead of a second annotator (supersedes 0010 item 3) | Accepted |
| [0012](0012-disclosed-assistant-annotator.md) | A disclosed AI annotator for the training tail, measured by kappa | Accepted |
| [0013](0013-human-evaluation-labels-model-training-labels.md) | Human labels for evaluation, disclosed model labels for training (supersedes 0011, 0012 items 2 and 5) | Accepted |
| [0014](0014-labels-after-agreement-and-audits.md) | The labelled dataset after the agreement and R1 audits (applies 0013 item 3; amends design 05 gates 1-3; human-only training) | Accepted |
