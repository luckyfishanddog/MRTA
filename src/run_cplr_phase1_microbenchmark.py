"""Read-only w30/w45/w60 CPLR phase-1 decoder microbenchmark."""

from __future__ import annotations

import argparse
import csv
import json
import random
import statistics
import sys
import time
import tracemalloc
from pathlib import Path
from typing import Any, Sequence

from continuous_lineage_decoder import (
    CPLRChromosome,
    CPLRDecoderConfig,
    aggregate_system_metrics,
    decode_active_tasks,
    decode_boundaries,
    evaluate_routes,
    order_active_tasks,
)
from generate_welds import load_frozen_weld_instance
from lineage_presplit import build_lineages
from mrta_problem_core import assign_welds_to_robots_split, evaluate_order
from reachable_side_keys import apply_reachable_side_index, build_reachable_side_index


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "results" / "cplr_phase1"
INSTANCES = ("w30", "w45", "w60")


def _mean(values: list[float]) -> float:
    return statistics.fmean(values) if values else 0.0


def benchmark(iterations: int, output_dir: Path, seed: int = 20260719) -> list[dict[str, Any]]:
    rows = []
    rng = random.Random(seed)
    tracemalloc.start()
    for name in INSTANCES:
        welds, _ = load_frozen_weld_instance(
            str(ROOT / "data" / "instances" / "selected" / f"instance_{name}.xlsx")
        )
        config = CPLRDecoderConfig()
        t0 = time.perf_counter_ns()
        built = build_lineages(welds, eps=config.eps)
        index = build_reachable_side_index(built.lineages, config)
        lineages = apply_reachable_side_index(built.lineages, index)
        preprocessing_ns = time.perf_counter_ns() - t0
        samples = {key: [] for key in (
            "active_decode_ns", "route_sort_ns", "direction_dp_ns", "aggregation_ns",
            "cplr_total_ns", "standard_total_ns",
        )}
        for _ in range(iterations):
            chromosome = CPLRChromosome.from_mapping(
                rng.random(), rng.random(), {key: rng.random() for key in index.keys}
            )
            begin = time.perf_counter_ns()
            x_up, x_low = decode_boundaries(chromosome, config)
            mark = time.perf_counter_ns()
            tasks = decode_active_tasks(lineages, x_up, x_low, config)
            after_active = time.perf_counter_ns()
            routes = order_active_tasks(tasks, chromosome)
            after_sort = time.perf_counter_ns()
            stats = evaluate_routes(routes, config.motion_model)
            after_dp = time.perf_counter_ns()
            aggregate_system_metrics(stats)
            end = time.perf_counter_ns()
            samples["active_decode_ns"].append(after_active - mark)
            samples["route_sort_ns"].append(after_sort - after_active)
            samples["direction_dp_ns"].append(after_dp - after_sort)
            samples["aggregation_ns"].append(end - after_dp)
            samples["cplr_total_ns"].append(end - begin)

            standard_begin = time.perf_counter_ns()
            standard_routes, _ = assign_welds_to_robots_split(
                welds, x_up, x_low, eps=config.eps,
                min_subweld_length=config.min_subweld_length,
            )
            standard_stats = []
            for robot_tasks in standard_routes:
                ordered = sorted(robot_tasks, key=lambda task: (str(task.id),))
                _, route_stats = evaluate_order(
                    ordered, tuple(range(len(ordered))), config.motion_model
                )
                standard_stats.append(route_stats)
            totals = [float(item["total_time"]) for item in standard_stats]
            _ = (max(totals, default=0.0), min(totals, default=0.0))
            samples["standard_total_ns"].append(time.perf_counter_ns() - standard_begin)
        _, peak = tracemalloc.get_traced_memory()
        row = {
            "instance": name,
            "iterations": iterations,
            "original_weld_count": len(welds),
            "lineage_count": len(lineages),
            "reachable_side_key_count": len(index.keys),
            "reachable_side_pruning_ratio": index.pruning_ratio,
            "lineage_preprocessing_ms": preprocessing_ns / 1e6,
            **{f"mean_{key[:-3]}_ms": _mean(value) / 1e6 for key, value in samples.items()},
            "peak_memory_bytes": peak,
        }
        row["cplr_over_standard_ratio"] = (
            row["mean_cplr_total_ms"] / row["mean_standard_total_ms"]
            if row["mean_standard_total_ms"] else None
        )
        rows.append(row)
    tracemalloc.stop()
    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / "decoder_microbenchmark.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    summary = {
        "schema_version": 1,
        "seed": seed,
        "iterations_per_instance": iterations,
        "timing_clock": "time.perf_counter_ns",
        "read_only": True,
        "rows": rows,
        "interpretation": "CPLR speedup is not required in phase 1; ratios are descriptive only.",
    }
    (output_dir / "decoder_microbenchmark_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return rows


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--iterations", type=int, default=200)
    parser.add_argument("--seed", type=int, default=20260719)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    if args.iterations < 1:
        parser.error("--iterations must be positive")
    print(json.dumps(benchmark(args.iterations, args.output_dir, args.seed), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
