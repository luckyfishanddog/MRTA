# Three-Solver W30 Evaluation-Budget Pilot Report

This report calibrates execution budget only; it contains no algorithm-quality ranking.

## Output audit

| Solver | Previously available | Added in this stage | Runner reconstruction |
|---|---|---|---|
| GA+ACO | system metrics, route IDs/directions, algorithm timer | standard solution.robots, system/runtime blocks, flattened robot CSV fields | success |
| Paper-Aligned-HGA-XCut-Control | system metrics, route IDs/directions, algorithm timer | standard solution.robots, system/runtime blocks, flattened robot CSV fields | success |
| ABMA | system metrics, route IDs/directions, algorithm timer | standard solution.robots, system/runtime blocks, flattened robot CSV fields | success |

## Pilot runs

| Solver | Requested | Realized | Stop reason | Algorithm s | Solver wall s | Postprocess s | Total wall s | Eval/s |
|---|---:|---:|---|---:|---:|---:|---:|---:|
| GA+ACO | 16000 | 16000 | objective_budget | 3862.163003 | 3864.625866 | 0.058902 | 3864.715061 | 4.142756 |
| Paper-Aligned-HGA-XCut-Control | 16000 | 16000 | objective_budget | 11.084498 | 12.913797 | 0.057625 | 12.988671 | 1443.457288 |
| ABMA | 16000 | 16000 | objective_budget | 110.289496 | 112.387072 | 0.066623 | 112.465737 | 145.072746 |

## Raw scientific metrics (no ranking)

| Solver | Fitness | Makespan | Load imbalance | Total idle distance |
|---|---:|---:|---:|---:|
| GA+ACO | 0.83047947605 | 1244.60993309 | 94.9291399388 | 65.6888065749 |
| Paper-Aligned-HGA-XCut-Control | 1.28520195024 | 1313.83258766 | 199.047300041 | 77.0349860316 |
| ABMA | 0.763633595249 | 1244.32684864 | 0.173696823588 | 103.237932534 |

## Common conclusion

- Recommended common primary budget: `16000` evaluations.
- Uniform common budget was executed: `true`.
- Recommended wall-clock safety timeout: `21600` seconds.
- W30 budget pilot completed: `true`.
- W45/W60 timing remains uncalibrated and must not be inferred from this W30 run.
- `formal_run_approved` remains false.

