# CPLR Phase 1 Core Representation Report

## 1. Repository state

- Repository: `luckyfishanddog/MRTA` working copy at `D:\pybullet_test\MRTA_GA`.
- Initial/current branch: `experiment/eprk-hga-atomic-formal-comparison`.
- Initial HEAD: `c3e06a00776bc03f28f58ab5ae02b4fce1325540` (`c3e06a0 Add current MRTA AI handoff report`).
- The worktree was not clean at entry: the user already had two untracked methodology files under `docs/plans/`. No branch switch, reset, clean, checkout, or overwrite was performed.
- Requested interpreter path `D:\pybullet_test.venv\Scripts\python.exe` did not exist. All development commands used the actual project interpreter `D:\pybullet_test\.venv\Scripts\python.exe`.
- `pytest>=9.0`, already declared in `requirements.txt`, was missing from that environment and was installed before testing.
- Baseline suite: 188 passed, 1 failed. Final suite: 206 passed, 1 failed. The sole failure is unchanged: `test_protocol_exact_validator_reports_frozen_float_drift` expects six historical normalization-drift messages but currently receives none.

## 2. Existing modules read and reused

- `mrta_problem_core.partition_world_y`: fixed horizontal partition position.
- `mrta_problem_core.assign_welds_to_robots_split`: independent standard dynamic-splitter reference path.
- `mrta_problem_core.evaluate_order`: frozen two-state minimum-route-time direction DP, floating-point operation order, and final-direction tie rule.
- `mrta_problem_core.MotionModel`: welding/travel parameters.
- `generate_welds.Weld` and `load_frozen_weld_instance`: geometry and frozen w30/w45/w60 loading.
- `objective_normalization`: optional normalization schema accepted by the decoder without redefining the scientific objective.
- `solver_output_metrics`: four-robot/system metric naming and invariants.
- `atomic_route_evaluator` and its tests: independent evidence for the two-state direction-DP contract.
- `MRTA_ABMA.py` and existing ABMA/fixed-pre-split tests were read for compatibility only.

Frozen files were not modified: `MRTA_ABMA.py`, `MRTA_HGA_PAPER_ALIGNED_CONTROL.py`, `MRTA_EPRK_MA.py`, and `atomic_problem_core.py` have no diff.

## 3. New immutable data structures

- `Lineage`: stable integer ID, parent weld ID, upper/lower half, global parent parameter interval, endpoints, x extent, length, and left/right reachability flags.
- `LineageSideId`: sortable `(lineage_id, side)` identity; transient `_segN` IDs are not used for cross-candidate identity.
- `ActiveTask`: unique lineage-side identity, robot owner, parent parameter interval, current endpoints, length, and weld time.
- `CPLRChromosome`: upper/lower boundary genes and sorted reachable lineage-side random keys.
- `DecodedCPLRState`: decoded boundaries, active tasks, four ordered signatures, route statistics, direction flags, system metrics, and structural validation.
- `ReachableSideIndex` and `CertificationResult`: immutable preprocessing/certification evidence.

## 4. Fixed-horizontal lineage rules

Original welds are sorted deterministically by stable ID and exact endpoint tuple. Each weld is split once at the existing fixed horizontal boundary only when the frozen strict crossing test is satisfied. Every child stores its exact interval in the original weld parameterization. Segments on the shared horizontal boundary use the existing upper-first half-open ownership rule. Zero-length children are excluded.

Observed frozen-instance results:

| Instance | Original welds | Lineages | Horizontal split parents | Length error |
|---|---:|---:|---:|---:|
| w30 | 30 | 31 | 1 | 1.4211e-14 |
| w45 | 45 | 48 | 3 | 1.4211e-14 |
| w60 | 60 | 63 | 3 | 1.4211e-14 |

## 5. Left/right task decoding

The upper/lower genes map linearly to the configured legal domains, defaulting to `[0, 20]`. An upper lineage reads `x_up`; a lower lineage reads `x_low`. A strict interior x intersection creates at most one nonzero left and one nonzero right task. Non-crossing and coincident cases use the frozen left-first half-open rule. The decoder reproduces the standard splitter's parameter-space endpoint de-duplication, including its asymmetric near-one endpoint behavior.

Robot ownership is fixed: upper-left/upper-right/lower-left/lower-right map to robots 0/1/2/3. A lineage-side identity can appear at most once and belongs to exactly one route whenever the standard splitter supplies a valid assignment.

