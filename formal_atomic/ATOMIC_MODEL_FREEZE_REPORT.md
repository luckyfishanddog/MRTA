# 固定原子焊缝科学模型冻结报告

- 冻结时间：`2026-07-16T13:20:59.401486+00:00`
- 模型：`atomic_weld_partition_routing_v1`
- model freeze manifest SHA-256：`27e1a38c668621d830cbec6d3f7af0a0dddcb79a7d0765c673b37fc7f51002c8`
- normalization SHA-256：`031cfa7c8fe1668e38c463f6142807d09a3f159d6c627f2cf8d5164c4aaf4dcb`
- y=6 先切分，随后按水平投影执行 Lmax=5 m/Lmin=1 m 预切分；搜索期间禁止再次切分。
- 开放路线、无初始定位/返回、setup/post=0、精确二状态方向 DP、每实例独立归一化。
- 碰撞审计是独立后处理，不进入搜索目标。
