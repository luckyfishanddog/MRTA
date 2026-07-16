# Cross-Instance Budget Recommendation

This report presents two validated execution-budget options. It does not choose between them and does not authorize formal experiments.

| Instance | Validated common budget | Maximum observed solver wall time (s) | Per-instance timeout (s) |
|---|---:|---:|---:|
| w30 | 16000 | 3864.625866 | 21600 |
| w45 | 16000 | 10019.285029 | 21600 |
| w60 | 11000 | 13669.840594 | 21600 |

## Option A: one global budget

Use `11000` complete-system objective evaluations for every solver and every instance. This is the minimum validated instance budget, rounded down to 1000. It is operationally simpler and maximally conservative across the three sizes.

## Option B: per-instance budgets

Use `16000` on w30, `16000` on w45, and `11000` on w60. All three official solvers receive the same budget within an instance. This preserves the higher validated budget where it passed while retaining paired within-instance fairness.

## Timeout options

The frozen rule is `ceil_to_300(max(21600, 1.5 × maximum observed solver wall time + 300))`. It yields `21600` seconds for w30, w45, and w60. Therefore both the global-timeout option and the per-instance-timeout option are numerically `21600` seconds in this calibration.

## Decision boundary

The user must select budget option A or B before formal execution. `formal_run_approved` and `user_approved_formal_execution` remain false. No Pilot result is used for an algorithm-quality ranking.
