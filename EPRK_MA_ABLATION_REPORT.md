# EPRK-MA 小规模消融报告

前提：开发门禁通过后执行；w30、seeds 104/105、2000 Primary；不使用 holdout seed，不据此再调参。

| 变体 | 两 seed 中位 fitness | 相对 full 说明 |
|---|---:|---|
| full | 1.005604 | 基准 |
| no_event_local_search | 1.005041 | 短预算波动内略好，不支持单独必要性结论 |
| no_route_local_search | 1.013071 | 变差 0.74% |
| no_zero_continuity_initialization | 1.005390 | 接近 full |
| random_only_initialization | 1.012206 | 变差 0.66% |
| no_migration | 1.005604 | 相同；2000 Primary 内迁移尚未触发 |
| no_restart | 1.005604 | 相同；2000 Primary 内重启尚未触发 |

14/14 运行精确耗尽预算并通过独立重算。只有两个 seed，结果是机制诊断而非显著性证据。迁移/重启需要更长预算才能形成有效消融；本轮未追加，以免在 holdout 后扩大开发搜索。

