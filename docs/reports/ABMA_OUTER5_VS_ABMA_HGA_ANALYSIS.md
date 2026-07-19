# ABMA Outer5 vs ABMA20 / HGA Analysis

Decision case: **B (quality-loss branch)**.

固定 5 代过度削弱外层分区搜索，不能作为主算法。

ABMA1 still beats HGA-budget72 in median fitness on all three instances, so the HGA-loss clause of the stated Case B is not met. The rejection is instead driven by large ABMA20 quality contributions after generation 5. Case C is also not selected overall because the fitness advantage over HGA72 is not uniformly small, although ABMA remains dramatically slower.

- Overall median ABMA20/ABMA1 wall speedup: `2.2765470300695343`
- Median ABMA1 fitness degradation vs ABMA20: `0.16173118216168866`
- ABMA20 final best within generation 5: `0/9`
- Overall median ABMA1/HGA72 wall ratio: `24.40553972959526`

## Per-instance trade-off

| instance | ABMA20/ABMA1 median wall speedup | ABMA1 fitness degradation median | makespan degradation median | load degradation median | idle degradation median | ABMA1/HGA72 median wall ratio | ABMA1 vs HGA72 fitness difference median | ABMA20/HGA252 median wall ratio | ABMA20 vs HGA252 fitness difference median |
|---|---|---|---|---|---|---|---|---|---|
| w30 | 1.9686598171044427 | 0.26203951542033604 | 0.017089585316638553 | 1.3806354896982416 | -0.07460538770763477 | 9.04129323322616 | -2.6561825709244156 | 16.42817825300965 | -2.8766067584406363 |
| w45 | 1.99948105558956 | 0.0644812744949921 | 0.006992268837657799 | 0.20335216787998675 | -0.038129994893080485 | 24.40553972959526 | -0.26153494501694663 | 57.35040021322359 | -0.32416046444399504 |
| w60 | 2.3814231387730445 | 0.18421996033519594 | 0.049439356249847626 | 0.37789703180345796 | -0.06900962811811998 | 60.87366674777677 | -0.10352004046575392 | 121.60655474016353 | -0.29761303979422193 |

The fitness loss is mainly associated with much worse load imbalance (especially w30/w60) and a smaller makespan penalty; idle distance often improves because the weighted objective trades it against makespan/load.

All 9 ABMA20 final incumbents appeared after generation 5 (generations 16–20). Remaining improvement after generation 5 ranged from about 5.66% to 38.56%, so the final 15 generations were not redundant in these development runs.

## Historical high-budget context

| instance | solver | seed | primary_budget | fitness | solver_wall_clock_time_s | compatible_for_historical_context | comparison_policy |
|---|---|---|---|---|---|---|---|
| w30 | Paper-Aligned-HGA-XCut-Control | 42 | 16000 | 1.2852019502428496 | 12.913796899956651 | True | historical high-budget context only; excluded from ABMA1 paired statistics |
| w30 | ABMA | 42 | 16000 | 0.7636335952485163 | 112.38707160006743 | True | historical high-budget context only; excluded from ABMA1 paired statistics |
| w45 | Paper-Aligned-HGA-XCut-Control | 42 | 16000 | 1.163853433644039 | 18.424801800050773 | True | historical high-budget context only; excluded from ABMA1 paired statistics |
| w45 | ABMA | 42 | 16000 | 0.9660429077456851 | 255.05572309996933 | True | historical high-budget context only; excluded from ABMA1 paired statistics |
| w60 | Paper-Aligned-HGA-XCut-Control | 42 | 11000 | 1.2516369570717083 | 12.536505199968815 | True | historical high-budget context only; excluded from ABMA1 paired statistics |
| w60 | ABMA | 42 | 11000 | 0.9831890142931694 | 1038.2310815000674 | True | historical high-budget context only; excluded from ABMA1 paired statistics |

Historical 11000/16000-Primary rows are quality/time context only and do not enter any paired statistic above.
