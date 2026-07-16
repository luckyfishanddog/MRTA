# EPRK-MA 正式实验预检修复、资源校准与 Smoke 阶段 AI 交接报告

日期：2026-07-16  
仓库：`luckyfishanddog/MRTA`  
分支：`fix/atomic-formal-preflight-normalization-waiver`

## 1. 阶段起点

阶段开始时 EPRK-MA 候选、atomic weld 模型、39 个扩展实例和 164 个保护文件已经冻结；正式协议/manifest 框架已存在，但旧 w30/w45/w60 normalization 无法在当前环境重算为相同字节哈希，旧 exact 门禁因此停止。

## 2. 原预检停止原因

旧默认 validator 对 w30、w45、w60 各报两项错误：normalization spec hash mismatch，以及 normalization values or baseline evidence mismatch，共 6 项。默认 exact-check 未被削弱或伪装为通过。

## 3. 是否属于算法失败

不是。失败发生在旧 normalization 的重算浮点尾数/序列化哈希层；没有 EPRK-MA 求解质量、可行性或冻结源码失败。

## 4–8. 字段级差异、幅度与原因

审计逐字段比较 frozen、recomputed 与 reserialized 内容，共记录 20 个数值差异：w30 6 个、w45 7 个、w60 7 个。差异位于 ideal/baseline/scales/floors 的浮点量及其相关中间量；完整路径、十六进制浮点、绝对/相对差、依赖哈希和环境信息均在 field diff JSON/CSV 中。

- 最大绝对差：`9.094947017729282e-13`。
- 最大相对差：`1.4782098740360288e-15`。
- 字段集合、列表顺序、instance hash、算法/version、权重、baseline policy、load floor 与 normalization mode 一致。
- 无 NaN、Inf、符号变化或未列入范围的科学字段变化。
- `baseline_hash` 字符串的变化被单独列为数值 payload 尾差导致的派生摘要差异，不能被误称为数值字段本身的 exact match。
- 使用 frozen normalization 重放历史 unified fitness，w30/w45/w60 的差均为 0。

证据支持差异来自浮点求和、解析和序列化表示，而不是科学参数变化。

## 9–11. Compatibility waiver

全部严格条件满足，已生成仅限旧 validation 的 waiver。状态为 `approved_for_legacy_validation_only`，`exact_hash_match=false`，`compatibility_match=true`。范围只包括明确列出的 legacy w30/w45/w60；禁止用于 atomic normalization、EPRK profile、算法结果、实例或统计文件。

Waiver SHA-256：`abbd445490e48fc9762888dc9748e9b209872f860722270f312fbad29d2dffe2`。

## 12–14. 三态预检结果

新 preflight 明确保留三态：`exact_hash_pass`、`legacy_float_compatibility_pass`、`failure`。

- exact pass：21 项，包括 Git/保护哈希、EPRK profile/source、atomic freeze/source、benchmark、39 raw、39 atomic、39 atomic normalization、正式 protocol/manifest、run_id、command hash、seed、solver pairing、execution order、collision/resume policy、154 项 unit tests 和最小 solver smoke。
- compatibility pass：2 项，即旧 normalization compatibility audit，以及旧 exact validator 预期漂移的显式兼容确认。
- failure：0 项。

兼容通过从未计入 exact-pass。

## 15. 保护文件哈希

开始与结束均扫描 164 个保护文件；`protected_hashes_before.json` 与 `protected_hashes_after.json` 的文件映射完全一致，变化数为 0。

## 16–18. 测试、旧 runner 与新 preflight

- `python -m unittest discover -v`：154 项，全部通过。
- 兼容层专项测试：21 项，全部通过。
- 旧 `--validate-protocol`：仍失败，只报预期 6 项 exact normalization 漂移。
- 旧 `--self-check`：仍因同一 6 项 exact 漂移失败。
- 新 atomic preflight：`atomic_formal_preflight_passed=true`，21 exact、2 compatibility、0 failure。

## 19–21. 39 实例、EPRK profile 与 atomic model

