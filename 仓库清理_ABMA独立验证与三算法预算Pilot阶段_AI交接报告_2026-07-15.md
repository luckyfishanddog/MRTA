# 仓库清理、ABMA 独立验证与三算法预算 Pilot 阶段 AI 交接报告

日期：2026-07-15  
项目目录：`D:/pybullet_test/MRTA_GA`  
Python：`D:/pybullet_test/.venv/Scripts/python.exe`

## 1. 阶段结论

本阶段已按强制顺序完成：仓库一致性审计与受控清理、全量回归、ABMA seeds 45–47 独立验证、三算法 w30/seed42/2000 次预检，以及三算法 w30/seed42/16000 次统一预算 Pilot。所有实验门禁均通过。

正式算法集合严格为 `GA+ACO`、`Paper-Aligned-HGA-XCut-Control`、`ABMA-Legacy-Exact-Fast-v1`。DE+LKH 没有活动 adapter、没有进入 dry-run、validation、preflight 或 Pilot；仅保留历史源文件与历史证据。

本阶段不做算法优劣排名。`formal_run_approved=false`，`user_approved_formal_execution=false`；没有运行 w45、w60 或正式 30 seeds。

## 2. 清理前发现的问题

1. 仓库存在 20 个可再生 `.pyc` 缓存文件。
2. 统一 runner 仍包含 DE+LKH adapter 和活动注册，虽然协议正式算法集合已排除 DE+LKH。
3. 协议同时混放正式 exact-fast、legacy 参考、被拒绝的增量修复与多保真变体，正式 ABMA 入口不够唯一。
4. ABMA adapter 会向 exact-fast 命令注入只属于历史多保真方案的 low/mid/high 参数。
5. Word 第 3.2 节仍将被拒绝的增量/多保真候选作为主方法描述；正式实验算法段落重复两次。
6. 历史结果目录含不可变证据及嵌入相对路径，不适合直接搬移。
7. 未发现 `MRTA_ABMA(n).py`、`UNIFIED_EXPERIMENT_PROTOCOL(n).json` 或 `run_unified_experiments(n).py` 等活动文件编号副本。
8. 若干未知二进制文档/数据无法仅凭文件名证明可删除，按保守策略保留。

## 3. 删除、归档、重命名与保留

### 3.1 删除

仅删除 `__pycache__/` 中 20 个 `.pyc`。删除前逐个解析绝对路径并确认位于工作区内；清理后 `remaining_pyc=0`。完整列表见 `repository_cleanup_manifest.json`。

### 3.2 归档

在修改论文前，将原 Word 复制到：

`archive/stage_reports/双滑轨四悬臂机器人任务分配及焊缝排序_before_protocol_2.5.docx`

同时建立 `archive/stage_reports`、`archive/historical_protocols`、`archive/historical_experiments`、`archive/rejected_abma_variants`、`archive/excluded_de_lkh` 目录。历史实验未搬移，因为 raw evidence 内嵌现有相对路径；保持原位更利于复现。

### 3.3 重命名

没有文件重命名。

### 3.4 保留

- `MRTA_DE_LKH.py`、`route_local_search.py`：只作历史复现，活动 runner 不可选择。
- 所有冻结实例、归一化证据、ABMA profile、raw validation/Pilot 结果和阶段报告：保留审计链。
- 未知二进制文件：没有充分证据证明安全删除。
- 被拒绝 ABMA 变体的代码/测试：仅作历史复现和防回归，不属于正式算法定义。

清理证据：`REPOSITORY_CLEANUP_PLAN.md`、`REPOSITORY_CLEANUP_REPORT.md`、`repository_inventory_before.json`、`repository_inventory_after.json`、`repository_cleanup_manifest.json`。

## 4. 协议与 runner 一致性修复

