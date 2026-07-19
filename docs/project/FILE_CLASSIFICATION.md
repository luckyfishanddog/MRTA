# 项目文件归类与用途

## 1. `src/`：算法与运行代码

### 求解算法

| 文件 | 用途 |
|---|---|
| `MRTA_GA_ACO.py` | GA+ACO 基线算法，负责区域边界、任务分配和焊缝顺序联合搜索。 |
| `MRTA_DE_LKH.py` | 历史 DE+LKH 求解器，保留用于旧实验复现，不属于当前正式算法集合。 |
| `MRTA_HGA_PAPER_ALIGNED_CONTROL.py` | 论文对齐的 HGA 控制算法及命令行入口。 |
| `MRTA_HGA_ATOMIC_CONTROL.py` | 面向固定原子焊缝表示的 HGA 对照算法。 |
| `MRTA_ABMA.py` | 主 ABMA 算法，包含 SHADE 外层、ALNS/VND 内层、精确方向解码和等价加速。 |
| `MRTA_ABMA1.py` | ABMA 外层固定 5 代的受控消融包装器。 |
| `MRTA_ABMA_FIXED_PRESPLIT.py` | 固定预切分、连通端点零空走版本的 ABMA 实现。 |
| `MRTA_EPRK_MA.py` | EPRK-MA 随机键联合优化算法。 |
| `tiny_atomic_exact_solver.py` | 小规模原子实例精确求解器，用于正确性基准。 |

### 问题模型与公共能力

| 文件 | 用途 |
|---|---|
| `generate_welds.py` | 从 Excel/冻结实例读取、生成、筛选和导出焊缝数据。 |
| `mrta_problem_core.py` | 通用运动模型、焊缝方向、路径时间和区域分配基础函数。 |
| `atomic_problem_core.py` | 原子焊缝数据结构、事件边界和科学模型序列化。 |
| `atomic_weld_preprocessor.py` | 将原始焊缝预处理为固定原子焊缝实例。 |
| `atomic_route_evaluator.py` | 原子焊缝路线、方向和目标值评价。 |
| `weld_fixed_presplit.py` | 固定预切分规则、边界候选和任务分配实现。 |
| `objective_normalization.py` | 三目标理想值、基准值、尺度和归一化目标计算。 |
| `legacy_normalization_compatibility.py` | 旧归一化浮点差异的兼容与豁免校验。 |
| `solver_output_metrics.py` | 统一四机器人指标、轨迹和结果 JSON/CSV 结构。 |
| `collision_aware_schedule.py` | 独立碰撞审计和等待时间后处理。 |
| `route_local_search.py` | 路线局部搜索算子，主要服务历史 DE+LKH。 |
| `project_paths.py` | 当前目录路径常量和旧协议路径兼容解析。 |
| `__init__.py` | 标识 `src` 代码目录。 |

### 实验运行与编排

| 文件 | 用途 |
|---|---|
| `run_unified_experiments.py` | 三算法统一实验、预算矩阵、恢复执行、碰撞审计和汇总主入口。 |
| `run_fixed_presplit_experiments.py` | 固定预切分方案的预处理、等价性、运行时间和条件实验。 |
| `run_eprk_development.py` | EPRK 参数竞赛、开发集、等时、留出集和消融实验。 |
| `run_eprk_atomic_formal.py` | EPRK 与 Atomic-HGA 正式实验的顺序、恢复和交叉验证入口。 |
| `run_atomic_formal_smoke.py` | 原子正式实验前的 Smoke 运行。 |
| `run_atomic_formal_preflight.py` | 正式实验的资源、哈希、协议和失败关闭预检。 |
| `run_abma_outer5_cold_entry.py` | ABMA 外层 5 代对比实验的冷进程单次入口。 |
| `run_abma_outer5_comparison.py` | ABMA1、ABMA20 与 HGA 的完整对比和统计汇总。 |
| `orchestrate_eprk_hga_formal_background.py` | 后台启动并记录 EPRK/HGA 正式实验流程。 |
| `prepare_eprk_atomic_formal.py` | 构建原子正式协议、碰撞策略和实验清单。 |
| `finalize_eprk_hga_formal_execution.py` | 串联重算、审计、归档、测试与报告的收尾流程。 |
| `finalize_protocol_26.py` | 完成统一协议 2.6 的门禁、预算与清单字段。 |
| `migrate_protocol_26.py` | 从旧协议迁移到 2.6 并保存历史协议。 |

