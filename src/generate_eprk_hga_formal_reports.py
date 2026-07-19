"""Generate formal comparison reports from the validated 4680-run tables."""

from __future__ import annotations

import csv
import json
import subprocess
from pathlib import Path
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
FORMAL = ROOT / "formal_atomic"
OUT = FORMAL / "formal_execution"
HANDOFF = ROOT / "docs" / "handoffs" / "EPRK-MA与HGA扩展实例正式对比及主算法资格判定阶段_AI交接报告_2026-07-17.md"
HGA_NAME = "Paper-Aligned-HGA-Atomic-Control"
POSITIVE_CONCLUSION = (
    "在统一固定原子焊缝模型、扩展多类型实例、30 个配对 seeds、等评价和等时间协议下，"
    "EPRK-MA 相对于项目适配的 Paper-Aligned-HGA-Atomic-Control 展现出统计支持的综合优势，"
    "具备作为本文主算法的实验资格。"
)
NEGATIVE_CONCLUSION = "现有正式证据不足以支持 EPRK-MA 作为主算法。"


def read_csv(name: str) -> list[dict[str, str]]:
    with (OUT / name).open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write(name: str, lines: Sequence[str]) -> None:
    (OUT / name).write_text("\n".join(lines) + "\n", encoding="utf-8")


def markdown_table(rows: Sequence[Mapping[str, Any]], fields: Sequence[str]) -> list[str]:
    output = ["| " + " | ".join(fields) + " |", "|" + "|".join("---" for _ in fields) + "|"]
    for row in rows:
        output.append("| " + " | ".join(str(row.get(field, "")) for field in fields) + " |")
    return output


