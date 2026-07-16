# EPRK-MA / HGA-Atomic 端到端 Smoke 报告

## 范围与结论

本阶段仅执行独立 smoke 矩阵，不执行正式矩阵。实例为 `real_w30` 与 `long_weld_rich_n60_r1`，算法为 EPRK-MA-Frozen 与 Paper-Aligned-HGA-Atomic-Control，种子仅为 194、195，模式为 equal-primary 与 equal-time，共 16 次优化运行。

- 优化运行：16 成功，0 失败。
- collision postprocess：16 完成，0 个 unresolved conflict。
- equal-primary：8/8 精确完成 20000 Primary evaluations，并以 `objective_budget` 停止。
- equal-time：8/8 以 `time_limit` 停止，并只在完整候选边界退出。
- cross-validation：16/16 通过。
- raw result hash：16/16 已记录；collision 前后原始结果 16/16 未改变。
- manifest 最终状态：`completed`。
- 正式种子 201–230：未执行。
- 完整 4680 次矩阵：未执行。

## 时间与顺序

`real_w30` 的两个算法共同使用 30 秒 equal-time 上限；`long_weld_rich_n60_r1` 共同使用 110 秒上限。算法顺序由 `SHA256(instance|seed|mode)` 奇偶确定，并在每个配对内交替。执行为 sequential=true、parallel=false。

## Resume 与 checkpoint

第一次启动在完成恰好 5 个成功运行及其碰撞审计后受控停止。第二次启动识别并复用这 5 个结果，执行余下 11 个运行。前 5 个文件的 SHA-256 和纳秒 mtime 均保持不变，证明没有重复执行。checkpoint 每完成一项更新；hash/resume_key 不一致时移入 `smoke_quarantine` 的策略已启用。本次没有需要 quarantine 的结果，也没有失败结果。

## 碰撞后处理

每个成功优化结果均执行统一 postprocess。16 个 raw 文件在 postprocess 前后哈希一致，routes/directions 不变；总 unresolved conflict 为 0。碰撞处理不计入算法时间、solver wall time 或搜索目标。

## 统计工具 smoke

统计工具对 4 个 instance×mode 组执行配对汇总、10,000 次确定性 paired bootstrap、双侧 exact Wilcoxon 与 Holm 校正，并成功生成 `smoke_fitness_pairs.png`。每组仅有 2 对数据，所有结果仅证明工具链可执行，禁止用于算法资格或主结论。

## 证据文件

- `smoke_manifest.json` 与伴随 SHA-256；
- `smoke_runs.csv`；
- `smoke_collision_runs.csv`；
- `smoke_failures.csv`；
- `smoke_resume_audit.json`；
- `smoke_checkpoint.json`；
- `smoke_statistics.json`、`smoke_statistics.csv`；
- `smoke_fitness_pairs.png`；
- `smoke_results/` 下 16 个原始结果、hash companion 和碰撞审计。