### 构建、分析和审计

| 文件 | 用途 |
|---|---|
| `build_atomic_normalization.py` | 生成原子模型归一化配置。 |
| `build_fixed_presplit_normalization.py` | 生成固定预切分归一化配置和基线表。 |
| `build_repository_inventory.py` | 生成仓库文件、哈希、重复项和引用清单。 |
| `generate_instance_candidates.py` | 生成和筛选不同规模的焊缝实例候选。 |
| `generate_atomic_benchmark_suite.py` | 构建原子基准实例套件及其元数据。 |
| `analyze_atomic_formal_smoke.py` | 分析原子 Smoke 输出和资源表现。 |
| `analyze_eprk_hga_formal_results.py` | 汇总 EPRK/HGA 正式结果、配对统计和结论。 |
| `audit_formal_result_recomputation.py` | 从原始结果重新计算关键指标并核对。 |
| `audit_legacy_normalization_drift.py` | 审计旧协议归一化浮点漂移。 |
| `formal_atomic_integrity.py` | 生成和比较正式阶段受保护文件哈希快照。 |
| `formal_execution_authorization.py` | 验证正式执行授权和失败关闭条件。 |
| `freeze_eprk_atomic.py` | 冻结 EPRK 画像、原子模型和相关哈希清单。 |
| `archive_formal_raw_results.py` | 打包正式原始结果并生成归档索引。 |
| `generate_eprk_hga_formal_reports.py` | 根据正式结果生成报告和交接文档。 |
| `publish_eprk_hga_formal_results.py` | 检查待发布材料并生成发布正文。 |

### 论文维护

| 文件 | 用途 |
|---|---|
| `update_word_abma_section.py` | 更新论文中的 ABMA 方法和实验描述。 |
| `update_paper_protocol25_docx.py` | 将协议 2.5 内容写入论文并生成备份。 |
| `update_paper_protocol26_docx.py` | 将协议 2.6 内容写入论文并生成备份。 |

## 2. `tests/`：自动化测试

| 文件 | 用途 |
|---|---|
| `conftest.py` | 将 `src/` 加入测试导入路径。 |
| `test_abma_exact_fast_equivalence.py` | 验证 ABMA exact-fast 与参考实现等价。 |
| `test_abma_incremental_multifidelity.py` | 验证历史增量/多保真 ABMA 行为。 |
| `test_abma_fixed_presplit_equivalence.py` | 验证固定预切分参考版与快速版一致。 |
| `test_fixed_presplit_welds.py` | 验证固定预切分长度、方向和父焊缝守恒。 |
| `test_fixed_presplit_boundaries.py` | 验证合法边界候选和唯一分配。 |
| `test_fixed_presplit_zero_travel.py` | 验证连通端点零空走和缓存。 |
| `test_fixed_presplit_protocol.py` | 验证固定预切分协议门禁和文件哈希。 |
| `test_mrta_abma1_outer5.py` | 验证 ABMA1 只允许 5 代并保持受控语义。 |
| `test_hga_abma_budget_regression.py` | 回归 HGA/ABMA 预算语义。 |
| `test_hga_paper_aligned.py` | 验证论文对齐 HGA 参数、算子和配置加载。 |
| `test_hga_atomic_control.py` | 验证 Atomic-HGA 预算、哈希和复现性。 |
| `test_eprk_budget_semantics.py` | 验证 EPRK 主预算计数。 |
| `test_eprk_decoder.py` | 验证随机键到原子任务方案的解码。 |
| `test_eprk_local_search.py` | 验证 EPRK 局部搜索和增量复用。 |
| `test_eprk_random_keys.py` | 验证随机键排序、重编码和表型键。 |
| `test_eprk_reproducibility.py` | 验证相同种子和预算得到相同解。 |
| `test_eprk_tiny_exact.py` | 用小规模精确解检查 EPRK。 |
| `test_atomic_boundary_events.py` | 验证原子合法边界事件。 |
| `test_atomic_route_evaluator.py` | 验证原子路线评价。 |
| `test_atomic_weld_preprocessor.py` | 验证原子焊缝预处理。 |
| `test_collision_postprocess.py` | 验证碰撞检测、等待插入和原始结果不变。 |
| `test_formal_execution_authorization.py` | 验证正式授权文件和失败关闭。 |
| `test_frozen_instances.py` | 验证冻结实例文件和元数据。 |
| `test_legacy_normalization_compatibility.py` | 验证旧归一化兼容范围。 |
| `test_objective_normalization.py` | 验证目标归一化公式和证据。 |
| `test_solver_output_metrics.py` | 验证各求解器使用统一输出结构。 |
| `test_unified_protocol_v2.py` | 验证统一协议 2.6、算法集合、预算和门禁。 |
| `test_analyze_eprk_hga_formal_results.py` | 验证正式结果统计与资格判定。 |
| `tests/fixtures/*.xlsx` | 生成焊缝平台时使用的测试工作簿。 |