- 协议升级为 `2.5.0`。
- 活动 adapter registry 只包含 `ga_aco`、`paper_aligned_hga`、`abma`。
- 唯一正式 ABMA：`ABMA-Legacy-Exact-Fast-v1` / `legacy_exact_fast`。
- 正式 ABMA 保持 SHADE 外层、ALNS=80、VND=3、精确双状态方向 DP、同一随机数流/候选/接受规则/完整系统评价预算。
- exact-fast 命令不再注入历史多保真控制参数。
- 增量修复及 low/mid/high 多保真方案迁入 `historical_rejected_abma_variants`。
- runner 强校验 profile 文件 SHA-256、`.sha256` 声明、算法/variant、80/3、正式命令以及三个科学源文件哈希。
- `MRTA_ABMA.py`、`mrta_problem_core.py`、`objective_normalization.py` 仍与冻结画像逐字节一致。画像中的 runner/test 哈希作为开发冻结时的 provenance 保留；协议 2.5 的编排和门禁测试允许演进，不能反向修改冻结画像。
- validation 汇总现在强制判定精确输出、2000 次预算、profile hash、median≥2.0 和每 seed≥1.5。
- Pilot 汇总输出统一文件名，并记录 fitness、makespan、load imbalance、total idle distance、四类时间、速率和 checkpoint trace。

## 5. Word 内容清理

当前 `双滑轨四悬臂机器人任务分配及焊缝排序.docx` 已更新：

- 第 3.2 节只把 `ABMA-Legacy-Exact-Fast-v1` 作为正式方法；
- 明确 SHADE + 完整 ALNS/VND 80/3 + 精确方向 DP；
- 结构性加速仅限精确预计算、缓存和等价快路径；
- 增量/多保真变体降为被拒绝的历史探索；
- 正式算法集合只保留三算法；
- DE+LKH 只作排除历史；
- 重复正式算法段落由 2 处减为 1 处；
- 没有写入“ABMA 优于 HGA”的结论。

后台 Word COM 未获环境批准，随后采用本地 DOCX OpenXML 包级更新；已通过 ZIP 完整性、XML 解析和正文抽取校验。修改前 Word 备份已保留。尚未进行 Microsoft Word GUI 渲染复核。

## 6. 测试命令与真实结果

执行：

```text
python run_unified_experiments.py --validate-protocol ...
python run_unified_experiments.py --self-check ...
python run_unified_experiments.py --dry-run --instances w30 --seeds 42 ...
python -m unittest discover -v
```

结果：

- 协议校验：通过；引用文件均存在。
- self-check：通过。
- dry-run：恰好 3 条正式算法命令；ABMA 为 `legacy_exact_fast`；无 DE；正式执行仍 fail closed。
- 进入 validation 前：98/98 单元测试通过。
- Pilot 后第一次最终回归：97 项通过，1 项旧门禁断言失败（仍预期 `w30_budget_pilot_completed=false`）；仅更新该测试以反映已通过的真实 Pilot 门禁。
- 修复断言后最终回归：98/98 通过，用时约 15.3 秒。

测试过程中没有运行 DE+LKH 优化算法。历史兼容测试只校验公共目标函数/输出构造路径。

## 7. ABMA 独立 validation（w30，budget=2000）

配置：seeds 45、46、47；每 seed 串行运行 `legacy` 与 `legacy_exact_fast`；无碰撞审计；无提前停止；不调参。

| Seed | Legacy fitness | Exact-fast fitness | Legacy wall s | Exact-fast wall s | Speedup | Exact | Budget |
|---:|---:|---:|---:|---:|---:|---|---|
| 45 | 0.7636026111753739 | 0.7636026111753739 | 431.051679 | 109.594442 | 3.933153× | true | 2000/2000 |
| 46 | 0.7634956731222902 | 0.7634956731222902 | 624.377763 | 162.549311 | 3.841159× | true | 2000/2000 |
| 47 | 0.7634925776698531 | 0.7634925776698531 | 555.730333 | 145.258924 | 3.825791× | true | 2000/2000 |

三个 seed 的 objective count、stop reason、边界、路线、方向、fitness、makespan、load imbalance、idle distance、candidate/acceptance hashes、outer/inner RNG hashes以及去除计时后的 checkpoint trace 均精确等价。runner reconstruction 全部成功。

- Median speedup：`3.8411590938843863`（门槛 2.0）。
- Minimum per-seed speedup：`3.825791347114875`（门槛 1.5）。
- Profile hash 全部一致。
- `abma_validation_completed=true`。

证据：`abma_validation_runs.csv`、`abma_validation_summary.json`、`ABMA_INDEPENDENT_VALIDATION_REPORT.md`；raw root 为 `unified_experiments/abma_independent_validation_2026-07-15`。

