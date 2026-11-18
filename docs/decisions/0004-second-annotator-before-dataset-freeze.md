# ADR-0004: A second annotator before the dataset freeze

**Status:** Accepted · 2026-11-18

## Context

Labels decide what the model learns; one person's labels have no measured agreement.

## Decision

The author's guide or a peer labels a stratified sample before the P5 freeze, and Cohen's κ is reported.

## Consequences

If no second annotator is available, P5 reports single-annotator test–retest agreement and states it as a limitation.
