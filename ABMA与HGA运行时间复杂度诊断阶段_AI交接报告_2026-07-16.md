# ABMA 与 HGA 运行时间复杂度诊断阶段 AI 交接报告

## 1. 当前项目状态

当前协议 SHA-256 为 `5fb8fec80189731e702186e13567ea8cb1d16c707b06446f9ab1e705c76291dc`。正式求解器仍为 GA+ACO、Paper-Aligned-HGA-XCut-Control 与 ABMA-Legacy-Exact-Fast-v1；DE+LKH 仍排除。`formal_run_approved=false`、`user_approved_formal_execution=false`，正式 270 次矩阵尚未执行。

本轮只新增 `diagnostics/abma_hga_runtime_scaling/` 下的独立诊断脚本、原始诊断输出、统计表、图与报告，没有修改 ABMA、HGA、问题核心、归一化、冻结 profile、协议算法参数或 formal manifest。

## 2. 诊断运行

- 正常：w30/w45/w60 × HGA/ABMA × seeds 42/43/44 × budget 500，共 18 个顺序冷进程。
- cProfile：w30/w45/w60 × HGA/ABMA × seed 42 × budget 100，共 6 个独立进程。
- 实际 Pilot 路线微基准：evaluator 构造、exact DP、construct+miss、route-cache hit。
- 无预热、无碰撞后处理、无 early stopping、无正式排名；所有原始输出位于 `diagnostics/abma_hga_runtime_scaling/protocol_5fb8fec80189731e702186e13567ea8cb1d16c707b06446f9ab1e705c76291dc/`。

## 3. 核心结果

正常平均秒/Primary：

| solver | w30 | w45 | w60 |
|---|---:|---:|---:|
| ABMA | 0.113341 | 0.275308 | 0.899707 |
| HGA | 0.000790 | 0.002106 | 0.001831 |

ABMA 的 route eval/Primary 为 6,652.98→17,199.00→33,069.49；DP/Primary 为 3,866.55→13,003.14→27,286.06；transition entries/Primary 为 1,430.36→3,207.48→5,435.08。HGA 对应 route eval 为 5.63→7.61→10.32、DP 为 2.58→4.45→7.29，且不构建每候选完整转移矩阵。

w60 ABMA 特别慢的直接证据：Pilot 最终分区 `m=[20,12,19,22]`、`Σm²=1,389`、`Σm³=27,235`、split=13；profile route-cache miss 率 82.52%；380.68 s outer profile 中 regret insertion 354.35 s、Direction DP 275.27 s（嵌套累计）。

## 4. 原因结论

ABMA 的平方转移预计算、随路线增长的 regret insertion 候选、cache-miss Direction DP 与固定 80 ALNS/3 VND 相互放大；外层 Primary count 只在 partition 候选入口增加，内部数千至数万次路线工作不追加预算。HGA 的可行完整局部候选本身消耗 Primary，且使用跨候选 assignment/route cache，没有固定 80-ALNS 嵌套，因此单位 Primary 更轻。

算法机制占比采用不重叠的顶层近似：w60 ABMA 的 ALNS+VND 为 outer profile 的 97.2%。Direction DP 为 72.3%，但与 ALNS/regret 重叠。可直接隔离的通用 Python 数据处理下界约 2.6%；exact-fast Python 函数自耗时 57.5%，它同时承载算法 DP，不能作为可消除实现浪费，也不能与 97.2% 相加。HGA 小预算全进程约 64–82% 是启动/导入差额，但算法内部仍比 ABMA 轻 131–491 倍。

## 5. 经验模型及限制

正常 500-budget 三点按原始 n 拟合：ABMA `p=2.936, log-R²=0.968`；HGA `p=1.291, log-R²=0.720`。长 Pilot 单独拟合为 ABMA `p=3.660`、HGA `p=0.509`。只允许称为描述性经验指数：样本只有三个、空间分布不同，w60 同时改变分割/不均衡/miss 率，且短诊断与长 Pilot 的缓存成熟度不同。

## 6. 报告与数据入口

- 综合报告：`diagnostics/abma_hga_runtime_scaling/ABMA_HGA_RUNTIME_DIAGNOSIS_REPORT.md`
- 论文草稿：`diagnostics/abma_hga_runtime_scaling/COMPUTATIONAL_EFFICIENCY_ANALYSIS_DRAFT.md`
- 调用图：`ABMA_PRIMARY_EVALUATION_CALL_GRAPH.md`、`HGA_PRIMARY_EVALUATION_CALL_GRAPH.md`
- 复杂度审计：`ABMA_RUNTIME_COMPLEXITY_AUDIT.md`、`HGA_RUNTIME_COMPLEXITY_AUDIT.md`
- 表：`runtime_scaling_summary.csv`、`runtime_scaling_model.json`、`instance_runtime_structure.csv`、三个 work-count/amplification CSV。
- 图：7 个指定 PNG 均已生成。

向新 AI 上传时，最小核心集合为：本交接报告、`UNIFIED_EXPERIMENT_PROTOCOL.json`、`abma_final_profile.json`、`abma_final_profile.sha256`、两份 solver 源码、`mrta_problem_core.py`、`objective_normalization.py`、综合诊断报告、三个统计 CSV（runtime summary、instance structure、work amplification）与 `runtime_scaling_model.json`。若要复核原始证据，再上传整个 protocol-hash 诊断目录和 w30/w45/w60 的 Pilot `result.json`。

## 7. 测试和完整性

- 110/110 unittest 通过。
- protocol validation：valid=true。
- unified runner self-check：passed。
- 保护文件诊断前后 SHA-256 完全一致：

| file | SHA-256 |
|---|---|
| MRTA_ABMA.py | `7f6f09a030543f6e4e14d2bd837791c90896da3bb1e395f409c33d6eaef20df9` |
| MRTA_HGA_PAPER_ALIGNED_CONTROL.py | `db2557a2c96606f0b7f933808f9efb6b26468240073405697aa54304be26f2e7` |
| mrta_problem_core.py | `92cd6da3c50ee7bcbbf029134ddd371dd876370f829f6e11205a8175cf1371b5` |
| objective_normalization.py | `7465a04016897a4a51847bcae8b04c704bab01269220aaa21e0efeef99b18a83` |
| abma_final_profile.json | `631b2d675e7531d04a3c019cc8013158223944074da3e42d489a5745addee1d3` |
| abma_final_profile.sha256 | `63d8d943691164c9251206f4c09e44c32cde455738d3347ed33ca0a4172a50f3` |
| UNIFIED_EXPERIMENT_PROTOCOL.json | `5fb8fec80189731e702186e13567ea8cb1d16c707b06446f9ab1e705c76291dc` |
| formal_experiment_manifest.json | `c4f3e53e87a13c151cfebe77ef9de6398e6038638f18087892b6958444cc877e` |

## 8. 未解决问题

- 仅三种实例规模，不能证明渐近复杂度。
- cProfile budget100 主要停在 HGA 初始化和冷缓存阶段，不能代表长运行阶段占比。
- 尚未做多硬件复现、进程 CPU affinity 固定或系统噪声控制。
- 微基准未单独覆盖 regret 单任务插入、HGA 完整 individual hit/miss 的全部七项候选测试；当前调用计数和 profile 已足以支撑主要结论，但这三项可在后续只读阶段补充。
- 诊断结果不授权算法优化、预算选择或正式实验执行。