def main() -> None:
    authorization = load_json(OUT / "FORMAL_EXECUTION_AUTHORIZATION.json")
    checkpoint = load_json(OUT / "execution_checkpoint.json")
    qualification = load_json(OUT / "qualification_gates.json")
    collision_overall = load_json(OUT / "collision_overall_summary.json")
    archive = load_json(OUT / "formal_raw_archive_index.json")
    recomputation = load_json(OUT / "independent_recomputation_audit.json")
    overall = read_csv("overall_summary.csv")
    family = read_csv("family_summary.csv")
    size = read_csv("size_summary.csv")
    tests = read_csv("paired_tests_by_instance.csv")
    work = read_csv("work_amplification.csv")
    raw = read_csv("raw_runs.csv")
    failures = read_csv("run_failures.csv")
    current_commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    qualified = bool(qualification["eprk_main_algorithm_qualified"])
    conclusion = POSITIVE_CONCLUSION if qualified else NEGATIVE_CONCLUSION

    write("EPRK_MA_HGA_FORMAL_COMPARISON_REPORT.md", [
        "# EPRK-MA / HGA 正式比较报告", "",
        f"对照算法始终使用完整名称 **{HGA_NAME}**。它是统一 atomic 模型下的项目适配控制算法，"
        "不是文献原始 C++ 代码的逐行复现。", "",
        f"- 计划/完成优化运行：4680 / {len(raw)}",
        f"- 失败 attempts：{len(failures)}",
        "- 每个 instance/mode/seed 使用相同 seed、实例、normalization、目标、CPU 环境和冻结交替顺序。", "",
        "## 总体实例级 fitness", "",
        *markdown_table(overall, ["mode", "instance_count", "median_relative_gap_percent", "iqr_percent",
                                  "wilcoxon_p_two_sided", "rank_biserial_positive_favors_eprk",
                                  "wins_eprk", "ties", "losses_eprk"]), "",
        "## Family", "", *markdown_table(family, ["family", "mode", "instance_count",
                                                     "median_relative_gap_percent", "iqr_percent",
                                                     "wins_eprk", "ties", "losses_eprk"]), "",
        "## 规模", "", *markdown_table(size, ["nominal_size_group", "mode", "instance_count",
                                                  "median_relative_gap_percent", "iqr_percent",
                                                  "wins_eprk", "ties", "losses_eprk"]), "",
        "Smoke 与正式结果严格分离；本报告只使用完整 4680-run formal raw table。",
    ])

    write("EPRK_MA_HGA_RUNTIME_COMPARISON_REPORT.md", [
        "# EPRK-MA / HGA 正式运行速度比较", "",
        "Equal-primary 比较 20000 个完整 Primary；equal-time 比较相同冻结 wall limit。"
        "碰撞与统计后处理耗时不计入 solver wall time。", "",
        *markdown_table(work, ["mode", "solver", "median_solver_wall_time_s", "median_evaluations_per_second",
                               "median_route_evaluations_per_primary", "median_direction_dp_per_primary"]), "",
        "逐次 convergence trace、Primary、route evaluation、direction DP、cache 与 RSS 均记录在 raw_runs.csv；"
        "完整 result.json 保存在本地原始结果归档中。",
    ])

    write("EPRK_MA_HGA_COLLISION_COMPARISON_REPORT.md", [
        "# EPRK-MA / HGA 碰撞后处理比较", "",
        "碰撞审计是冻结的独立 postprocess，不是原始优化目标；它不修改路线、方向、raw fitness 或 raw makespan。", "",
        *[f"- {key}: `{value}`" for key, value in collision_overall.items()], "",
        "所有 unresolved conflicts 均如实保留；未通过删除运行来处理冲突。",
    ])

    write("EPRK_MA_FORMAL_STATISTICAL_ANALYSIS_REPORT.md", [
        "# EPRK-MA 正式统计分析报告", "",
        "主要终点是 equal-time normalized fitness；relative_gap=(EPRK-HGA)/HGA×100%，负值有利于 EPRK。", "",
        "- 单实例：30 个相同 seeds 的双侧配对 Wilcoxon，zero_method=wilcox，tie tolerance=1e-12。",
        "- 多重比较：同一 mode 的 39 个实例使用 Holm 校正。",
        "- 效应量：rank-biserial，正值表示有利于 EPRK。",
        "- Bootstrap：固定 seed 20260716，10000 次配对实例级重采样。",
        "- 总体推断单位：39 个实例级中位 relative gaps，不把 30 seeds 当作 30 个独立实例。", "",
        "## 总体", "", *markdown_table(overall, list(overall[0].keys())), "",
        "## 单实例 Holm 显著方向", "",
        f"- EPRK：`{sum(row['holm_significant_direction'] == 'eprk' for row in tests)}`",
        f"- HGA：`{sum(row['holm_significant_direction'] == 'hga' for row in tests)}`",
        f"- 无：`{sum(row['holm_significant_direction'] == 'none' for row in tests)}`",
    ])

    gates = qualification["gates"]
    gate_rows = [{"gate": key, "passed": value["passed"]} for key, value in gates.items()]
    write("EPRK_MA_MAIN_ALGORITHM_QUALIFICATION_REPORT.md", [
        "# EPRK-MA 主算法资格报告", "", *markdown_table(gate_rows, ["gate", "passed"]), "",
        f"`eprk_main_algorithm_qualified = {str(qualified).lower()}`", "", conclusion, "",
        "资格阈值来自结果前冻结的 formal protocol，未因结果修改。",
    ])

    write("EPRK_MA_PAPER_RESULTS_DRAFT.md", [
        "# EPRK-MA 论文结果草稿", "", "## 可使用的结论", "", conclusion, "",
        f"对照为项目适配的 {HGA_NAME}；结论限于统一固定原子焊缝模型、39 个实例、30 个配对 seeds、"
        "冻结 equal-primary/equal-time 协议。", "", "## 不可使用的结论", "",
        "不得声称显著优于文献原始 HGA C++ 源码、优于所有已知算法或达到全局最优；不得以三个真实实例证明普适性，"
        "也不得把 collision-adjusted makespan 当作原始优化目标。非显著差异不能描述为显著优势。",
    ])

    before = load_json(OUT / "protected_hashes_before.json")
    after = load_json(OUT / "protected_hashes_after.json")
    gate_state = {key: value["passed"] for key, value in gates.items()}
    handoff_items = [
        f"起始 PR：#2；head `{authorization['pr_head_sha']}`。",
        f"工作分支：`{authorization['execution_branch']}`。",
        f"正式授权 hash：`{(OUT / 'FORMAL_EXECUTION_AUTHORIZATION.sha256').read_text().strip()}`。",
        f"正式协议 hash：`{authorization['formal_protocol_sha256']}`。",
        f"Manifest hash：`{authorization['formal_manifest_sha256']}`。",
        f"EPRK profile hash：`{authorization['eprk_profile_sha256']}`。",
        f"Atomic model hash：`{authorization['atomic_model_freeze_sha256']}`。",
        f"Benchmark suite hash：`{authorization['benchmark_suite_sha256']}`。",
        "完整测试与 preflight 日志位于 formal_execution/finalization_logs。",
        "Atomic preflight 要求 21 exact、2 compatibility、0 failure。",
        f"正式矩阵规模：`{authorization['authorized_run_count']}`。",
        f"实际成功：`{len(raw)}`。",
        f"失败 attempts：`{len(failures)}`。",
        f"Checkpoint complete：`{checkpoint['complete']}`。",
        "Equal-primary 质量结果见正式比较报告。",
        "Equal-primary 速度结果见运行速度报告。",
        "Equal-time 主终点见正式比较和统计报告。",
        "真实实例结果见 instance_summary.csv。",
        "Family 结果见 family_summary.csv。",
        "规模结果见 size_summary.csv。",
        "Raw/collision makespan 见 collision CSV。",
        "冲突与等待见碰撞报告。",
        "Throughput 见 work_amplification.csv。",
        "Route/Primary 见 work_amplification.csv。",
        "DP/Primary 见 work_amplification.csv。",
        "RSS 见 raw_runs.csv 与 memory 图。",
        "单实例 Wilcoxon 见 paired_tests_by_instance.csv。",
        "Holm 结果见 holm_adjusted_tests.csv。",
        "总体 Wilcoxon 见 overall_summary.csv。",
        "Effect size 见 effect_sizes.csv。",
        "Bootstrap 见 bootstrap_confidence_intervals.csv。",
        "Win/tie/loss 见 win_tie_loss.csv。",
        f"A–G 门禁：`{json.dumps(gate_state, ensure_ascii=False)}`。",
        f"eprk_main_algorithm_qualified：`{str(qualified).lower()}`。",
        f"可用于论文：{conclusion}",
        "不可用于论文：不得外推到文献原始源码、全部算法或全局最优。",
        f"保护文件 before/after 一致：`{before['files'] == after['files']}`。",
        f"60-result 独立复算通过：`{recomputation['passed']}`。",
        f"原始文件数：`{archive['raw_file_count']}`；完整原始结果只在本地保存。",
        f"原始归档 SHA-256：`{archive['archive_sha256']}`。",
        f"报告生成时 Git commit：`{current_commit}`；最终结果 commit 以 Git history 为准。",
        "Stacked PR 以 fix/atomic-formal-preflight-normalization-waiver 为 base。",
        "仍需以最终发布摘要确认 push/PR URL；不得用部分结果替代完整证据。",
    ]
    HANDOFF.write_text(
        "# EPRK-MA 与 HGA 扩展实例正式对比及主算法资格判定阶段 AI 交接报告\n\n"
        + "\n".join(f"{index}. {item}" for index, item in enumerate(handoff_items, 1)) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
