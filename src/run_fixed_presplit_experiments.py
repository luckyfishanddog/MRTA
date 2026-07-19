"""Sequential cold-process development runner for fixed-pre-split ABMA."""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence

from MRTA_ABMA_FIXED_PRESPLIT import FAST_VARIANT, REFERENCE_VARIANT, VARIANTS
from weld_fixed_presplit import file_sha256, load_fixed_instance, run_all
from project_paths import resolve_project_path


ROOT = Path(__file__).resolve().parents[1]
PYTHON = Path(r"D:\pybullet_test\.venv\Scripts\python.exe")
OUTPUT_ROOT = ROOT / "fixed_presplit_development"
RESULT_ROOT = ROOT / "results" / "fixed_presplit"
INSTANCES = ("w30", "w45", "w60")


def _write_csv(path: Path, rows: Sequence[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields = []
    for row in rows:
        for key in row:
            if key not in fields: fields.append(key)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader(); writer.writerows(rows)


def preprocessing_outputs() -> None:
    RESULT_ROOT.mkdir(parents=True, exist_ok=True)
    rows = run_all(ROOT / "data/instances/fixed_presplit")
    _write_csv(RESULT_ROOT / "fixed_presplit_preprocessing_summary.csv", rows)
    boundaries = {}
    for name in INSTANCES:
        _, metadata = load_fixed_instance(ROOT / f"data/instances/fixed_presplit/{name}_fixed_presplit.json")
        boundaries[name] = {
            "preprocessing_hash": metadata["preprocessing_hash"],
            "upper": metadata["boundary_candidates"]["upper"],
            "lower": metadata["boundary_candidates"]["lower"],
        }
    (RESULT_ROOT / "fixed_presplit_boundary_candidates.json").write_text(
        json.dumps(boundaries, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
    )


def _output_path(phase: str, instance: str, variant: str, seed: int, budget: int) -> Path:
    return OUTPUT_ROOT / "runs" / phase / instance / variant / f"seed_{seed}_budget_{budget}.json"


def run_one(phase: str, instance: str, variant: str, seed: int, budget: int,
            resume: bool = True) -> Dict[str, Any]:
    output = _output_path(phase, instance, variant, seed, budget)
    if resume and output.exists():
        value = json.loads(output.read_text(encoding="utf-8"))
        if value.get("budget") == budget and value.get("seed") == seed:
            print(f"RESUME {phase} {instance} {variant} seed={seed} budget={budget}", flush=True)
            return value
    output.parent.mkdir(parents=True, exist_ok=True)
    command = [str(PYTHON), str(ROOT / "src" / "MRTA_ABMA_FIXED_PRESPLIT.py"),
               "--instance", str(ROOT / f"data/instances/fixed_presplit/{instance}_fixed_presplit.json"),
               "--normalization", str(resolve_project_path("fixed_presplit_normalization.json")),
               "--variant", variant, "--seed", str(seed), "--budget", str(budget),
               "--output", str(output)]
    print(f"RUN {phase} {instance} {variant} seed={seed} budget={budget}", flush=True)
    completed = subprocess.run(command, cwd=ROOT, check=False)
    if completed.returncode != 0 or not output.exists():
        raise RuntimeError(f"cold process failed ({completed.returncode}): {' '.join(command)}")
    return json.loads(output.read_text(encoding="utf-8"))


def equivalence_fields(value: Dict[str, Any]) -> Dict[str, Any]:
    diagnostics = value["diagnostics"]
    return {
        "fitness": value["fitness"], "makespan": value["makespan"],
        "load_imbalance": value["load_imbalance"], "total_idle_distance": value["total_idle_distance"],
        "best_boundary": value["best_boundary"], "best_boundary_u": value["best_boundary_u"],
        "robot_order_ids": value["robot_order_ids"],
        "robot_direction_flags": value["robot_direction_flags"],
        "robot_stats": value["robot_stats"], "assignment_stats": value["assignment_stats"],
        "candidate_sequence_hash": diagnostics["candidate_sequence_hash"],
        "candidate_acceptance_hash": diagnostics["candidate_acceptance_hash"],
        "outer_rng_final_state_hash": diagnostics["outer_rng_final_state_hash"],
        "inner_rng_final_state_hash": diagnostics["inner_rng_final_state_hash"],
        "objective_evaluation_count": diagnostics["objective_evaluation_count"],
        "partition_cache_hits": diagnostics["partition_cache_hits"],
        "partition_cache_misses": diagnostics["partition_cache_misses"],
        "boundary_pair_visit_histogram": diagnostics["boundary_pair_visit_histogram"],
    }


def run_correctness(resume: bool = True) -> List[Dict[str, Any]]:
    rows = []
    for instance in INSTANCES:
        reference = run_one("correctness", instance, REFERENCE_VARIANT, 72, 100, resume)
        fast = run_one("correctness", instance, FAST_VARIANT, 72, 100, resume)
        a, b = equivalence_fields(reference), equivalence_fields(fast)
        mismatches = [key for key in a if a[key] != b[key]]
        rows.append({
            "instance": instance, "seed": 72, "budget": 100,
            "exact_equivalent": not mismatches,
            "mismatch_fields": json.dumps(mismatches, ensure_ascii=False),
            "reference_elapsed_time_s": reference["elapsed_time_s"],
            "exact_fast_elapsed_time_s": fast["elapsed_time_s"],
            "speedup_reference_over_fast": reference["elapsed_time_s"] / fast["elapsed_time_s"],
            "candidate_sequence_hash": a["candidate_sequence_hash"],
            "candidate_acceptance_hash": a["candidate_acceptance_hash"],
        })
    _write_csv(RESULT_ROOT / "fixed_presplit_equivalence_results.csv", rows)
    return rows


def run_runtime(resume: bool = True) -> List[Dict[str, Any]]:
    values = []
    for instance in INSTANCES:
        for seed in (72, 73, 74):
            for variant in VARIANTS:
                values.append(run_one("runtime", instance, variant, seed, 500, resume))
    summarize_runtime(values)
    return values


def _flat_run(value: Dict[str, Any]) -> Dict[str, Any]:
    d = value["diagnostics"]; primary = int(d["objective_evaluation_count"])
    route = int(d.get("route_evaluation_count", 0)); dp = int(d.get("direction_dp_count", 0))
    route_misses = int(d.get("route_cache_miss_count", 0))
    return {
        "instance": value["instance"], "variant": value["development_variant"],
        "seed": value["seed"], "budget": value["budget"],
        "elapsed_time_s": value["elapsed_time_s"], "peak_rss_mb": value["peak_rss_mb"],
        "fitness": value["fitness"], "makespan": value["makespan"],
        "primary_evaluation_count": primary,
        "partition_evaluation_count": d["partition_evaluations"],
        "partition_cache_hits": d["partition_cache_hits"], "partition_cache_misses": d["partition_cache_misses"],
        "unique_boundary_pair_count": d["unique_boundary_pair_count"],
        "duplicate_boundary_pair_visit_count": d["duplicate_boundary_pair_visit_count"],
        "route_evaluation_count": route, "direction_dp_count": dp,
        "route_cache_hit_count": d.get("route_cache_hit_count", 0),
        "route_local_cache_hit_count": d.get("route_local_cache_hit_count", 0),
        "route_cache_miss_count": route_misses,
        "route_cache_miss_rate_percent": 100.0 * route_misses / max(1, route),
        "pair_request_count": d.get("pair_request_count", 0),
        "pair_cache_hit_count": d.get("pair_cache_hit_count", 0),
        "pair_local_cache_hit_count": d.get("pair_local_cache_hit_count", 0),
        "pair_cache_miss_count": d.get("pair_cache_miss_count", 0),
        "zero_connected_transition_count": d.get("zero_connected_transition_count", 0),
        "selected_zero_connected_transition_count": d.get("selected_zero_connected_transition_count", 0),
        "seconds_per_primary": value["elapsed_time_s"] / max(1, primary),
        "route_evaluations_per_primary": route / max(1, primary),
        "direction_dp_per_primary": dp / max(1, primary),
        "candidate_sequence_hash": d["candidate_sequence_hash"],
        "candidate_acceptance_hash": d["candidate_acceptance_hash"],
        "result_file": str(_output_path("runtime", value["instance"], value["development_variant"], value["seed"], value["budget"])),
    }


def summarize_runtime(values: Sequence[Dict[str, Any]]) -> None:
    rows = [_flat_run(value) for value in values]
    _write_csv(RESULT_ROOT / "fixed_presplit_runtime_runs.csv", rows)
    amplification = [{key: row[key] for key in (
        "instance", "variant", "seed", "budget", "primary_evaluation_count",
        "partition_evaluation_count", "route_evaluation_count", "direction_dp_count",
        "seconds_per_primary", "route_evaluations_per_primary", "direction_dp_per_primary",
        "route_cache_miss_rate_percent", "pair_request_count", "pair_cache_hit_count",
        "pair_local_cache_hit_count", "pair_cache_miss_count")}
        for row in rows]
    _write_csv(RESULT_ROOT / "fixed_presplit_work_amplification.csv", amplification)
    summary: Dict[str, Any] = {"groups": {}, "matched_speedups": [], "gates": {}}
    for instance in INSTANCES:
        for variant in VARIANTS:
            group = [row for row in rows if row["instance"] == instance and row["variant"] == variant]
            if group:
                summary["groups"][f"{instance}/{variant}"] = {
                    key: statistics.median(float(row[key]) for row in group)
                    for key in ("elapsed_time_s", "seconds_per_primary", "route_evaluations_per_primary",
                                "direction_dp_per_primary", "route_cache_miss_rate_percent", "peak_rss_mb")
                }
    for instance in INSTANCES:
        for seed in (72, 73, 74):
            ref = next((row for row in rows if row["instance"] == instance and row["seed"] == seed and row["variant"] == REFERENCE_VARIANT), None)
            fast = next((row for row in rows if row["instance"] == instance and row["seed"] == seed and row["variant"] == FAST_VARIANT), None)
            if ref and fast:
                summary["matched_speedups"].append({"instance": instance, "seed": seed,
                    "speedup_reference_over_fast": ref["elapsed_time_s"] / fast["elapsed_time_s"]})
    speedups = [item["speedup_reference_over_fast"] for item in summary["matched_speedups"]]
    fast30 = summary["groups"].get(f"w30/{FAST_VARIANT}", {})
    fast60 = summary["groups"].get(f"w60/{FAST_VARIANT}", {})
    growth = fast60.get("seconds_per_primary", math.inf) / max(1e-300, fast30.get("seconds_per_primary", 0.0))
    summary["gates"] = {
        "median_speedup": statistics.median(speedups) if speedups else None,
        "median_speedup_at_least_2": bool(speedups and statistics.median(speedups) >= 2.0),
        "w60_fast_route_cache_miss_rate_below_82p52": fast60.get("route_cache_miss_rate_percent", math.inf) < 82.52,
        "w60_fast_direction_dp_per_primary_below_13643p03": fast60.get("direction_dp_per_primary", math.inf) < 13643.03,
        "w60_over_w30_fast_seconds_per_primary": growth,
        "w60_over_w30_fast_below_7p94": growth < 7.94,
    }
    (RESULT_ROOT / "fixed_presplit_runtime_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
    )


def run_conditional(resume: bool = True) -> List[Dict[str, Any]]:
    equivalence = list(csv.DictReader((RESULT_ROOT / "fixed_presplit_equivalence_results.csv").open(encoding="utf-8-sig")))
    if not equivalence or any(row["exact_equivalent"].lower() != "true" for row in equivalence):
        raise RuntimeError("budget-2000 gate blocked: correctness equivalence did not pass")
    runtime_rows = list(csv.DictReader((RESULT_ROOT / "fixed_presplit_runtime_runs.csv").open(encoding="utf-8-sig")))
    if len(runtime_rows) != 18:
        raise RuntimeError("budget-2000 gate blocked: 18 budget-500 runs are not complete")
    return [run_one("conditional_2000", instance, FAST_VARIANT, 72, 2000, resume) for instance in INSTANCES]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=("preprocess", "correctness", "runtime", "conditional", "all"), default="all")
    parser.add_argument("--no-resume", action="store_true")
    args = parser.parse_args(); resume = not args.no_resume
    if args.phase in ("preprocess", "all"): preprocessing_outputs()
    if args.phase in ("correctness", "all"): run_correctness(resume)
    if args.phase in ("runtime", "all"): run_runtime(resume)
    if args.phase in ("conditional", "all"): run_conditional(resume)


if __name__ == "__main__": main()