39 个 raw instance、39 个 atomic instance、39 个新 atomic normalization 均通过文件与语义 exact hash。EPRK frozen profile SHA-256 为 `de75ad629aeeff608cab96097fe96aed091237348b9f1fe3e6f94c68442771f2`，冻结源码一致。atomic model freeze manifest 与冻结源码一致。现有几何、lmax/lmin、setup/post-processing 均未修改。

## 22. 正式预算资源校准

按 9 个代表实例 × seeds 191–193 × 2 算法顺序执行 54 次 20000-Primary 校准。54/54 到达 `objective_budget`，54/54 cross-validation 通过，没有任何运行超过 300 秒，因此未触发全局 fallback。建议/冻结 equal-primary 预算保持 20000；选择与解质量无关。

## 23. 各实例等时间上限

- real_w30 30 秒；real_w45 30 秒；real_w60 40 秒。
- uniform 全部 30/60/100、r1/r2：40 秒。
- clustered 全部：40 秒。
- boundary_dense 全部：40 秒。
- long_weld_rich 全部：110 秒。
- zero_travel_chain 全部：30 秒。
- load_skewed 全部：30 秒。

同一实例两个算法使用完全相同的 T；合成实例按同 family 的 n60/r1 参考映射。

## 24–27. 16-run Smoke、resume、collision 与统计工具

- 16 个优化运行全部成功，0 失败；8 个 equal-primary 精确完成 20000，8 个 equal-time 在完整候选边界以 time_limit 停止。
- 第一次启动在 5 项成功后受控中断；第二次启动复用 5 项并完成余下 11 项。前 5 项 SHA-256 与 mtime 全部不变，resume 通过。
- 16/16 碰撞后处理完成，16/16 raw hash 不变，unresolved conflicts 总数 0。
- 4 个配对组完成 paired bootstrap、exact Wilcoxon、Holm 与图表生成；工具 smoke 通过。Smoke 不用于资格结论。

## 28–29. 正式矩阵门禁

本阶段证据满足 `ready_for_full_formal_execution=true` 的技术条件，但正式协议继续保持 `formal_run_approved=false`。正式 seeds 201–230 未执行，完整 4680 次正式矩阵未执行，正式统计未执行。

## 30–32. Git 状态

- 分支：`fix/atomic-formal-preflight-normalization-waiver`。
- 本报告生成时的基线提交：`b93bbe92063591bc187f70ae8f6d83b8cecdd9d1`；包含本报告的最终本地提交 SHA 见最终交付回复/Git history（提交对象不能自包含自身 SHA）。
- Push/PR：在完成最终复验和提交后记录于最终交付回复；不得合并到 main。

## 33. 尚未解决的问题

旧默认 exact validator/self-check 仍会因历史浮点尾数返回失败，这是保留的历史行为，不是待“放宽”的错误。SciPy 未安装；统计 smoke 使用仓库内可审计的 exact Wilcoxon/paired bootstrap/Holm 实现并已通过。没有其他阻止下一阶段的科学或完整性问题。

## 34. 下一阶段唯一允许动作

在下一次独立对话中，由用户再次明确批准后，才可将 `formal_run_approved` 置于受控执行状态并启动完整 4680 次正式矩阵。不得在本阶段或未经新批准时运行 seeds 201–230。

## 核心证据索引

- `formal_atomic/remediation/LEGACY_NORMALIZATION_DRIFT_AUDIT.md`
- `formal_atomic/remediation/LEGACY_NORMALIZATION_FLOAT_COMPATIBILITY_REPORT.md`
- `formal_atomic/remediation/ATOMIC_FORMAL_PREFLIGHT_REMEDIATION_REPORT.md`
- `formal_atomic/remediation/ATOMIC_FORMAL_RESOURCE_CALIBRATION_REPORT.md`
- `formal_atomic/remediation/ATOMIC_FORMAL_SMOKE_REPORT.md`
- `formal_atomic/remediation/preflight_summary_v2.json`
- `formal_atomic/remediation/calibration_summary.json`
- `formal_atomic/remediation/smoke_manifest.json`
- `formal_atomic/remediation/smoke_resume_audit.json`
