# ABMA 固定预切分实现报告

## 实现结论

已新增独立开发算法 `ABMA-Fixed-PreSplit-Development-v1`，没有修改冻结 `MRTA_ABMA.py`。实现复用其 SHADE 外层、80 ALNS、3 VND、联合系统目标与接受逻辑，通过 adapter 替换任务表示、边界解码、整体分配和路线 evaluator。

## 新增核心文件

- `weld_fixed_presplit.py`：确定性 y/长焊缝预切分、稳定 ID、合法边界、固定整体分配。
- `MRTA_ABMA_FIXED_PRESPLIT.py`：双变体 solver、精确零转移、实例级缓存、CLI。
- `build_fixed_presplit_normalization.py`：独立理想点/baseline/range 归一化。
- `run_fixed_presplit_experiments.py`：串行冷进程、resume、等价门禁、500/2000 受控实验和汇总。
- `UNIFIED_EXPERIMENT_PROTOCOL_FIXED_PRESPLIT_DEV.json`：独立开发协议，正式批准门关闭。

## 预处理结果

| 实例 | 原始焊缝 | 固定焊缝 | y=6 父焊缝/固定段 | 长焊缝父焊缝/固定段 | 上/下合法边界 |
|---|---:|---:|---:|---:|---:|
| w30 | 30 | 31 | 1 / 2 | 0 / 0 | 27 / 9 |
| w45 | 45 | 48 | 3 / 6 | 0 / 0 | 24 / 16 |
| w60 | 60 | 74 | 3 / 6 | 1 / 12 | 27 / 6 |

w60 被长规则切分的父焊缝为 `inst0048|group=144-DK1B.dxf|row=0000`，水平跨度 12.5 m，得到 12 个约 1.0416667 m 的等参数段。三实例总长度误差均为 0；逐父焊接时间恒等式的最大浮点残差为 `1.4211e-14 s`。所有合法边界均不穿过固定焊缝，所有运行的分配完整且唯一。

## 零空走与缓存

连接端点转移在 DP 内严格返回时间/距离 0，不触发抬升下降，父身份不参与条件。reference 使用 evaluator 局部 route/pair 缓存；exact-fast 增加实例级 pair、压缩 route stats 与正反路线共享 travel scalar。缓存容量有硬上限，淘汰后精确重算。

## 等价验证

seed72、budget100 的三规模 reference/fast 均逐字段严格相等，`fixed_presplit_equivalence_results.csv` 的 `exact_equivalent=True` 且 mismatch 为空。比较覆盖：

- fitness、makespan、load imbalance、idle distance；
- 最佳 u、离散索引和实际 x；
- 四机器人固定 ID 顺序、方向 flags、完整 route stats；
- 候选序列、接受序列、outer/inner RNG 最终哈希；
- Primary 计数、partition hit/miss 和边界访问直方图。

## 测试

新增 11 项固定预切分测试全部通过。全仓 `unittest discover` 共运行 182 项：181 项通过，1 项既有 `test_unified_protocol_v2.test_protocol_exact_validator_reports_frozen_float_drift` 失败；该测试固定期待旧协议验证器报告 6 条漂移，但当前验证器实际返回 0 条。本轮未修改相关冻结协议、runner 或测试，其受保护文件哈希保持不变。

## HGA 后续 adapter（仅设计）

未来 adapter 应读取同一 fixed instance、同一离散边界索引和零转移 evaluator，保留 HGA 的论文对齐编码/交叉/变异，不复用 ABMA 的 SHADE。先做只解析不求解的 dry-run：

```powershell
D:\pybullet_test\.venv\Scripts\python.exe MRTA_HGA_FIXED_PRESPLIT_ADAPTER.py --instance data/instances/fixed_presplit/w30_fixed_presplit.json --normalization fixed_presplit_normalization.json --seed 72 --budget 100 --dry-run
```

`MRTA_HGA_FIXED_PRESPLIT_ADAPTER.py` 本轮没有创建，上述命令仅为未来接口合同，未执行。

