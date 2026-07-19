# ABMA 最终候选门禁报告

日期：2026-07-15  
结论：**开发候选未冻结；验证、三算法预检和 Pilot 均未获授权。**

## 1. 权威范围

- 正式实验算法仅为 `GA+ACO`、`Paper-Aligned-HGA-XCut-Control`、`ABMA`。
- `DE+LKH` 是历史探索排除项；保留 `MRTA_DE_LKH.py`、`route_local_search.py` 和历史数据，但本阶段没有运行，也不得进入新汇总、排序或统计检验。
- 开发实例固定为 w30，实例哈希 `2fab0e3e...bb2a62`，外层完整系统评价预算为 2000，无碰撞审计，串行运行。
- 开发实验快照协议哈希为 `a5a42a79961a8f9c9fe7530dbccd5a5bf75e8c995b556d019ba5ddf6ce92f38a`；补齐低种群多样性灰区规则后的当前协议哈希为 `2b3a10736ce74fd83584af4fee4425d312a096b8666c9734d0f8e088ed291f7d`。

## 2. 已实现内容

1. 协议升级为 2.3.0 三算法体系，正式 runner 不再生成 DE+LKH 任务；显式正式 DE 请求被拒绝。
2. ABMA 新增可恢复 low/mid/high 内层、95% 最近秩审计分位数、至少 50 个有效审计样本、每 20 个决策的确定性反事实审计及 high-certified 种群/档案/最终解约束。
3. 增量修复使用 `parent_id + canonical geometry` 身份，复用未变化机器人的精确 evaluator 缓存；部分几何变化时，仅对可以一一映射的完整路线复用父缓存。
4. 外层 RNG 与分区派生内层 RNG 分离；内层晋级深度不会消耗外层随机流。
5. 完整审计记录保存在 `result.json`；`metrics.csv` 仅保存审计聚合，避免超长 CSV 字段导致 runner invalid。

## 3. 开发候选

候选仅用于开发评估，**不是冻结的最终配置**：

- variant：`incremental_multifidelity`
- low：ALNS 10，VND 0
- mid：ALNS 30，VND 1
- high：ALNS 60，VND 3
- audit quantile：0.95
- minimum audit samples：50
- audit interval：20
- 开发 profile hash：`170b5304cb2898163dd0c7219e0f16105e5d41ec2d1a2740c5c03e34ac321535`

## 4. 配对开发结果

| seed | variant | fitness | algorithm time (s) | 对 legacy 加速 | 对 legacy 退化 | 误拒率 | 判定 |
|---:|---|---:|---:|---:|---:|---:|---|
| 42 | legacy | 0.7636335952 | 373.714 | 1.000× | 0% | 0% | 基线 |
| 42 | multifidelity only | 0.7635993354 | 368.313 | 1.015× | -0.0045% | 3.33% | 速度失败 |
| 42 | incremental multifidelity | 0.7722410885 | 335.633 | 1.113× | 1.127% | 0% | 质量通过、速度失败 |
| 43 | legacy | 0.7635318768 | 431.197 | 1.000× | 0% | 0% | 基线 |
| 43 | multifidelity only | 0.7635196552 | 404.509 | 1.066× | -0.0016% | 0% | 速度失败 |
| 43 | incremental multifidelity | 0.7812443644 | 314.715 | 1.370× | 2.320% | 0% | 质量失败、速度失败 |

候选的聚合审计误拒为 `0 / (41 + 28) = 0%`，通过 5% 线；但 seed 42 和 seed 43 的配对加速均低于 2×。因此即使 seed 44 达到任意加速，三种子中位数也不可能达到 2×。seed 43 的 fitness 退化也超过逐种子 2% 上限。

## 5. 提前停止与门禁

- seed 44：runner 已开始 legacy 后被终止；没有形成有效完整结果，所有 seed 44 变体均标记为 `not_run_gate_impossible`。
- 最终候选冻结：**false**。
- 验证 seeds 45、46、47：**未触碰、未运行**。
- 三算法 w30/2000 preflight：**未运行**。
- 三算法 w30/16000 Pilot：**未运行**。
- w45、w60、formal 30 seeds：**未运行**。
- `formal_run_approved`：**false**。

## 6. 测试与文档

- `python -m unittest discover -q`：84 项通过。
- 协议校验：通过。
- Word 方法章节已更新为自适应审计晋级和三算法实验集合；DE+LKH 明确为历史排除项。

## 7. 交付物

- `abma_development_benchmark.csv`：6 条有效配对记录及 seed 44 fail-closed 状态。
- `abma_validation_benchmark.csv`：未运行哨兵记录。
- `abma_final_profile.json`：`status=not_frozen`，不得作为正式 profile 使用。
- `abma_final_profile.sha256`：`NOT_FROZEN` 哨兵，不是正式冻结哈希。
- `THREE_SOLVER_W30_PILOT_REPORT.md`：未获授权说明。

## 8. 后续工作边界

下一阶段不能直接运行验证或 Pilot。必须先提出新的、可解释且不改变科学语义的 ABMA 性能方案，在开发 seeds 上重新达到：三种子中位加速至少 2×、每个 seed 退化不超过 2%、聚合误拒不超过 5%。任何新调参不得查看 seeds 45—47。
