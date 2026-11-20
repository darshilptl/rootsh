# P5 results: labelling, agreement, variants, audits, freeze

Phase 5 of `docs/plan.md`. Design: `docs/design/05-labelling.md`. Decisions:
ADR-0010 to ADR-0014. Every number is from a script run on
`data/gold/research.db` on 2026-11-20 [RUN] unless tagged otherwise. Raw
outputs: `data/gold/agreement-p5.txt` (`research/labels/agreement.py`) and
`data/gold/audits-p5.txt` (`research/labels/audits.py`); counts from
`research/labels/plan.py`.

## 1. Steps and who labelled them

| | steps |
|---|---|
| gold-set steps (192 runs) | 1,764 |
| covered by rule R1 v2 (read-only workspace steps) | 1,132 |
| author queue (heldout 162, validation 61, train 52) | 275, all answered |
| author answers stored (first pass 294, adjudicated 11) | 305 |
| disclosed assistant labels (`claude-opus-5-5`, ADR-0012/0013) | 473 |
| train steps with only assistant labels, **excluded** (ADR-0014) | 396 |

Self re-tests were dropped (ADR-0013), so the test–retest table is empty.

## 2. Agreement: author vs. the disclosed assistant (first pass, n = 77)

| field | agreement | kappa |
|---|---|---|
| authorized | 0.662 | **0.347** |
| cause | 0.519 | 0.269 |
| harm | 0.494 | 0.205 |
| severity (weighted) | 0.468 | 0.304 |

Confusion on `authorized` (author, assistant): both refused 22, author
refused / assistant allowed **21**, author allowed / assistant refused 5,
both allowed 29. The author is much stricter. Target was kappa ≥ 0.60
(ADR-0013); it is missed, so **all assistant training labels are dropped**.

## 3. Rule R1 and its audit

- **R1 v1 audit:** the author refused 15 of 60 sampled R1 steps
  (25%). A second look (`rootsh label --adjudicate`, first answer shown)
  kept 4 refusals, re-answered 11 (10 to yes, 1 still no).
- **R1 v2** (commit 4781f0b) leaves reads inside `.git` to a person; one
  standing refusal read `.git/hooks` and `git config`. 25 steps left R1,
  22 of them new author labels.
- **Under v2:** 4 disagreements in 57 audited rule steps, **7.0%
  (95% Wilson interval 2.8%–16.7%)**. The 4 are plain reads of project files
  (`mkdocs.yml` and docs, a glob for `mkdocs.yaml`, `app/orders.py`,
  `README.md`) refused for their context; no context-free rule separates them.
  Gate 2 ("no disagreement") is amended to this disclosure (ADR-0014, after
  the numbers were seen).
- The one `parse_error` (dotfiles, `&& # comment` on one line) is correct:
  `bash -n` rejects the same command.

## 4. Tier-2 variants

191 variants from 131 author-labelled non-heldout parents (G1 138, G2 40,
G3 3, G4 10), far below the design's "few thousand" target because only
human labels may be parents. Blind audit, 53 sampled:

| generator | audited | disagree | result |
|---|---|---|---|
| G1 prompt swap | 20 | 3 (15%) | dropped (> 10%) |
| G2 target swap | 20 | 8 (40%) | dropped |
| G3 origin swap | 3 | 0 | kept |
| G4 origin restore | 10 | 0 | kept |

## 5. Shortcut audits (leave-one-repository-out AUC, design 05 §9)

With R1 labels in training, tier 1 failed: source-only **0.790**,
scenario-ID **0.289** (two-sided gate: 1 − 0.289 = 0.711 > 0.60). R1's 802
non-heldout labels are all yes and almost all read/glob/grep, so agent and
tool predicted the label. **ADR-0014 item 6: training uses human labels
only**; R1 labels stay in the manifest for coverage and separate reporting.
On what trains:

| tier | view | n | AUC | gate |
|---|---|---|---|---|
| 1 | action-only | 131 | 0.422 | report |
| 1 | prompt-only | 131 | 0.569 | report |
| 1 | source-only | 131 | 0.537 | pass |
| 1 | task/scenario-ID | 131 | 0.492 | pass |
| 1 | file-name | 131 | 0.546 | pass |
| 2 | all five | 13 | n/a | not measurable |

Tier 2 is not measurable: its 13 variants do not hold both classes in two
repositories (ADR-0014 item 7).

**Diversity** (training sources): tier 1 train 70 steps, 31 unique prompts,
66 unique actions; validation 61 steps, 17 prompts, 58 actions; 5.4% of
validation action 3-grams occur in train. Tier 2: train 4, validation 9,
overlap 0.

## 6. Freeze

`rootsh freeze` → manifest SHA-256
`a357bba6c02cdb9f27c9e42bf93eb055a717c75df4506e59595018e56cbc7931`,
written twice with the same hash; `freezes` holds one row. Heldout labels are
only hashes in the manifest, with labels in
`data/gold/freeze/heldout-a357bba6c02c.sealed.json` for the P6 evaluator.

## 7. Gate (design 05 §10, as amended by ADR-0014)

| # | gate | status |
|---|---|---|
| 1 | every heldout/validation step labelled; excluded train steps counted | pass (396 excluded) |
| 2 | R1 audit done, disagreement disclosed | pass as amended (4/57) |
| 3 | agreement reported | reported; kappa 0.347 misses 0.60 |
| 4 | variant audit per generator | pass; G1, G2 dropped |
| 5 | splits; no variant crosses its parent's split | pass (tests) |
| 6 | gated audits on what trains | pass; tier 2 not measurable |
| 7 | freeze hashed, repeatable | pass |
| 8 | 13 catalog programs with regression tests | pass (earlier P5 commits) |
| 9 | ruff, format, mypy strict, tests | pass (441 tests) |

## 8. Limits

- **Training data is small:** 70 human train labels with 14 refusals, plus
  4 G4 variants; validation 61 human labels and 9 variants. P6 results
  carry wide intervals.
- **One annotator.** The only agreement measure is against a model
  annotator, and it is low (0.347); the author was stricter throughout.
- **R1 error 7.0%** on its audit; heldout evaluation reports R1-labelled
  steps apart from human-labelled ones.
- **Post-hoc amendments** (gates 1–3, two-sided audit gate, human-only
  training) were made after seeing the numbers; each is recorded with its
  reason in ADR-0014.
- **Open for P8/P9:** agents read rootsh's own `.venv` and `data/` through
  the sandbox in several gold runs, and in one run tried to delete
  `data/gold/toolenv`; the defense's files must be unreadable to agents.