## 3. `config/`：配置与协议

| 子目录 | 文件与用途 |
|---|---|
| `protocols/` | `UNIFIED_EXPERIMENT_PROTOCOL*.json` 为统一/固定预切分协议；`EPRK_MA_DEVELOPMENT_PROTOCOL.json` 为 EPRK 开发协议；`formal_experiment_manifest.*` 为正式运行矩阵。 |
| `profiles/` | `abma_final_profile*` 为冻结 ABMA 画像；`eprk_ma_candidate_profile*` 为 EPRK 候选画像；`eprk_ma_defaults.json` 和 `paper_aligned_hga_control_defaults.json` 为算法默认参数。 |
| `normalization/` | 原子模型和固定预切分的归一化 JSON、校验和及基线 CSV。 |
| `budgets/` | `cross_instance_budget_options.json` 保存跨规模预算候选。 |
| `manifests/` | fixed-presplit 哈希清单和仓库清理清单。 |

## 4. `results/`：汇总结果

| 子目录 | 文件用途 |
|---|---|
| `abma/` | ABMA 候选、等价性、验证、画像函数/阶段时间和汇总。 |
| `atomic/` | 原子边界事件、预处理摘要和微型精确解。 |
| `eprk/` | 参数竞赛、开发、等时、留出、消融和工作放大结果。 |
| `fixed_presplit/` | 边界候选、预处理、等价性、运行时间、工作放大和条件实验汇总。 |
| `calibration/` | 碰撞审计、三算法 W30 Pilot、W45/W60 预检与规模校准。 |
| `inventory/` | 整理前后的仓库完整清单。 |

## 5. `docs/`：文档

| 子目录 | 文件用途 |
|---|---|
| `reports/` | 算法候选、实现、运行时间、独立验证、消融、碰撞与规模校准报告。 |
| `specifications/` | 原子焊缝、fixed-presplit、EPRK 方法、伪代码和论文方法草稿。 |
| `plans/` | 正式实验执行计划和跨实例预算建议。 |
| `handoffs/` | 各开发阶段的 AI 交接报告。 |
| `project/` | 项目总览、清理记录、后续计划和本归类说明。 |
| `thesis/` | 论文文档目录；`drafts/` 保存独立草稿。主论文将在 Word 关闭后移入此处，目前因文件被占用而暂留项目根目录。 |

## 6. 保持原目录的实验证据

- `formal_atomic/`、`unified_experiments/`、`experiments/` 中的协议快照、命令、日志和结果保持原有内部结构。
- `diagnostics/` 和 `eprk_development_outputs/` 保持一次诊断/开发实验的程序与证据链不被拆散。
- 这些目录中的 Markdown 属于某次运行的随附证据，不移动到项目级 `docs/`。

## 7. `config/` 全量文件索引