## 6. Reachable-side pruning

Reachability is determined by enumerating each endpoint/tolerance event, its neighbours, and all open event intervals over the configured boundary domain. Only sides that are never active over the full legal domain are removed. The index includes domain and epsilon, so a changed domain/tolerance requires rebuilding rather than reusing stale results.

For the full `[0,20]` domains, all sides of w30/w45/w60 are reachable: 62/96/126 keys and a pruning ratio of 0. This is a valid result; no unsupported pruning was claimed.

## 7. Route ordering and direction tie rule

Each route is sorted from scratch by `(random_key, LineageSideId)`. Equal random keys therefore have a deterministic integer/side tie-break. No incremental sorting, route cache, LRU, repair, ALNS, VND, or local search was added.

The ordered task geometry is passed directly to `mrta_problem_core.evaluate_order`. Thus the route objective remains minimum route completion time, the DP remains two-state, and the frozen tie rule remains: strict `<` during transitions and final direction 0 when the two terminal DP values are equal.

## 8. Theory-to-code correspondence

- One fixed-horizontal lineage per non-crossing weld and two for a strict y-crossing weld: `build_lineages`.
- At most one task per lineage per robot: convex line-segment/half-region intersection and `split_lineage_at_boundary`.
- Any decoded valid task has unique ownership: `LineageSideId`, route partitioning, and structural validation.
- Opposite left/right orders for the same two parents require two side keys: explicit counterexample/positive construction in `test_route_completeness.py`.
- Any target small route permutation is constructible with `q(v_j)=j/(n+1)`: constructive completeness test.
- Direction optimality and tie compatibility: exhaustive direction enumeration and standard-certifier comparison.

## 9. Tests and results

New phase-1 tests: 18 passed.

- Horizontal lineage conservation, stable IDs, and horizontal-boundary ownership.
- Decoder/standard-splitter equivalence at extremes, interior values, and mixed boundaries.
- ULP endpoint absorption regression.
- Known standard-splitter parameter-epsilon gap regression.
- Random uniqueness and per-robot lineage-side ownership.
- Pruned/unpruned active-route equivalence.
- Single-parent-key counterexample and two-side-key completeness construction.
- Fieldwise decoder determinism and equal-key tie ordering.
- Direction DP versus exhaustive orientation enumeration.

Final complete suite: 206 passed, 1 unchanged pre-existing failure in 23 seconds (latest full run is repeated after report generation in the handoff commands). There are no new test regressions.

## 10. Randomized equivalence validation

Command:

```powershell
& 'D:\pybullet_test\.venv\Scripts\python.exe' src\run_cplr_phase1_validation.py --candidates 100000 --seed 20260719 --output-dir results\cplr_phase1
```

Coverage: 10,000 candidates in each of ten categories: interior random, exact endpoint, endpoint `-2*eps`, endpoint `+2*eps`, upper-only change, lower-only change, both changes, all keys tied, upper extreme, and lower extreme. Instance counts were 33,334/33,333/33,333 for w30/w45/w60.

Result: **98,928 certified; 1,072 failed; NO-GO**.

- All 1,072 failures are decoder rejection/standard-assignment failures in directed endpoint tolerance neighbourhoods.
- Among candidates reaching full certification, structural, route-order, direction, numerical, duplicate, and omission differences were zero.
- The first 1,000 full failure chromosomes are retained in `randomized_equivalence_failures.jsonl`; the summary explicitly records truncation.

Minimal counterexample:

- Instance: w30.
- Parent weld: `inst0006|group=312-GR2C|row=0000`.
- Geometry: `(4.198308820222, 4.322467866854, 0.1)` to `(1.239168195222, 4.322467866854, 0.1)`.
- Boundary: `x_low = 4.198308818221999` (about `2e-9` left of the first endpoint).
- Frozen standard result: the intersection is suppressed by parameter-space epsilon de-duplication, the complete segment is no longer compatible with either x half under coordinate-space epsilon, `classify_weld_region` returns `None`, and `unassigned_subweld_count=1`.

Root cause: one numeric epsilon is applied in two different spaces. `_unique_parameters` compares dimensionless segment parameters, while region ownership compares world-coordinate metres. For welds with x span greater than one metre, a parameter displacement below `1e-9` can still be a coordinate displacement above `1e-9`, creating a microscopic ownership gap.

