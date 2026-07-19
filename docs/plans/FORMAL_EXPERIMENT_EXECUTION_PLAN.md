# Formal Experiment Execution Plan

This file freezes a 270-run manifest and dry command templates only. It does not authorize or launch formal experiments.

- Protocol SHA-256: `5fb8fec80189731e702186e13567ea8cb1d16c707b06446f9ab1e705c76291dc`
- Instances: w30, w45, w60
- Official solvers: GA+ACO, Paper-Aligned-HGA-XCut-Control, ABMA-Legacy-Exact-Fast-v1
- Paired seeds: 42–71 (30 per solver/instance)
- Total raw optimization runs: 270
- Budget selection status: `pending_user_selection_between_A_and_B`
- Collision audit: separate postprocess after a successful frozen raw run
- Resume: requires matching protocol, source, and command hashes
- Failure: retain attempt and exclude it from successful paired statistics; never substitute a seed
- Formal execution remains fail-closed until explicit user approval and budget/timeout selections.

## Statistics

Per instance: descriptive mean/std/median/min/max/IQR/success/time; Friedman raw-fitness omnibus; paired Wilcoxon for three pairs; Holm correction; rank-biserial effect size; W/T/L; deterministic 10,000-resample paired median-difference percentile bootstrap 95% CI.

Raw and collision-adjusted metric families are reported separately.

## Sensitivity design

Weights, load-scale floor, deterministic baseline construction, and collision separation are isolated under a separate sensitivity output root and do not contaminate the main experiment.