| 文件 | 用途 |
|---|---|
| `budgets/cross_instance_budget_options.json` | W30/W45/W60 跨规模正式预算候选及超时上限。 |
| `manifests/fixed_presplit_hash_manifest.json` | fixed-presplit 协议、源码、测试和结果的完整哈希清单。 |
| `manifests/repository_cleanup_manifest.json` | 早期仓库清理范围及文件处置记录。 |
| `normalization/atomic_normalization_baselines.csv` | 原子模型各规模归一化基线明细。 |
| `normalization/atomic_normalization_spec.json` | 原子模型三目标归一化规范。 |
| `normalization/atomic_normalization_spec.json.sha256` | 归一化规范的兼容命名校验和。 |
| `normalization/atomic_normalization_spec.sha256` | 原子归一化规范的标准校验和文件。 |
| `normalization/fixed_presplit_normalization.json` | fixed-presplit 三目标归一化参数。 |
| `normalization/fixed_presplit_normalization.sha256` | fixed-presplit 归一化配置校验和。 |
| `normalization/fixed_presplit_normalization_baselines.csv` | fixed-presplit 各实例归一化基线。 |
| `profiles/abma_final_profile.json` | 冻结的 ABMA 正式候选参数与实现画像。 |
| `profiles/abma_final_profile.sha256` | ABMA 冻结画像校验和。 |
| `profiles/eprk_ma_candidate_profile.json` | EPRK-MA 候选参数与冻结元数据。 |
| `profiles/eprk_ma_candidate_profile.json.sha256` | EPRK 候选画像的兼容命名校验和。 |
| `profiles/eprk_ma_candidate_profile.sha256` | EPRK 候选画像的标准校验和。 |
| `profiles/eprk_ma_defaults.json` | EPRK-MA 默认运行参数。 |
| `profiles/paper_aligned_hga_control_defaults.json` | 论文对齐 HGA 的默认参数。 |
| `protocols/EPRK_MA_DEVELOPMENT_PROTOCOL.json` | EPRK 开发、竞赛、等时和留出实验协议。 |
| `protocols/EPRK_MA_DEVELOPMENT_PROTOCOL.sha256` | EPRK 开发协议校验和。 |
| `protocols/formal_experiment_manifest.csv` | 正式实验运行矩阵的表格版。 |
| `protocols/formal_experiment_manifest.json` | 正式实验运行矩阵及机器可读元数据。 |
| `protocols/UNIFIED_EXPERIMENT_PROTOCOL.json` | 三算法统一实验主协议。 |
| `protocols/UNIFIED_EXPERIMENT_PROTOCOL_FIXED_PRESPLIT_DEV.json` | fixed-presplit 开发实验协议。 |
| `protocols/UNIFIED_EXPERIMENT_PROTOCOL_FIXED_PRESPLIT_DEV.sha256` | fixed-presplit 协议校验和。 |

## 8. `results/` 全量文件索引

