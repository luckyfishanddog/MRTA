# W45 Scale Calibration Report

This is a budget/timing Pilot only. It contains no algorithm-quality ranking.

| Solver | Requested | Realized | Status | Stop | Algorithm s | Solver wall s | Fitness | Makespan |
|---|---:|---:|---|---|---:|---:|---:|---:|
| GA+ACO | 16000 | 16000 | success | objective_budget | 10016.655254 | 10019.285029 | 0.975926585128 | 1977.67616573 |
| Paper-Aligned-HGA-XCut-Control | 16000 | 16000 | success | objective_budget | 13.055612 | 18.424802 | 1.16385343364 | 2050.9363083 |
| ABMA | 16000 | 16000 | success | objective_budget | 253.532452 | 255.055723 | 0.966042907746 | 1974.65768572 |

- Validated common instance budget: `16000`.
- Recommended instance timeout: `21600` seconds.
- Collision audit was excluded from solver and algorithm timing.
- Formal execution remains unapproved.
