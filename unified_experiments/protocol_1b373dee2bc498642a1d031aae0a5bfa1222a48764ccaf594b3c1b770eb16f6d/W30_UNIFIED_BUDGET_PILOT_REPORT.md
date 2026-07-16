# W30 Unified Evaluation-Budget Pilot Report

This report calibrates execution budget only; it contains no algorithm-quality ranking.

The run was changed by user instruction after GA+ACO completed: DE-LKH was
interrupted after its attempt was launched, while HGA and ABMA were run with a
reduced 5,000-evaluation budget. Therefore this is partial mixed-budget
evidence, not a completed four-solver common-budget pilot.

## Output audit

| Solver | Previously available | Added in this stage | Runner reconstruction |
|---|---|---|---|
| GA+ACO | system metrics, route IDs/directions, algorithm timer | standard solution.robots, system/runtime blocks, flattened robot CSV fields | success |
| DE-LKH-Hybrid | system metrics, route IDs/directions, algorithm timer | standard solution.robots, system/runtime blocks, flattened robot CSV fields | interrupted |
| Paper-Aligned-HGA-XCut-Control | system metrics, route IDs/directions, algorithm timer | standard solution.robots, system/runtime blocks, flattened robot CSV fields | success |
| ABMA | system metrics, route IDs/directions, algorithm timer | standard solution.robots, system/runtime blocks, flattened robot CSV fields | success |

## Pilot runs

| Solver | Requested | Realized | Stop reason | Algorithm s | Solver wall s | Postprocess s | Total wall s | Eval/s |
|---|---:|---:|---|---:|---:|---:|---:|---:|
| GA+ACO | 16000 | 16000 | objective_budget | 4503.172796 | 4506.032310 | 0.130646 | 4506.195977 | 3.553050 |
| DE-LKH-Hybrid | 16000 | N/A | user_interrupted_after_attempt_launch | N/A | N/A | N/A | N/A | N/A |
| Paper-Aligned-HGA-XCut-Control | 5000 | 5000 | objective_budget | 3.637113 | 5.406525 | 0.365393 | 5.807546 | 1374.716781 |
| ABMA | 5000 | 5000 | objective_budget | 411.001212 | 412.795145 | 0.050094 | 412.864774 | 12.165414 |

## Common conclusion

- Recommended common primary budget: `null` (not established).
- Formula-derived provisional candidate: `16000` evaluations; not validated across all four solvers.
- Uniform common budget was executed: `false`.
- Recommended wall-clock safety timeout: `21600` seconds.
- W30 budget pilot completed: `false`.
- W45/W60 timing remains uncalibrated and must not be inferred from this W30 run.
- `formal_run_approved` remains false.