| 文件 | 用途 |
|---|---|
| `abma/abma_candidate_development_results.csv` | ABMA 候选开发结果。 |
| `abma/abma_development_benchmark.csv` | ABMA 开发阶段基准运行记录。 |
| `abma/abma_exact_equivalence_results.csv` | ABMA 精确参考与优化实现等价性结果。 |
| `abma/abma_exact_fast_development_results.csv` | exact-fast 开发运行结果。 |
| `abma/abma_profile_functions.txt` | ABMA 函数级性能热点摘要。 |
| `abma/abma_profile_stage_times.csv` | ABMA 各阶段耗时明细。 |
| `abma/abma_profile_summary.json` | ABMA 性能画像汇总。 |
| `abma/abma_validation_benchmark.csv` | ABMA 独立验证基准。 |
| `abma/abma_validation_runs.csv` | ABMA 独立验证逐次运行结果。 |
| `abma/abma_validation_summary.json` | ABMA 独立验证汇总结论。 |
| `atomic/atomic_boundary_events_summary.csv` | 原子实例合法边界事件摘要。 |
| `atomic/atomic_preprocessing_summary.csv` | 原子焊缝预处理统计。 |
| `atomic/tiny_atomic_exact_results.json` | 微型原子实例精确解基准。 |
| `calibration/collision_audit_pilot_runs.csv` | 碰撞后处理 Pilot 逐次审计结果。 |
| `calibration/collision_audit_pilot_summary.json` | 碰撞审计 Pilot 汇总。 |
| `calibration/three_solver_w30_checkpoint_trace.csv` | W30 三算法检查点轨迹。 |
| `calibration/three_solver_w30_pilot_runs.csv` | W30 三算法 Pilot 运行结果。 |
| `calibration/three_solver_w30_pilot_summary.json` | W30 三算法 Pilot 汇总。 |
| `calibration/three_solver_w30_preflight_runs.csv` | W30 三算法预检结果。 |
| `calibration/w45_scale_pilot_runs.csv` | W45 规模 Pilot 运行记录。 |
| `calibration/w45_scale_pilot_summary.json` | W45 Pilot 汇总与预算外推。 |
| `calibration/w45_scale_preflight_runs.csv` | W45 规模预检运行记录。 |
| `calibration/w45_scale_preflight_summary.json` | W45 预检汇总。 |
| `calibration/w60_scale_pilot_runs.csv` | W60 规模 Pilot 运行记录。 |
| `calibration/w60_scale_pilot_summary.json` | W60 Pilot 汇总与预算外推。 |
| `calibration/w60_scale_preflight_runs.csv` | W60 规模预检运行记录。 |
| `calibration/w60_scale_preflight_summary.json` | W60 预检汇总。 |
| `eprk/eprk_ablation_runs.csv` | EPRK 组件消融结果。 |
| `eprk/eprk_development_runs.csv` | EPRK 开发集运行结果。 |
| `eprk/eprk_equal_time_runs.csv` | EPRK 等时间预算结果。 |
| `eprk/eprk_holdout_runs.csv` | EPRK 留出集验证结果。 |
| `eprk/eprk_parameter_racing_results.csv` | EPRK 参数竞赛结果。 |
| `eprk/eprk_work_amplification.csv` | EPRK 辅助工作放大统计。 |
| `fixed_presplit/fixed_presplit_boundary_candidates.json` | fixed-presplit 合法边界候选集合。 |
| `fixed_presplit/fixed_presplit_conditional_2000_summary.csv` | 2000 预算条件实验汇总。 |
| `fixed_presplit/fixed_presplit_equivalence_results.csv` | fixed-presplit 参考版/快速版等价性。 |
| `fixed_presplit/fixed_presplit_preprocessing_summary.csv` | fixed-presplit 预处理统计。 |
| `fixed_presplit/fixed_presplit_runtime_runs.csv` | fixed-presplit 运行时间逐次记录。 |
| `fixed_presplit/fixed_presplit_runtime_summary.json` | fixed-presplit 运行时间汇总。 |
| `fixed_presplit/fixed_presplit_work_amplification.csv` | fixed-presplit 工作放大统计。 |
| `inventory/repository_inventory_before.json` | 整理前仓库文件清单。 |
| `inventory/repository_inventory_after.json` | 早期清理后的仓库文件清单，用于历史对照。 |

## 9. `docs/` 全量文件索引

### 报告、规范与计划