## 8. 三算法 w30 preflight（seed=42，budget=2000）

| Solver | Realized | Stop reason | Wall s | Reconstruction |
|---|---:|---|---:|---|
| GA+ACO | 2000 | objective_budget | 440.795007 | success |
| Paper-Aligned-HGA-XCut-Control | 2000 | objective_budget | 2.994634 | success |
| ABMA-Legacy-Exact-Fast-v1 | 2000 | objective_budget | 115.025742 | success |

三者均精确耗尽预算，无 DE 命令，无碰撞审计，无提前停止。按 preflight 线性估算 16000 次 wall-clock 约为 3526.36、23.96、920.21 秒；没有确定性 blocker。证据：`three_solver_w30_preflight_runs.csv`。

## 9. 三算法 16000 次统一预算 Pilot

| Solver | Fitness | Makespan | Load imbalance | Idle distance | Algorithm s | Solver wall s | Post s | Total wall s | Eval/s |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| GA+ACO | 0.830479476050 | 1244.609933088 | 94.929139939 | 65.688806575 | 3862.163003 | 3864.625866 | 0.058902 | 3864.715061 | 4.142756 |
| Paper-Aligned-HGA-XCut-Control | 1.285201950243 | 1313.832587664 | 199.047300041 | 77.034986032 | 11.084498 | 12.913797 | 0.057625 | 12.988671 | 1443.457288 |
| ABMA-Legacy-Exact-Fast-v1 | 0.763633595249 | 1244.326848637 | 0.173696824 | 103.237932534 | 110.289496 | 112.387072 | 0.066623 | 112.465737 | 145.072746 |

三者均满足：requested=realized=16000、`objective_budget_exhausted=true`、`stop_reason=objective_budget`、`run_status=success`、output cross-validation=success。单 seed Pilot 不支持任何算法优劣结论。

证据：`three_solver_w30_pilot_runs.csv`、`three_solver_w30_pilot_summary.json`、`three_solver_w30_checkpoint_trace.csv`、`THREE_SOLVER_W30_BUDGET_PILOT_REPORT.md`；raw root 为 `unified_experiments/three_solver_w30_protocol25_2026-07-15`。

## 10. 共同预算与安全超时

- 推荐三算法共同主评价预算：`16000` 次完整四机器人系统评价。
- 推荐每算法 wall-clock safety timeout：`21600` 秒。
- 最大实测 solver wall-clock：`3864.62586619996` 秒（GA+ACO）。
- 21600/max wall 安全余量：`5.589156815647725×`。
- 预算只统一外层完整系统评价次数，不表示三算法 CPU 指令或内部邻域检查量相同。

## 11. 哈希与 approval gates

- 当前 protocol SHA-256：`f6a1d11957e19f4f55eb61f8e006313ce04bca0d64852b29b25417af1e7ed4f8`。
- 冻结 ABMA profile SHA-256：`631b2d675e7531d04a3c019cc8013158223944074da3e42d489a5745addee1d3`。
- `formal_run_approved=false`。

| Gate | Value |
|---|---|
| normalization_baselines_computed_and_hashed | true |
| normalization_unit_tests_passed | true |
| abma_development_gate_passed | true |
| abma_final_candidate_frozen | true |
| abma_validation_completed | true |
| three_solver_cross_validation_completed | true |
| w30_budget_pilot_completed | true |
| user_approved_formal_execution | false |

## 12. 尚未解决的问题与下一阶段边界

1. w45/w60 运行时间仍未校准；不得从 w30 外推后直接启动。
2. 正式 30 seeds 尚未获用户批准，runner 保持 fail closed。
3. 权重、公共基线构造和负载下限敏感性分析仍待设计/批准。
4. Word 已做结构和正文校验，但尚未在 Microsoft Word GUI 中进行视觉版式复核。
5. 未知二进制/历史文件继续保守保留，若需进一步瘦身必须逐项人工确认。
6. 不得修改冻结 profile，不得用 validation seeds 调参，不得把单 seed Pilot 写成算法排名。

本报告应作为下一阶段 AI 的首要交接入口；继续工作前必须同时核对 `UNIFIED_EXPERIMENT_PROTOCOL.json`、`abma_final_profile.json`、本报告及上述根目录证据文件。
