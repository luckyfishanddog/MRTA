# ABMA 结构性等价加速报告

日期：2026-07-15  
协议：`2.4.0`  
范围：仅 `w30`、开发 seeds 42–44；未运行 validation、Pilot、w45/w60 或 formal。

## 结论

`legacy_exact_fast` 已通过精确等价与开发速度门禁，冻结为 `ABMA-Legacy-Exact-Fast-v1`。它完整保留 Legacy 的 SHADE 外层搜索、每分区 80 次 ALNS、3 轮 VND、随机数流、候选生成、接受规则、方向 DP、目标函数和 2000 次完整系统评价预算。

2000 次评价的三种子速度比分别为 4.2626×、4.0550×、3.7675×，中位数 4.0550×。三种子的 fitness、makespan、load imbalance、idle distance 差异均严格为 0，候选序列、接受序列、外层 RNG、内层 RNG 和评价轴上的 incumbent trace 哈希/内容一致。

## 被拒绝的上一候选

上一候选 `incremental_multifidelity` 保持“开发拒绝”历史状态：seed 42、43 的速度比均低于 2×，且 seed 43 fitness 退化 2.3198%，超过 2% 质量上限。它没有被改写为正式主算法。

## Profiling

基线：`w30 / seed42 / legacy / budget200`，串行、无碰撞审计。共发生 1,185,400 次路线评价，其中 633,232 次路线 cache miss 触发精确方向 DP；系统目标计算 261,346 次。

cProfile 累计时间前十项：

| 排名 | 函数 | 累计秒 |
|---:|---|---:|
| 1 | `ABMASolver.solve` | 385.845 |
| 2 | `ABMASolver._raw_evaluate` | 385.827 |
| 3 | `ABMASolver._legacy_evaluate` | 385.823 |
| 4 | `optimize_partition_joint_alns` | 385.244 |
| 5 | `RouteEvaluator.evaluate` | 350.285 |
| 6 | `mrta_problem_core.evaluate_order` | 322.409 |
| 7 | `RouteEvaluator.travel_cost` | 307.059 |
| 8 | `_repair_route` | 306.619 |
| 9 | `optimize_directions_for_order` | 234.648 |
| 10 | `travel_time` | 138.119 |

阶段计时显示方向 DP 约 324.843 秒、路线评价约 330.071 秒、regret 插入约 306.578 秒；相对地，状态复制约 0.007 秒、分区分配约 0.290 秒。因此优化主线由证据确定为精确运动代价预计算与 DP 快路径，而不是删减 Legacy 搜索或把复制当作主要瓶颈。

## 实施的等价优化

1. 每个分区预计算不可变的焊缝时间。
2. 对机器人局部任务集合预计算 `(前任务, 前方向, 后任务, 后方向)` 的精确空移时间与距离。
3. 快路径方向 DP 保留 Legacy 的位置循环、方向循环、严格 `<` tie 规则、回溯顺序和浮点加法顺序。
4. 保留原分区局部 route-order tuple cache；未引入近似签名或跨模型复用。
5. 预计算 ALNS related/boundary 中心与 regret tie key。
6. 四机器人 stats/orders 容器采用写时隔离的浅复制；被替换机器人的路线与统计仍独立。
7. 官方归一化在配置已验证后走同公式、同分量顺序的快路径。
8. profiling 默认关闭；性能门禁没有 cProfile 扰动。

## 门禁结果

| 阶段 | seed | 预算 | Legacy 秒 | Exact-fast 秒 | 加速 | 精确等价 |
|---|---:|---:|---:|---:|---:|---|
| 9.1 | 42 | 100 | 64.9931 | 15.5900 | 4.1689× | 是 |
| 9.2 | 42 | 500 | 192.7673 | 48.6123 | 3.9654× | 是 |
| 9.2 | 43 | 500 | 206.8568 | 51.3403 | 4.0291× | 是 |
| 9.3 | 42 | 2000 | 450.3259 | 105.6454 | 4.2626× | 是 |
| 9.3 | 43 | 2000 | 461.7887 | 113.8816 | 4.0550× | 是 |
| 9.3 | 44 | 2000 | 430.1406 | 114.1710 | 3.7675× | 是 |

9.3 的全部运行独立重建成功、评价数严格为 2000、停止原因为 objective budget。由于 exact-fast 没有候选筛选，false rejection rate 为 `null`，不写成 0；5% 误拒门槛不适用并视为无筛选风险。

首次 9.3 汇总曾错误地把 trace 中的 `elapsed_algorithm_time` 纳入科学等价比较。只读定位证明 checkpoint 与 incumbent 科学字段完全一致，差异仅是预期的耗时缩短。runner 已修正为从科学 trace 比较中排除时钟字段，同时保留原始 elapsed 证据；原始结果未覆盖、未修改。

## 冻结与后续边界

冻结文件为 `abma_final_profile.json`，SHA-256：

`631b2d675e7531d04a3c019cc8013158223944074da3e42d489a5745addee1d3`

validation seeds 45–47 本阶段未运行；三算法 Pilot、w45/w60、formal 均未运行。`formal_run_approved` 仍为 `false`。下一阶段可以依据冻结 profile 单独申请 validation，但不能自动进入 Pilot 或 formal。