| 文件 | 用途 |
|---|---|
| `reports/ABMA_FINAL_CANDIDATE_REPORT.md` | ABMA 最终候选冻结报告。 |
| `reports/ABMA_FIXED_PRESPLIT_IMPLEMENTATION_REPORT.md` | fixed-presplit ABMA 实现说明。 |
| `reports/ABMA_FIXED_PRESPLIT_RUNTIME_DIAGNOSIS_REPORT.md` | fixed-presplit 运行时间诊断。 |
| `reports/ABMA_INDEPENDENT_VALIDATION_REPORT.md` | ABMA 独立验证报告。 |
| `reports/ABMA_OUTER5_EXPERIMENT_REPORT.md` | ABMA 外层 5 代实验结果。 |
| `reports/ABMA_OUTER5_IMPLEMENTATION_REPORT.md` | ABMA1 受控包装实现说明。 |
| `reports/ABMA_OUTER5_VS_ABMA_HGA_ANALYSIS.md` | ABMA1、ABMA20、HGA 对比分析。 |
| `reports/ABMA_STRUCTURAL_ACCELERATION_REPORT.md` | ABMA 结构性等价加速报告。 |
| `reports/COLLISION_AUDIT_POLICY_REPORT.md` | 碰撞审计策略与证据。 |
| `reports/EPRK_MA_ABLATION_REPORT.md` | EPRK 组件消融报告。 |
| `reports/EPRK_MA_DEVELOPMENT_REPORT.md` | EPRK 开发实验报告。 |
| `reports/THREE_SOLVER_W30_BUDGET_PILOT_REPORT.md` | W30 三算法预算 Pilot 报告。 |
| `reports/THREE_SOLVER_W30_PILOT_REPORT.md` | W30 三算法运行 Pilot 报告。 |
| `reports/W45_SCALE_CALIBRATION_REPORT.md` | W45 规模校准报告。 |
| `reports/W60_SCALE_CALIBRATION_REPORT.md` | W60 规模校准报告。 |
| `specifications/ATOMIC_WELD_MODEL_SPEC.md` | 原子焊缝科学模型规范。 |
| `specifications/EPRK_MA_METHOD_SPEC.md` | EPRK-MA 方法规范。 |
| `specifications/EPRK_MA_PAPER_METHOD_DRAFT.md` | EPRK 论文方法段草稿。 |
| `specifications/EPRK_MA_PSEUDOCODE.md` | EPRK 算法伪代码。 |
| `specifications/FIXED_PRESPLIT_MODEL_SPECIFICATION.md` | 固定预切分任务模型规范。 |
| `plans/CROSS_INSTANCE_BUDGET_RECOMMENDATION.md` | 跨规模预算选择建议。 |
| `plans/FORMAL_EXPERIMENT_EXECUTION_PLAN.md` | 正式实验执行计划。 |

### 项目说明与交接

| 文件 | 用途 |
|---|---|
| `project/README.md` | 整理后的项目入口、目录和运行方式。 |
| `project/FILE_CLASSIFICATION.md` | 当前全量归类及文件用途索引。 |
| `project/REPOSITORY_CLEANUP_PLAN.md` | 早期仓库清理计划。 |
| `project/REPOSITORY_CLEANUP_REPORT.md` | 早期仓库清理执行报告。 |
| `project/下一步计划.md` | 当前后续工作计划。 |
| `project/仓库清理_ABMA独立验证与三算法预算Pilot阶段_AI交接报告_2026-07-15.md` | 仓库清理与预算 Pilot 阶段交接。 |
| `project/当前完整工作总览与新对话交接报告_2026-07-16.md` | 项目全局状态与新会话交接。 |
| `handoffs/ABMA与HGA运行时间复杂度诊断阶段_AI交接报告_2026-07-16.md` | ABMA/HGA 性能诊断阶段交接。 |
| `handoffs/ABMA固定预切分任务表示与零空走转移开发阶段_AI交接报告_2026-07-16.md` | fixed-presplit 开发阶段交接。 |
| `handoffs/ABMA外层5代消融及与原ABMA_HGA对比阶段_AI交接报告_2026-07-19.md` | ABMA 外层 5 代对比阶段交接。 |
| `handoffs/ABMA结构性等价加速与开发门禁复验阶段_AI交接报告_2026-07-15.md` | ABMA 等价加速阶段交接。 |
| `handoffs/EPRK-MA固定原子焊缝联合优化开发阶段_AI交接报告_2026-07-16.md` | EPRK 原子模型开发阶段交接。 |
| `handoffs/EPRK-MA正式实验预检修复_资源校准与Smoke阶段_AI交接报告_2026-07-16.md` | EPRK 正式预检与 Smoke 阶段交接。 |
| `handoffs/w45_w60规模校准_碰撞后处理与正式实验协议冻结阶段_AI交接报告_2026-07-15.md` | W45/W60 校准与协议冻结交接。 |
| `handoffs/三算法实验重构与ABMA最终候选冻结阶段_AI交接报告_2026-07-15.md` | 三算法重构与 ABMA 冻结交接。 |
| `thesis/drafts/问题定义草稿.docx` | 论文问题定义的独立 Word 草稿。 |
