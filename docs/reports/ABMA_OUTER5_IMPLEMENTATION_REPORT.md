# ABMA Outer5 Implementation Report

## Implementation

`MRTA_ABMA1.py` is a thin wrapper: it imports the authoritative `MRTA_ABMA`, subclasses `ABMAConfig`/`ABMASolver`, delegates parsing and output construction, then rewrites only the independent solver identity. It does not copy the approximately 2,800-line solver.

The sole search-factor change is `outer_generations: 20 → 5`. Controlled values remain population=12, objective cap=0, early-stop patience=0, `legacy_exact_fast`, ALNS=80, VND=3, official normalization, dynamic parent-aware split, open routes and exact bidirectional two-state DP.

The CLI rejects generations other than 5, nonzero objective caps, nonzero early stopping, and source variants other than `legacy_exact_fast`. Output identity is `ABMA-Outer5-v1` / `ABMA1` / `outer5_legacy_exact_fast`.

## Equivalence and instrumentation

The dedicated 7-test suite proves exact equality with authoritative ABMA at `--generations 5` for candidate/acceptance hashes, outer/inner RNG state hashes, solutions, histories, routes and statistics (runtime-only fields excluded). The full repository suite passed 96/96.

Entry checks: `MRTA_ABMA1.py --self-check` passed; `MRTA_ABMA.py --self-check` passed. The protected HGA CLI has no `--self-check` option and rejects that command because `--instance-path` is required. `run_unified_experiments.py --validate-protocol` and `--self-check` both report the repository's six pre-existing normalization frozen-float drift errors; the regression suite explicitly tests for that current behavior.

The cold entry only increases `IncumbentTrace` checkpoint density and counts accepted decisions. Patches are process-local and restored before exit; they do not modify search decisions or persistent module state.

ABMA final-population diversity is not exposed by the authoritative solver and is therefore marked unavailable. HGA's native `final_diversity_rank` is preserved, without claiming cross-algorithm semantic equivalence.

## Source authority and hashes

- `MRTA_ABMA.py`: `7f6f09a030543f6e4e14d2bd837791c90896da3bb1e395f409c33d6eaef20df9`
- `MRTA_ABMA1.py`: `223325235ef76ff67733cee711b480a6a308b4a8b93219c2d6e9f11c414fc622`
- `MRTA_HGA_PAPER_ALIGNED_CONTROL.py`: `db2557a2c96606f0b7f933808f9efb6b26468240073405697aa54304be26f2e7`
- `mrta_problem_core.py`: `92cd6da3c50ee7bcbbf029134ddd371dd876370f829f6e11205a8175cf1371b5`
- `objective_normalization.py`: `7465a04016897a4a51847bcae8b04c704bab01269220aaa21e0efeef99b18a83`
- `UNIFIED_EXPERIMENT_PROTOCOL.json`: `5fb8fec80189731e702186e13567ea8cb1d16c707b06446f9ab1e705c76291dc`
- `abma_final_profile.json`: `631b2d675e7531d04a3c019cc8013158223944074da3e42d489a5745addee1d3`
- `formal_experiment_manifest.json`: `c4f3e53e87a13c151cfebe77ef9de6398e6038638f18087892b6958444cc877e`
- No `MRTA_ABMA(16).py` exists in the current repository, so no alternate-file authority decision was needed.
- Protected hashes matched before and after the 36-run experiment.
