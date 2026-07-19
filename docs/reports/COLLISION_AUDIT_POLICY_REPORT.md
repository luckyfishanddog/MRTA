# Collision Audit Pilot Policy Report

The audit is a deterministic postprocess of frozen successful raw solutions. It is not part of optimization, evaluation budget, or algorithm ranking.

| Instance | Solver | Status | Raw makespan | Adjusted makespan | Added wait | Conflicts | Unresolved | Audit s | Raw unchanged |
|---|---|---|---:|---:|---:|---:|---:|---:|---|
| w45 | ABMA | success | 1974.6576857200725 | 1974.6576857200732 | 0.0 | 0 | 0 | 0.060440 | True |
| w45 | GA+ACO | success | 1977.6761657288857 | 1977.6761657288855 | 0.0 | 0 | 0 | 0.057644 | True |
| w45 | Paper-Aligned-HGA-XCut-Control | success | 2050.9363083048434 | 2050.936308304843 | 0.0 | 0 | 0 | 0.055900 | True |
| w60 | ABMA | success | 3233.837965387536 | 3314.8367910035463 | 81.0 | 52 | 0 | 1.646341 | True |
| w60 | GA+ACO | success | 3245.2657825721026 | 3245.2657825721017 | 0.0 | 0 | 0 | 0.098869 | True |
| w60 | Paper-Aligned-HGA-XCut-Control | success | 3513.293387716747 | 3513.2933877167466 | 0.0 | 0 | 0 | 0.097479 | True |
| w30 | ABMA | success | 1244.3268486370553 | 1298.1534825076521 | 54.0 | 15 | 0 | 0.280473 | True |
| w30 | GA+ACO | success | 1244.6099330875893 | 1244.6099330875895 | 0.0 | 0 | 0 | 0.033894 | True |
| w30 | Paper-Aligned-HGA-XCut-Control | success | 1313.8325876636752 | 1313.8325876636754 | 11.0 | 31 | 0 | 0.595228 | True |

- Raw fitness, makespan, boundaries, routes, and directions are not overwritten.
- Any failed audit or unresolved conflict is explicit and cannot be described as collision success.
- Raw and collision-adjusted metrics must be reported as separate families.
- No ranking conclusion is drawn from this Pilot.