The frozen splitter was not modified and the CPLR decoder does not hide this case with a looser tolerance.

## 11. Microbenchmark raw data

Command:

```powershell
& 'D:\pybullet_test\.venv\Scripts\python.exe' src\run_cplr_phase1_microbenchmark.py --iterations 200 --seed 20260719 --output-dir results\cplr_phase1
```

| Instance | Preprocess ms | Active decode ms | Sort ms | Direction DP ms | Aggregate ms | CPLR total ms | Standard total ms | Peak bytes |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| w30 | 46.0415 | 2.7947 | 0.5809 | 5.9839 | 0.1029 | 9.4728 | 11.8946 | 7,657,414 |
| w45 | 169.6932 | 5.4582 | 1.1633 | 11.1476 | 0.1147 | 17.9005 | 22.1746 | 8,234,695 |
| w60 | 196.3722 | 4.9907 | 0.9888 | 9.9311 | 0.0823 | 16.0030 | 20.0277 | 8,405,326 |

These are read-only development measurements. They do not establish a fixed speedup or an SCI performance conclusion. Direction DP is the largest measured CPLR phase; sorting is not the principal bottleneck.

## 12. Phase gate

| Gate | Result | Evidence |
|---|---|---|
| Horizontal pre-split length conservation | PASS | Max reported error 1.4211e-14 |
| Vertical dynamic split length/assignment conservation | FAIL | 1,072 directed tolerance-gap candidates rejected/unassigned |
| Every active task has unique ownership | FAIL as a universal claim | Standard splitter has legal-domain unassigned gaps |
| No omissions/duplicates | FAIL | Minimal counterexample has one omitted/unassigned parent task |
| Two-side-key route completeness | PASS | Counterexample and constructive tests pass |
| 100,000 candidates have zero structural differences/failures | FAIL | 1,072 final-certification failures |
| Direction DP equals standard implementation | PASS for certifiable candidates | Zero direction differences; exhaustive test passes |
| Final certifier failures are zero | FAIL | 1,072 |
| Existing suite has no new failure | PASS | Baseline and final retain the same one unrelated failure |
| w30/w45/w60 microbenchmark saved | PASS | CSV and JSON generated |

## 13. Known limitations

1. The frozen standard dynamic splitter has a parameter-space/coordinate-space epsilon mismatch near some endpoints.
2. Full-domain reachable-side pruning is zero on the three selected instances; future reduced domains may prune keys but must rebuild the index.
3. The microbenchmark compares complete read-only pipelines but is not a formal performance experiment.
4. The repository already has one unrelated failing normalization-drift test.
5. No GA, PER, provenance state, crossover, local search, LRU, incremental sorting, or formal experiment was implemented.

## 14. GO / NO-GO and next phase

**NO-GO. Phase 2 PER development is not permitted.**

Before Phase 1 can be reconsidered, the shared standard splitter's endpoint tolerance contract must be resolved explicitly. Because that module is part of the frozen scientific baseline for this task, the fix requires a separately authorized model/compatibility change, regression tests for parameter-to-coordinate epsilon conversion, and rerunning the complete baseline, 100,000-candidate gate, and microbenchmark. Merely excluding or loosening the failing candidates would not satisfy the stated correctness gate.

## 15. Modified files

Source:

- `src/lineage_presplit.py`
- `src/continuous_lineage_decoder.py`
- `src/reachable_side_keys.py`
- `src/cplr_standard_certifier.py`
- `src/run_cplr_phase1_validation.py`
- `src/run_cplr_phase1_microbenchmark.py`

Tests:

- `tests/test_lineage_presplit.py`
- `tests/test_lineage_decoder.py`
- `tests/test_lineage_side_uniqueness.py`
- `tests/test_reachable_side_pruning.py`
- `tests/test_route_completeness.py`
- `tests/test_cplr_decoder_determinism.py`
- `tests/test_cplr_direction_dp_equivalence.py`

Results/report:

- `results/cplr_phase1/lineage_summary.json`
- `results/cplr_phase1/randomized_equivalence_summary.json`
- `results/cplr_phase1/randomized_equivalence_failures.jsonl`
- `results/cplr_phase1/decoder_microbenchmark.csv`
- `results/cplr_phase1/decoder_microbenchmark_summary.json`
- `docs/reports/CPLR_PHASE1_CORE_REPRESENTATION_REPORT.md`
