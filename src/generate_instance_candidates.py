#!/usr/bin/env python3
"""Generate, validate and geometrically rank frozen MRTA instances.

No solver is imported or executed by this script.  Candidate selection uses
only input geometry and deterministic validity checks.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import shutil
import statistics
from collections import defaultdict
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from generate_welds import (
    DEFAULT_MIN_WELD_LENGTH_M,
    DEFAULT_WELD_Z_M,
    PLATFORM_H_M,
    PLATFORM_W_M,
    compute_weld_instance_hash,
    generate_weld_instance_from_source,
    load_frozen_weld_instance,
    save_frozen_weld_instance,
)


def parse_int_list(text: str) -> list[int]:
    values = [int(item.strip()) for item in text.replace(" ", ",").split(",") if item.strip()]
    if not values:
        raise argparse.ArgumentTypeError("at least one integer is required")
    return values


def crosses_line(a: float, b: float, boundary: float, eps: float = 1e-9) -> bool:
    return (a - boundary) * (b - boundary) < -(eps * eps)


def geometry_metrics(welds) -> dict[str, float | int | bool]:
    points = [point for weld in welds for point in weld.endpoints()]
    finite = all(math.isfinite(value) for point in points for value in point)
    in_bounds = all(
        -1e-9 <= point[0] <= PLATFORM_W_M + 1e-9
        and -1e-9 <= point[1] <= PLATFORM_H_M + 1e-9
        for point in points
    )
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    x_coverage = (max(xs) - min(xs)) / PLATFORM_W_M if xs else 0.0
    y_coverage = (max(ys) - min(ys)) / PLATFORM_H_M if ys else 0.0
    bbox_area_ratio = x_coverage * y_coverage
    midpoints = [weld.midpoint() for weld in welds]
    if midpoints:
        mean_x = statistics.fmean(point[0] for point in midpoints)
        mean_y = statistics.fmean(point[1] for point in midpoints)
        midpoint_dispersion = math.sqrt(
            statistics.fmean(
                (point[0] - mean_x) ** 2 + (point[1] - mean_y) ** 2
                for point in midpoints
            )
        ) / math.hypot(PLATFORM_W_M, PLATFORM_H_M)
    else:
        midpoint_dispersion = 0.0
    occupied = {
        (
            min(3, max(0, int(point[0] / PLATFORM_W_M * 4))),
            min(3, max(0, int(point[1] / PLATFORM_H_M * 4))),
        )
        for point in midpoints
    }
    grid_occupancy_ratio = len(occupied) / 16.0

    quadrant_lengths = [0.0, 0.0, 0.0, 0.0]
    cross_y = 0
    cross_x = 0
    cross_both = 0
    lengths = []
    for weld in welds:
        length = float(weld.length)
        lengths.append(length)
        mx, my, _ = weld.midpoint()
        quadrant = (0 if my >= 6.0 else 2) + (0 if mx < 10.0 else 1)
        quadrant_lengths[quadrant] += length
        cy = crosses_line(weld.y1, weld.y2, 6.0)
        cx = crosses_line(weld.x1, weld.x2, 10.0)
        cross_y += int(cy)
        cross_x += int(cx)
        cross_both += int(cy and cx)
    quadrant_mean = statistics.fmean(quadrant_lengths) if quadrant_lengths else 0.0
    quadrant_std = statistics.pstdev(quadrant_lengths) if len(quadrant_lengths) > 1 else 0.0
    quadrant_cv = quadrant_std / quadrant_mean if quadrant_mean > 0.0 else math.inf
    upper = quadrant_lengths[0] + quadrant_lengths[1]
    lower = quadrant_lengths[2] + quadrant_lengths[3]
    left = quadrant_lengths[0] + quadrant_lengths[2]
    right = quadrant_lengths[1] + quadrant_lengths[3]
    total = sum(lengths)
    return {
        "finite_coordinates": finite,
        "all_welds_in_platform": in_bounds,
        "bbox_area_ratio": bbox_area_ratio,
        "x_coverage_ratio": x_coverage,
        "y_coverage_ratio": y_coverage,
        "midpoint_dispersion": midpoint_dispersion,
        "grid_occupancy_ratio": grid_occupancy_ratio,
        "quadrant_0_length": quadrant_lengths[0],
        "quadrant_1_length": quadrant_lengths[1],
        "quadrant_2_length": quadrant_lengths[2],
        "quadrant_3_length": quadrant_lengths[3],
        "quadrant_length_mean": quadrant_mean,
        "quadrant_length_std": quadrant_std,
        "quadrant_length_cv": quadrant_cv,
        "upper_lower_length_imbalance": abs(upper - lower) / total if total else math.inf,
        "left_right_length_imbalance": abs(left - right) / total if total else math.inf,
        "cross_y6_count": cross_y,
        "cross_x10_count": cross_x,
        "cross_both_count": cross_both,
        "total_original_weld_length": total,
        "weld_length_mean": statistics.fmean(lengths) if lengths else 0.0,
        "weld_length_std": statistics.pstdev(lengths) if len(lengths) > 1 else 0.0,
        "weld_length_min": min(lengths, default=0.0),
        "weld_length_max": max(lengths, default=0.0),
    }


def plot_instance(welds, path: Path, title: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(10, 6))
    for weld in welds:
        ax.plot([weld.x1, weld.x2], [weld.y1, weld.y2], linewidth=1.3)
    ax.axvline(10.0, color="black", linestyle="--", linewidth=0.8)
    ax.axhline(6.0, color="black", linestyle="--", linewidth=0.8)
    ax.set(xlim=(0, PLATFORM_W_M), ylim=(0, PLATFORM_H_M), xlabel="x (m)", ylabel="y (m)", title=title)
    ax.set_aspect("equal", adjustable="box")
    ax.grid(True, alpha=0.25)
    fig.tight_layout()
    fig.savefig(path, dpi=180)
    plt.close(fig)


def percentile_ranks(rows: list[dict], field: str, higher_is_better: bool = True) -> dict[int, float]:
    indexed = [(index, float(row[field])) for index, row in enumerate(rows)]
    ordered = sorted(indexed, key=lambda item: item[1])
    denominator = max(1, len(ordered) - 1)
    result = {}
    start = 0
    while start < len(ordered):
        end = start + 1
        while end < len(ordered) and math.isclose(
            ordered[end][1], ordered[start][1], rel_tol=0.0, abs_tol=1e-12
        ):
            end += 1
        average_position = 0.5 * (start + end - 1)
        score = average_position / denominator
        if not higher_is_better:
            score = 1.0 - score
        for position in range(start, end):
            result[ordered[position][0]] = score
        start = end
    return result


def score_rows(rows: list[dict]) -> None:
    valid = [row for row in rows if row["hard_valid"]]
    if not valid:
        return
    fields = {
        "bbox_area_ratio": True,
        "x_coverage_ratio": True,
        "y_coverage_ratio": True,
        "midpoint_dispersion": True,
        "grid_occupancy_ratio": True,
        "quadrant_length_cv": False,
        "upper_lower_length_imbalance": False,
        "left_right_length_imbalance": False,
    }
    ranks = {field: percentile_ranks(valid, field, direction) for field, direction in fields.items()}
    for index, row in enumerate(valid):
        coverage = statistics.fmean(ranks[field][index] for field in list(fields)[:5])
        balance = statistics.fmean(ranks[field][index] for field in list(fields)[5:])
        n = max(1, int(row["actual_weld_count"]))
        interaction_rate = (int(row["cross_y6_count"]) + int(row["cross_x10_count"])) / n
        interaction = max(0.0, 1.0 - abs(interaction_rate - 0.10) / 0.10)
        if int(row["cross_y6_count"]) + int(row["cross_x10_count"]) == 0:
            interaction = 0.0
        row["coverage_rank_score"] = coverage
        row["balance_rank_score"] = balance
        row["boundary_interaction_score"] = interaction
        row["geometry_score"] = 0.45 * coverage + 0.35 * balance + 0.20 * interaction


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def copy_selected(row: dict, candidates_dir: Path, selected_dir: Path) -> None:
    target = int(row["target_weld_count"])
    seed = int(row["instance_seed"])
    source_stem = f"instance_w{target}_iseed{seed}"
    for extension in ("xlsx", "json", "png"):
        shutil.copy2(
            candidates_dir / f"w{target}" / f"{source_stem}.{extension}",
            selected_dir / f"instance_w{target}.{extension}",
        )


def build_report(rows: list[dict], selected: dict[int, dict], common_seed: int | None, output: Path, command: str) -> None:
    exact = [row for row in rows if row["hard_valid"]]
    lines = [
        "# 冻结焊缝实例候选选择报告",
        "",
        "> 本报告只使用输入几何指标；未运行或引用任何 MRTA 求解器结果。",
        "",
        "## 生成协议",
        "",
        f"- 运行命令：`{command}`",
        "- 精确焊缝数量与合法性是硬门槛；未精确命中的候选不会参与评分。",
        "- 几何总分：`0.45 × coverage_rank + 0.35 × balance_rank + 0.20 × boundary_interaction`。",
        "- coverage_rank 为包围盒、x/y 覆盖、中点离散度、4×4 网格占用率五项归一化排名均值。",
        "- balance_rank 为四象限长度 CV、上下不均衡、左右不均衡三项反向归一化排名均值。",
        "- boundary_interaction 偏好约 10% 焊缝穿过参考边界，零交互得 0 分。",
        "",
        "## 精确候选汇总",
        "",
        "| target | seed | groups requested/placed | hash (12) | coverage | quadrant CV | crossings y/x | score |",
        "|---:|---:|---:|---|---:|---:|---:|---:|",
    ]
    for row in sorted(exact, key=lambda item: (int(item["target_weld_count"]), -float(item["geometry_score"]))):
        lines.append(
            f"| {row['target_weld_count']} | {row['instance_seed']} | {row['requested_group_count']}/{row['placed_group_count']} | "
            f"`{str(row['instance_hash'])[:12]}` | {float(row['bbox_area_ratio']):.3f} | "
            f"{float(row['quadrant_length_cv']):.3f} | {row['cross_y6_count']}/{row['cross_x10_count']} | {float(row['geometry_score']):.4f} |"
        )
    lines += ["", "## 选定实例", ""]
    for target in sorted(selected):
        row = selected[target]
        lines += [
            f"### w{target}",
            "",
            f"- instance seed：`{row['instance_seed']}`",
            f"- requested/placed groups：`{row['requested_group_count']}/{row['placed_group_count']}`",
            f"- actual weld count：`{row['actual_weld_count']}`",
            f"- instance hash：`{row['instance_hash']}`",
            f"- total weld length：`{float(row['total_original_weld_length']):.6f} m`",
            f"- bbox/x/y/grid coverage：`{float(row['bbox_area_ratio']):.3f} / {float(row['x_coverage_ratio']):.3f} / {float(row['y_coverage_ratio']):.3f} / {float(row['grid_occupancy_ratio']):.3f}`",
            f"- quadrant CV：`{float(row['quadrant_length_cv']):.3f}`",
            f"- crossings y=6 / x=10 / both：`{row['cross_y6_count']} / {row['cross_x10_count']} / {row['cross_both_count']}`",
            f"- geometry score：`{float(row['geometry_score']):.4f}`",
            "",
        ]
    lines += ["## 公共 seed 结论", ""]
    if common_seed is None:
        lines.append("没有 seed 在三个规模上同时通过精确数量和合法性硬门槛；主方案采用各规模独立最佳 seed。")
    else:
        lines.append(f"推荐公共 instance seed：`{common_seed}`。它在 30/45/60 三个规模上均精确合法，并按三规模平均几何得分排名最高。")
        independent = {
            int(row["target_weld_count"]): int(row["instance_seed"])
            for row in rows if row.get("selected_independent_best")
        }
        lines.append(
            "各规模独立最高分 seed 为："
            + "、".join(f"w{target}={seed}" for target, seed in sorted(independent.items()))
            + "。"
        )
        lines.append("`selected/` 默认采用公共 seed 方案，以保持 30/45/60 生成逻辑一致；独立最优文件仍保留在 `candidates/` 中用于敏感性分析。")
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate and rank frozen weld instances")
    parser.add_argument("--source-excel", required=True)
    parser.add_argument("--target-weld-counts", type=parse_int_list, default=parse_int_list("30,45,60"))
    parser.add_argument("--instance-seeds", type=parse_int_list, default=parse_int_list("41,42,43,44,45,46,47,48,49,50"))
    parser.add_argument("--group-count-min", type=int, default=1)
    parser.add_argument("--group-count-max", type=int, default=60)
    parser.add_argument("--output-dir", default="data/instances")
    parser.add_argument("--input-units", choices=["mm", "m"], default="mm")
    parser.add_argument("--spacing", type=float, default=30.0, help="spacing in input units")
    parser.add_argument("--min-weld-length-mm", type=float, default=DEFAULT_MIN_WELD_LENGTH_M * 1000.0)
    parser.add_argument("--weld-z-mm", type=float, default=DEFAULT_WELD_Z_M * 1000.0)
    parser.add_argument("--allow-rotate", action=argparse.BooleanOptionalAction, default=True)
    args = parser.parse_args()
    if args.group_count_min < 1 or args.group_count_max < args.group_count_min:
        parser.error("invalid group-count range")

    output_dir = Path(args.output_dir)
    candidates_dir = output_dir / "candidates"
    selected_dir = output_dir / "selected"
    selected_dir.mkdir(parents=True, exist_ok=True)
    spacing_m = args.spacing * (0.001 if args.input_units == "mm" else 1.0)
    min_length_m = args.min_weld_length_mm / 1000.0
    weld_z_m = args.weld_z_mm / 1000.0
    rows: list[dict] = []

    for seed in args.instance_seeds:
        max_welds, max_meta = generate_weld_instance_from_source(
            args.source_excel,
            args.group_count_max,
            seed,
            input_units=args.input_units,
            spacing_m=spacing_m,
            allow_rotate=args.allow_rotate,
            min_weld_length_m=min_length_m,
            weld_z_m=weld_z_m,
            max_attempts_per_instance=2000,
        )
        by_count = {}
        for group_count in range(args.group_count_min, args.group_count_max + 1):
            subset = [weld for weld in max_welds if int(weld.instance_index) < group_count]
            placed_count = min(group_count, int(max_meta["placed_group_count"]))
            by_count[group_count] = (subset, placed_count)
        for target in args.target_weld_counts:
            exact_counts = [count for count, (welds, _) in by_count.items() if len(welds) == target]
            if exact_counts:
                chosen_count = min(exact_counts)
                exact = True
            else:
                chosen_count = min(by_count, key=lambda count: (abs(len(by_count[count][0]) - target), count))
                exact = False
            welds, placed_count = by_count[chosen_count]
            metadata = dict(max_meta)
            metadata.update(
                {
                    "target_weld_count": target,
                    "requested_group_count": chosen_count,
                    "placed_group_count": placed_count,
                    "actual_weld_count": len(welds),
                    "placed_groups": [item for item in max_meta["placed_groups"] if int(item["instance_index"]) < chosen_count],
                    "placed_rectangles_m": list(max_meta["placed_rectangles_m"][:placed_count]),
                }
            )
            metadata["total_original_weld_length"] = sum(weld.length for weld in welds)
            metadata["instance_hash"] = compute_weld_instance_hash(welds, metadata)
            metrics = geometry_metrics(welds)
            row = {
                "target_weld_count": target,
                "instance_seed": seed,
                "exact_target_found": exact,
                "requested_group_count": chosen_count,
                "placed_group_count": placed_count,
                "actual_weld_count": len(welds),
                "count_delta": len(welds) - target,
                "instance_hash": metadata["instance_hash"],
                **metrics,
            }
            row["no_short_welds"] = all(weld.length + 1e-9 >= min_length_m for weld in welds)
            row["hard_valid"] = bool(exact and row["finite_coordinates"] and row["all_welds_in_platform"] and row["no_short_welds"])
            row["duplicate_instance_hash"] = False
            row["roundtrip_hash_match"] = False
            row["regeneration_hash_match"] = False
            if exact:
                stem = f"instance_w{target}_iseed{seed}"
                directory = candidates_dir / f"w{target}"
                xlsx = directory / f"{stem}.xlsx"
                json_path = directory / f"{stem}.json"
                png = directory / f"{stem}.png"
                save_frozen_weld_instance(welds, metadata, str(xlsx), str(json_path))
                loaded, loaded_meta = load_frozen_weld_instance(str(xlsx))
                row["roundtrip_hash_match"] = loaded_meta["instance_hash"] == metadata["instance_hash"] and len(loaded) == len(welds)
                regenerated, regen_meta = generate_weld_instance_from_source(
                    args.source_excel,
                    chosen_count,
                    seed,
                    input_units=args.input_units,
                    spacing_m=spacing_m,
                    allow_rotate=args.allow_rotate,
                    min_weld_length_m=min_length_m,
                    weld_z_m=weld_z_m,
                    target_weld_count=target,
                )
                row["regeneration_hash_match"] = regen_meta["instance_hash"] == metadata["instance_hash"] and len(regenerated) == len(welds)
                row["hard_valid"] = bool(row["hard_valid"] and row["roundtrip_hash_match"] and row["regeneration_hash_match"])
                plot_instance(welds, png, f"w{target}, instance seed {seed}")
            rows.append(row)

    hash_groups = defaultdict(list)
    for row in rows:
        if row["exact_target_found"]:
            hash_groups[(row["target_weld_count"], row["instance_hash"])].append(row)
    for duplicates in hash_groups.values():
        if len(duplicates) > 1:
            for row in duplicates:
                row["duplicate_instance_hash"] = True

    for target in args.target_weld_counts:
        score_rows([row for row in rows if int(row["target_weld_count"]) == target])
    independent_best = {}
    for target in args.target_weld_counts:
        valid = [row for row in rows if int(row["target_weld_count"]) == target and row["hard_valid"]]
        if valid:
            independent_best[target] = max(valid, key=lambda row: (float(row["geometry_score"]), -int(row["requested_group_count"]), -int(row["instance_seed"])))
            independent_best[target]["selected_independent_best"] = True

    seed_scores = defaultdict(list)
    for row in rows:
        if row["hard_valid"]:
            seed_scores[int(row["instance_seed"])].append((int(row["target_weld_count"]), float(row["geometry_score"])))
    common_candidates = {
        seed: statistics.fmean(score for _, score in values)
        for seed, values in seed_scores.items()
        if {target for target, _ in values} == set(args.target_weld_counts)
    }
    common_seed = max(common_candidates, key=lambda seed: (common_candidates[seed], -seed)) if common_candidates else None
    for row in rows:
        row["recommended_common_seed"] = common_seed is not None and int(row["instance_seed"]) == common_seed

    # Formal default: prefer the best valid common seed for cross-scale
    # consistency.  Independent per-scale winners remain in candidates/ and
    # are documented for sensitivity analysis.
    if common_seed is not None:
        selected = {
            target: next(
                row for row in rows
                if int(row["target_weld_count"]) == target
                and int(row["instance_seed"]) == common_seed
                and row["hard_valid"]
            )
            for target in args.target_weld_counts
        }
        selection_scheme = "recommended_common_seed"
    else:
        selected = dict(independent_best)
        selection_scheme = "independent_per_scale_best"
    for row in selected.values():
        row["selected_for_formal_experiments"] = True
        copy_selected(row, candidates_dir, selected_dir)

    manifest = output_dir / "instance_candidates_manifest.csv"
    write_csv(manifest, rows)
    command = "python generate_instance_candidates.py " + " ".join(
        [
            f'--source-excel "{args.source_excel}"',
            "--target-weld-counts " + ",".join(map(str, args.target_weld_counts)),
            "--instance-seeds " + ",".join(map(str, args.instance_seeds)),
            f"--group-count-min {args.group_count_min}",
            f"--group-count-max {args.group_count_max}",
            f'--output-dir "{args.output_dir}"',
        ]
    )
    build_report(rows, selected, common_seed, output_dir / "INSTANCE_SELECTION_REPORT.md", command)
    summary = {
        "selected_scheme": selection_scheme,
        "selected_for_formal_experiments": {str(target): int(row["instance_seed"]) for target, row in selected.items()},
        "independent_per_scale_best": {str(target): int(row["instance_seed"]) for target, row in independent_best.items()},
        "recommended_common_seed": common_seed,
        "common_seed_mean_scores": common_candidates,
    }
    (output_dir / "selection_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if len(selected) == len(args.target_weld_counts) else 2


if __name__ == "__main__":
    raise SystemExit(main())
