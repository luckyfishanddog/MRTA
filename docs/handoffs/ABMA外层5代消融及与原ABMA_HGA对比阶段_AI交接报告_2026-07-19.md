# ABMA 外层 5 代消融及与原 ABMA/HGA 对比阶段 AI 交接报告

1. MRTA_ABMA1 通过导入和子类化权威 MRTA_ABMA 实现，没有复制完整求解器。
2. 与原 ABMA 唯一搜索因素差异是外层 generations 从 20 降为 5。
3. MRTA_ABMA.py hash `7f6f09a030543f6e4e14d2bd837791c90896da3bb1e395f409c33d6eaef20df9`；MRTA_ABMA1.py hash `223325235ef76ff67733cee711b480a6a308b4a8b93219c2d6e9f11c414fc622`。
4. ABMA1 的 9 次实测 Primary 均为 72，且均完成 5 代并以 generation_limit 停止。
5. ABMA20 的 9 次实测 Primary 均为 252，且均完成 20 代并以 generation_limit 停止。
6. w30/w45/w60 的全部每-seed 行见 abma_outer5_runs.csv。
7. ABMA20/ABMA1 中位 wall speedup 为 `2.2765470300695343`。
8. ABMA1 对 ABMA20 中位 fitness 退化为 `0.16173118216168866`。
9. ABMA20 最终最优代数为 16–20，9/9 都晚于第 5 代。
10. 第五代后剩余 fitness 改进为约 5.66%–38.56%，说明后 15 代有实质质量贡献。
11. ABMA1 在三个实例的中位 fitness 均优于 HGA-budget72，但总体中位墙钟约慢 `24.40553972959526` 倍。
12. ABMA20 在三个实例的中位 fitness 均优于 HGA-budget252，但墙钟代价远高；逐 seed 见 pairwise CSV。
13. 已有 w30/w45 的 16000 预算和 w60 的 11000 预算 Pilot 只进入 historical context，不参与配对统计。
14. 时间—质量权衡属于 Case B 的质量损失分支：5 代明显更快，但质量损失不可忽略。
15. 扩大验证判定：固定 5 代过度削弱外层分区搜索，不能作为主算法。 可保留为探索性快速变体，不应替换主算法。
16. 相同 Primary 不等于相同内部 route/DP 工作；ABMA1 相比 HGA 仍有根本结构效率问题。
17. 尚未解决：ABMA final population diversity 未由权威 solver 暴露；协议严格 normalization validator 存在既有冻结浮点漂移；n=3 不能作显著性推断。
18. 正式 270-run 实验未运行；formal_run_approved 与 user_approved_formal_execution 均仍为 false。
