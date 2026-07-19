# W60 Scale Calibration Report

This is a budget/timing Pilot only. It contains no algorithm-quality ranking.

| Solver | Requested | Realized | Status | Stop | Algorithm s | Solver wall s | Fitness | Makespan |
|---|---:|---:|---|---|---:|---:|---:|---:|
| GA+ACO | 11000 | 11000 | success | objective_budget | 13667.266518 | 13669.840594 | 1.09123410403 | 3245.26578257 |
| Paper-Aligned-HGA-XCut-Control | 11000 | 11000 | success | objective_budget | 10.898713 | 12.536505 | 1.25163695707 | 3513.29338772 |
| ABMA | 11000 | 11000 | success | objective_budget | 1036.659013 | 1038.231082 | 0.983189014293 | 3233.83796539 |

- Validated common instance budget: `11000`.
- Recommended instance timeout: `21600` seconds.
- Collision audit was excluded from solver and algorithm timing.
- Formal execution remains unapproved.
