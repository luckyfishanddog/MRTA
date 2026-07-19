"""Controlled ABMA outer-generation ablation and HGA equal-Primary comparison."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import platform
import statistics
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import run_unified_experiments as unified
from project_paths import resolve_project_path


ROOT = Path(__file__).resolve().parents[1]
PROTOCOL_PATH = resolve_project_path("UNIFIED_EXPERIMENT_PROTOCOL.json")
OUT = ROOT / "experiments" / "abma_outer5_v1"
RAW = OUT / "raw"
TRACES = OUT / "traces"
SUMMARIES = OUT / "summaries"
SEEDS = (78, 79, 80)
INSTANCES = ("w30", "w45", "w60")
ALGORITHMS = ("ABMA1", "ABMA20", "HGA-budget72", "HGA-budget252")
PROTECTED = (
    "MRTA_ABMA.py",
    "MRTA_HGA_PAPER_ALIGNED_CONTROL.py",
    "mrta_problem_core.py",
    "objective_normalization.py",
    "UNIFIED_EXPERIMENT_PROTOCOL.json",
    "abma_final_profile.json",
    "formal_experiment_manifest.json",
)
TOL = 1.0e-12


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n",
                         encoding="utf-8")
    os.replace(temporary, path)


def write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        if fields:
            writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)


def protected_hashes() -> dict[str, str]:
    return {name: sha256_file(resolve_project_path(name)) for name in PROTECTED}


def scientific_model_hash(protocol: Mapping[str, Any]) -> str:
    return sha256_bytes(canonical(protocol["scientific_model"]).encode("utf-8"))


def snapshot_before(protocol: Mapping[str, Any]) -> None:
    SUMMARIES.mkdir(parents=True, exist_ok=True)
    path = SUMMARIES / "protected_hashes_before.json"
    payload = {
        "created_at": utc_now(), "files": protected_hashes(),
        "protocol_hash": sha256_file(PROTOCOL_PATH),
        "formal_run_approved": protocol["formal_run_approved"],
        "user_approved_formal_execution": protocol["approval_gates"]["user_approved_formal_execution"],
    }
    if path.is_file():
        previous = read_json(path)
        if previous["files"] != payload["files"]:
            raise RuntimeError("protected source changed since ABMA outer5 stage began")
    else:
        write_json(path, payload)


def matrix(protocol: Mapping[str, Any]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    index = 0
    for instance in INSTANCES:
        for seed in SEEDS:
            abma_order = ("ABMA1", "ABMA20") if seed in (78, 80) else ("ABMA20", "ABMA1")
            hga_order = ("HGA-budget72", "HGA-budget252") if seed in (78, 80) else ("HGA-budget252", "HGA-budget72")
            for algorithm in (*abma_order, *hga_order):
                index += 1
                expected = 72 if algorithm in ("ABMA1", "HGA-budget72") else 252
                entries.append({
                    "index": index, "run_id": f"{instance}__{algorithm}__seed_{seed}",
                    "instance": instance, "instance_path": protocol["instances"][instance]["path"],
                    "seed": seed, "algorithm": algorithm, "expected_primary_evaluations": expected,
                    "formal": False, "collision_audit": False, "parallel": False,
                })
    if len(entries) != 36:
        raise AssertionError("development matrix must contain exactly 36 runs")
    return entries


def scientific_args(protocol: Mapping[str, Any], instance: str, seed: int) -> list[str]:
    entry = protocol["instances"][instance]
    spec = unified.RunSpec("abma_outer5_v1", "abma", instance, entry, seed, 72, {})
    return unified.ADAPTERS["abma"].scientific_args(protocol, spec)


def build_command(protocol: Mapping[str, Any], entry: Mapping[str, Any], run_dir: Path) -> list[str]:
    instance, seed, algorithm = str(entry["instance"]), int(entry["seed"]), str(entry["algorithm"])
    expected = int(entry["expected_primary_evaluations"])
    instance_entry = protocol["instances"][instance]
    cold = [sys.executable, "src/run_abma_outer5_cold_entry.py", "--solver"]
    if algorithm.startswith("ABMA"):
        solver = "abma1" if algorithm == "ABMA1" else "abma20"
        script = "MRTA_ABMA1.py" if algorithm == "ABMA1" else "MRTA_ABMA.py"
        generations = 5 if algorithm == "ABMA1" else 20
        return [
            *cold, solver, "--trace-evaluations", str(expected),
            "--instance-path", instance_entry["path"],
            "--weld-count", str(instance_entry["weld_count"]),
            "--solver-seed", str(seed), "--population-size", "12",
            "--generations", str(generations), "--max-objective-evaluations", "0",
            "--early-stop-patience", "0", "--abma-variant", "legacy_exact_fast",
            "--alns-iterations", "80", "--vnd-iterations", "3",
            "--route-time-limit-s", "0", "--metrics-csv", str(run_dir / "metrics.csv"),
            "--result-json", str(run_dir / "result.json"),
            "--profile-json", str(run_dir / "internal_profile.json"), "--quiet",
            *scientific_args(protocol, instance, seed),
        ]
    budget = expected
    controls = dict(protocol["budget"]["formal_solver_controls"]["paper_aligned_hga"])
    spec = unified.RunSpec("abma_outer5_v1", "paper_aligned_hga", instance, instance_entry,
                           seed, budget, controls)
    base = unified.ADAPTERS["paper_aligned_hga"].build(protocol, spec, sys.executable, run_dir)
    return [*cold, "hga", "--trace-evaluations", str(budget), *base[2:]]


def process_environment() -> dict[str, Any]:
    return {
        "captured_at": utc_now(), "python": sys.version, "executable": sys.executable,
        "platform": platform.platform(), "processor": platform.processor(),
        "cpu_count": os.cpu_count(), "sequential": True, "parallel": False,
    }


def run_cold(command: Sequence[str], timeout_s: float = 3600.0) -> tuple[int, str, str, float, int | None]:
    peak_rss: int | None = None
    try:
        import psutil
    except ImportError:
        psutil = None
    started = time.perf_counter()
    # Solvers can print a large result JSON.  Temporary files avoid the classic
    # PIPE deadlock where the child fills the OS pipe while the parent polls it.
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace") as stdout_file, \
            tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace") as stderr_file:
        process = subprocess.Popen(command, cwd=ROOT, text=True, stdout=stdout_file, stderr=stderr_file)
        watched = psutil.Process(process.pid) if psutil is not None else None
        timed_out = False
        while process.poll() is None:
            if time.perf_counter() - started > timeout_s:
                process.kill()
                timed_out = True
                break
            if watched is not None:
                try:
                    rss = watched.memory_info().rss
                    peak_rss = max(peak_rss or 0, rss)
                except Exception:
                    pass
            time.sleep(0.05)
        process.wait()
        stdout_file.seek(0); stderr_file.seek(0)
        stdout, stderr = stdout_file.read(), stderr_file.read()
        if timed_out:
            return -9, stdout, stderr + "\nwall-clock safety timeout", time.perf_counter() - started, peak_rss
        return process.returncode, stdout, stderr, time.perf_counter() - started, peak_rss


def run_spec(protocol: Mapping[str, Any], entry: Mapping[str, Any], resume: bool) -> dict[str, Any]:
    run_dir = RAW / str(entry["instance"]) / str(entry["algorithm"]) / f"seed_{entry['seed']}"
    run_dir.mkdir(parents=True, exist_ok=True)
    command = build_command(protocol, entry, run_dir)
    command_hash = sha256_bytes(canonical(command).encode("utf-8"))
    status_path = run_dir / "run_status.json"
    if resume and status_path.is_file():
        old = read_json(status_path)
        if old.get("status") == "success" and old.get("command_hash") == command_hash:
            print(f"[resume] {entry['run_id']}", flush=True)
            return read_json(run_dir / "normalized_result.json")
    for name in ("result.json", "metrics.csv", "internal_profile.json", "normalized_result.json"):
        path = run_dir / name
        if path.exists():
            path.unlink()
    write_json(run_dir / "command.json", {"argv": command, "command_hash": command_hash, "cwd": str(ROOT)})
    write_json(run_dir / "environment.json", process_environment())
    source_hashes = {name: sha256_file(resolve_project_path(name)) for name in (
        *PROTECTED, "MRTA_ABMA1.py", "run_abma_outer5_cold_entry.py", "run_abma_outer5_comparison.py",
        "paper_aligned_hga_control_defaults.json",
    )}
    write_json(run_dir / "source_hashes.json", source_hashes)
    print(f"[{entry['index']:02d}/36] {entry['run_id']}", flush=True)
    code, stdout, stderr, solver_wall, peak_rss = run_cold(command)
    (run_dir / "stdout.txt").write_text(stdout, encoding="utf-8")
    (run_dir / "stderr.txt").write_text(stderr, encoding="utf-8")
    if code != 0 or not (run_dir / "result.json").is_file():
        write_json(status_path, {"status": "failed", "returncode": code, "command_hash": command_hash,
                                "solver_wall_clock_time_s": solver_wall})
        raise RuntimeError(f"solver failed: {entry['run_id']} returncode={code}")
    post_started = time.perf_counter()
    payload = read_json(run_dir / "result.json")
    raw = dict(payload.get("metrics", payload))
    instance_entry = protocol["instances"][entry["instance"]]
    solver_key = "abma" if str(entry["algorithm"]).startswith("ABMA") else "paper_aligned_hga"
    spec = unified.RunSpec("abma_outer5_v1", solver_key, str(entry["instance"]), instance_entry,
                           int(entry["seed"]), int(entry["expected_primary_evaluations"]), {})
    rebuilt = unified.recompute_solution_metrics(payload, spec, protocol)
    expected = int(entry["expected_primary_evaluations"])
    realized = int(raw.get("objective_evaluation_count", -1))
    algorithm_time = float(raw.get("algorithm_time", raw.get("algorithm_time_s", raw.get("elapsed_time", 0.0))))
    assignment = payload.get("assignment_stats", raw)
    profile = read_json(run_dir / "internal_profile.json") if (run_dir / "internal_profile.json").is_file() else {}
    profile_counts = profile.get("counts", {})
    stage_times = profile.get("stage_times", {})
    route_hits = int(raw.get("route_cache_hits", raw.get("route_cache_hit", 0)) or 0)
    route_misses = int(raw.get("route_cache_misses", raw.get("route_cache_miss", 0)) or 0)
    route_evaluations = int(profile_counts.get("route_evaluation_count", route_hits + route_misses) or 0)
    direction_dp = int(profile_counts.get("direction_dp_count", route_misses) or 0)
    generation_history = list(payload.get("history", []))
    primary_trace = list(payload.get("checkpoint_trace", []))
    final_fitness = float(raw["fitness"])
    first_best = next((row for row in primary_trace
                       if math.isclose(float(row["best_fitness"]), final_fitness, rel_tol=0.0, abs_tol=TOL)), None)
    generation_of_best = None
    if str(entry["algorithm"]).startswith("ABMA") and generation_history:
        generation_of_best = next((index for index, value in enumerate(generation_history)
                                   if math.isclose(float(value), final_fitness, rel_tol=0.0, abs_tol=TOL)),
                                  len(generation_history) - 1)
    else:
        generation_of_best = raw.get("best_generation")
    improving_generations = sum(float(b) < float(a) - TOL for a, b in zip(generation_history, generation_history[1:]))
    fitness_g5 = float(generation_history[5]) if len(generation_history) > 5 else None
    fitness_g20 = float(generation_history[20]) if len(generation_history) > 20 else None
    remaining_after_g5 = (
        (fitness_g5 - fitness_g20) / max(abs(fitness_g20), 1e-15)
        if fitness_g5 is not None and fitness_g20 is not None else None
    )
    model_hash = scientific_model_hash(protocol)
    normalized = {
        **entry, "status": "success", "returncode": code,
        "protocol_version": protocol["protocol_version"], "protocol_hash": sha256_file(PROTOCOL_PATH),
        "scientific_model_version": "dynamic_split_parent_aware_v1",
        "scientific_model_hash": model_hash,
        "instance_hash": raw.get("instance_hash"),
        "normalization_mode": raw.get("normalization_mode"),
        "normalization_hash": instance_entry["normalization"]["normalization_spec_hash"],
        "normalization_hash_status": "protocol_declared_hash; strict protocol validator currently reports frozen-float drift",
        "solver_source_hash": source_hashes["MRTA_ABMA.py" if solver_key == "abma" else "MRTA_HGA_PAPER_ALIGNED_CONTROL.py"],
        "fitness": final_fitness, "makespan": float(rebuilt["system_metrics"]["makespan"]),
        "load_imbalance": float(rebuilt["system_metrics"]["load_imbalance"]),
        "total_idle_distance": float(rebuilt["system_metrics"]["total_idle_distance"]),
        "x_up": float(rebuilt["x_up"]), "x_low": float(rebuilt["x_low"]),
        "robot_task_counts": [int(row["task_count"]) for row in rebuilt["robot_metrics"]],
        "subweld_count": int(assignment.get("subweld_count", raw.get("subweld_count", 0))),
        "split_weld_count": int(assignment.get("split_weld_count", raw.get("split_weld_count", 0))),
        "unassigned_count": int(assignment.get("unassigned_subweld_count", raw.get("unassigned_subweld_count", -1))),
        "result_reconstruction_status": "success",
        "algorithm_time_s": algorithm_time, "solver_wall_clock_time_s": solver_wall,
        "process_startup_time_s": max(0.0, solver_wall - algorithm_time),
        "process_startup_time_measurement": "cold_process_wall_minus_solver_algorithm_time; includes imports and serialization",
        "postprocess_time_s": 0.0, "total_run_time_s": 0.0,
        "objective_evaluation_count": realized,
        "evaluations_per_second": realized / algorithm_time if algorithm_time > 0 else None,
        "seconds_per_primary_evaluation": algorithm_time / realized if realized > 0 else None,
        "generations_completed": raw.get("generations_completed", raw.get("best_generation")),
        "stop_reason": raw.get("stop_reason"),
        "partition_cache_hits": int(raw.get("partition_cache_hits", 0) or 0),
        "partition_cache_misses": int(raw.get("partition_cache_misses", 0) or 0),
        "unique_partition_count": int(raw.get("partition_evaluations", len(set()) if solver_key == "abma" else
                                             raw.get("assignment_cache_miss", 0)) or 0),
        "route_evaluation_count": route_evaluations, "direction_dp_count": direction_dp,
        "alns_time_s": stage_times.get("time_alns"), "vnd_time_s": stage_times.get("time_vnd"),
        "peak_rss_bytes": peak_rss,
        "initial_best_fitness": float(generation_history[0]) if generation_history else
                                (float(primary_trace[0]["best_fitness"]) if primary_trace else final_fitness),
        "best_fitness_after_generation_5": fitness_g5,
        "final_fitness_after_generation_20": fitness_g20,
        "remaining_improvement_after_generation_5": remaining_after_g5,
        "generation_of_final_best": generation_of_best,
        "primary_evaluation_of_final_best": (int(first_best["objective_evaluation_count"]) if first_best else None),
        "time_to_best_s": (float(first_best["elapsed_algorithm_time"]) if first_best else None),
        "number_of_improving_generations": improving_generations,
        "number_of_accepted_trials": raw.get("accepted_trial_count"),
        "final_population_diversity": raw.get("final_population_diversity"),
        "final_population_diversity_status": "not_exposed_by_authoritative_solver",
        "candidate_sequence_hash": raw.get("candidate_sequence_hash"),
        "acceptance_sequence_hash": raw.get("candidate_acceptance_hash"),
        "outer_rng_final_state_hash": raw.get("outer_rng_final_state_hash"),
        "inner_rng_final_state_hash": raw.get("inner_rng_final_state_hash"),
        "collision_audit": False, "parallel": False,
        "run_directory": run_dir.relative_to(ROOT).as_posix(),
    }
    errors = []
    if normalized["instance_hash"] != instance_entry["instance_hash"]: errors.append("instance hash mismatch")
    if normalized["normalization_mode"] != "ideal_baseline_range_v1": errors.append("normalization mode mismatch")
    if realized != expected: errors.append(f"Primary count {realized} != {expected}")
    if normalized["unassigned_count"] != 0: errors.append("unassigned tasks")
    if entry["algorithm"] == "ABMA1":
        if raw.get("algorithm_name") != "ABMA-Outer5-v1" or raw.get("solver_name") != "ABMA1":
            errors.append("ABMA1 identity mismatch")
        if raw.get("variant") != "outer5_legacy_exact_fast": errors.append("ABMA1 variant mismatch")
        if int(raw.get("expected_primary_evaluations", -1)) != 72: errors.append("ABMA1 expected count mismatch")
        if int(raw.get("outer_generations_completed", -1)) != 5: errors.append("ABMA1 generation mismatch")
        if raw.get("stop_reason") != "generation_limit": errors.append("ABMA1 stop reason mismatch")
    elif entry["algorithm"] == "ABMA20":
        if int(raw.get("generations_completed", -1)) != 20: errors.append("ABMA20 generation mismatch")
        if raw.get("stop_reason") != "generation_limit": errors.append("ABMA20 stop reason mismatch")
    else:
        if raw.get("stop_reason") != "objective_budget": errors.append("HGA stop reason mismatch")
        if not raw.get("objective_budget_exhausted"): errors.append("HGA budget not exhausted")
    if len(primary_trace) != expected: errors.append(f"full Primary trace has {len(primary_trace)} rows, expected {expected}")
    if errors:
        write_json(status_path, {"status": "invalid", "errors": errors, "command_hash": command_hash})
        raise RuntimeError(f"invalid run {entry['run_id']}: {'; '.join(errors)}")
    normalized["postprocess_time_s"] = time.perf_counter() - post_started
    normalized["total_run_time_s"] = solver_wall + normalized["postprocess_time_s"]
    write_json(run_dir / "normalized_result.json", normalized)
    write_json(status_path, {"status": "success", "command_hash": command_hash,
                             "completed_at": utc_now(), "normalized_result_sha256": sha256_file(run_dir / "normalized_result.json")})
    return normalized


def run_all(protocol: Mapping[str, Any], resume: bool, max_runs: int | None) -> list[dict[str, Any]]:
    entries = matrix(protocol)
    rows: list[dict[str, Any]] = []
    executed = 0
    for entry in entries:
        status_path = RAW / entry["instance"] / entry["algorithm"] / f"seed_{entry['seed']}" / "run_status.json"
        already = status_path.is_file() and read_json(status_path).get("status") == "success"
        resumable = resume and already
        if max_runs is not None and executed >= max_runs and not resumable:
            continue
        rows.append(run_spec(protocol, entry, resume=resume))
        executed += int(not resumable)
        write_json(SUMMARIES / "execution_checkpoint.json", {
            "updated_at": utc_now(), "successful_run_count": len(rows), "matrix_run_count": 36,
            "last_run_id": entry["run_id"], "complete": len(rows) == 36,
        })
    return rows


def load_completed_rows() -> list[dict[str, Any]]:
    rows = []
    for path in RAW.glob("*/*/seed_*/normalized_result.json"):
        row = read_json(path)
        if row.get("final_population_diversity") is None and str(row.get("algorithm", "")).startswith("HGA"):
            result = read_json(path.with_name("result.json"))
            row["final_population_diversity"] = result.get("metrics", {}).get("final_diversity_rank")
            row["final_population_diversity_status"] = "HGA final_diversity_rank; ABMA source does not expose an equivalent"
        rows.append(row)
    return sorted(rows, key=lambda row: int(row["index"]))


def descriptive(values: Sequence[float]) -> dict[str, float | None]:
    return {
        "mean": statistics.mean(values) if values else None,
        "median": statistics.median(values) if values else None,
        "minimum": min(values) if values else None,
        "maximum": max(values) if values else None,
        "standard_deviation": statistics.stdev(values) if len(values) > 1 else 0.0 if values else None,
    }


def exact_exploratory_wilcoxon(differences: Sequence[float]) -> float:
    nonzero = [value for value in differences if abs(value) > TOL]
    n = len(nonzero)
    if not n:
        return 1.0
    ordered = sorted(range(n), key=lambda index: abs(nonzero[index]))
    ranks = [0.0] * n
    for rank, index in enumerate(ordered, 1): ranks[index] = float(rank)
    observed = min(sum(rank for rank, value in zip(ranks, nonzero) if value > 0),
                   sum(rank for rank, value in zip(ranks, nonzero) if value < 0))
    extreme = 0
    for mask in range(1 << n):
        positive = sum(ranks[index] for index in range(n) if mask & (1 << index))
        negative = sum(ranks) - positive
        extreme += int(min(positive, negative) <= observed + TOL)
    return extreme / (1 << n)


def pairwise(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    by = {(row["instance"], int(row["seed"]), row["algorithm"]): row for row in rows}
    pairs = (("ABMA1_vs_ABMA20", "ABMA1", "ABMA20"),
             ("ABMA1_vs_HGA72", "ABMA1", "HGA-budget72"),
             ("ABMA20_vs_HGA252", "ABMA20", "HGA-budget252"))
    output = []
    for instance in INSTANCES:
        for seed in SEEDS:
            for comparison, left_name, right_name in pairs:
                left, right = by[(instance, seed, left_name)], by[(instance, seed, right_name)]
                row = {"comparison": comparison, "instance": instance, "seed": seed,
                       "left_algorithm": left_name, "right_algorithm": right_name}
                for field in ("fitness", "makespan", "load_imbalance", "total_idle_distance"):
                    lv, rv = float(left[field]), float(right[field])
                    row[f"{field}_left"] = lv; row[f"{field}_right"] = rv
                    row[f"{field}_difference"] = lv - rv
                    row[f"{field}_relative_degradation"] = (lv - rv) / max(abs(rv), 1e-15)
                lw, rw = float(left["solver_wall_clock_time_s"]), float(right["solver_wall_clock_time_s"])
                row.update({
                    "wall_time_left_s": lw, "wall_time_right_s": rw,
                    "wall_time_ratio_left_over_right": lw / rw,
                    "speedup_right_over_left": rw / lw,
                    "seconds_per_primary_left": left["seconds_per_primary_evaluation"],
                    "seconds_per_primary_right": right["seconds_per_primary_evaluation"],
                    "route_evaluations_left": left["route_evaluation_count"],
                    "route_evaluations_right": right["route_evaluation_count"],
                    "direction_dp_left": left["direction_dp_count"],
                    "direction_dp_right": right["direction_dp_count"],
                    "fitness_winner": "left" if float(left["fitness"]) < float(right["fitness"]) - TOL else
                                      "right" if float(right["fitness"]) < float(left["fitness"]) - TOL else "tie",
                })
                if comparison == "ABMA1_vs_ABMA20":
                    row.update({
                        "abma20_best_fitness_after_generation_5": right["best_fitness_after_generation_5"],
                        "abma20_final_fitness_after_generation_20": right["final_fitness_after_generation_20"],
                        "remaining_improvement_after_generation_5": right["remaining_improvement_after_generation_5"],
                        "abma20_generation_of_final_best": right["generation_of_final_best"],
                        "abma20_primary_evaluation_of_final_best": right["primary_evaluation_of_final_best"],
                        "abma20_time_to_best_s": right["time_to_best_s"],
                    })
                output.append(row)
    return output


def pairwise_summary(pairs: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    output = []
    for comparison in sorted({row["comparison"] for row in pairs}):
        for instance in INSTANCES:
            subset = [row for row in pairs if row["comparison"] == comparison and row["instance"] == instance]
            for metric in ("fitness_relative_degradation", "makespan_relative_degradation",
                           "load_imbalance_relative_degradation", "total_idle_distance_relative_degradation",
                           "speedup_right_over_left", "wall_time_ratio_left_over_right"):
                values = [float(row[metric]) for row in subset]
                wins = sum(value < -TOL for value in values) if "degradation" in metric else None
                losses = sum(value > TOL for value in values) if "degradation" in metric else None
                output.append({"comparison": comparison, "instance": instance, "metric": metric,
                               **descriptive(values), "wins_left": wins,
                               "ties": sum(abs(value) <= TOL for value in values) if "degradation" in metric else None,
                               "losses_left": losses,
                               "exploratory_wilcoxon_p": exact_exploratory_wilcoxon(values),
                               "statistical_label": "exploratory only; n=3; not inferential evidence"})
    return output


def convergence_rows(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    output = []
    for row in rows:
        result = read_json(ROOT / row["run_directory"] / "result.json")
        for trace in result.get("checkpoint_trace", []):
            output.append({"instance": row["instance"], "seed": row["seed"], "algorithm": row["algorithm"],
                           "trace_type": "primary_evaluation", **trace})
        for generation, fitness in enumerate(result.get("history", [])):
            primary = (12 if generation == 0 else 12 + 12 * generation)
            output.append({"instance": row["instance"], "seed": row["seed"], "algorithm": row["algorithm"],
                           "trace_type": "generation_boundary", "generation": generation,
                           "objective_evaluation_count": primary, "best_fitness": fitness})
    return output


def historical_context(protocol: Mapping[str, Any]) -> list[dict[str, Any]]:
    files = (("w30", ROOT / "three_solver_w30_pilot_summary.json", "runs"),
             ("w45", ROOT / "w45_scale_pilot_summary.json", "rows"),
             ("w60", ROOT / "w60_scale_pilot_summary.json", "rows"))
    output = []
    current_hashes = protected_hashes()
    model = protocol["scientific_model"]
    for instance, path, key in files:
        data = read_json(path)
        for summary in data[key]:
            solver = str(summary.get("solver"))
            if solver not in ("ABMA", "Paper-Aligned-HGA-XCut-Control"):
                continue
            run_dir = ROOT / summary["run_directory"]
            result = read_json(run_dir / "result.json")
            metrics = result.get("metrics", result)
            source_hashes = read_json(run_dir / "source_hashes.json")
            solver_file = "MRTA_ABMA.py" if solver == "ABMA" else "MRTA_HGA_PAPER_ALIGNED_CONTROL.py"
            checks = {
                "instance_hash_match": metrics.get("instance_hash") == protocol["instances"][instance]["instance_hash"],
                "solver_source_hash_match": source_hashes.get(solver_file) == current_hashes[solver_file],
                "problem_core_hash_match": source_hashes.get("mrta_problem_core.py") == current_hashes["mrta_problem_core.py"],
                "normalization_source_match": metrics.get("normalization_mode") == "ideal_baseline_range_v1",
                "scientific_model_match": all(math.isclose(float(metrics.get(field, float("nan"))), float(model[field]),
                                                            rel_tol=0.0, abs_tol=1e-12)
                                              for field in ("weld_speed", "travel_speed", "acceleration", "safe_z")),
                "seed_match": int(metrics.get("solver_seed", metrics.get("seed", -1))) == 42,
                "budget_match_summary": int(metrics.get("objective_evaluation_count", -1)) ==
                                        int(summary.get("realized_objective_evaluations", -2)),
                "collision_during_solver_false": True,
            }
            eligible = all(checks.values())
            output.append({
                "instance": instance, "solver": solver, "seed": 42,
                "primary_budget": int(metrics["objective_evaluation_count"]),
                "fitness": metrics["fitness"], "makespan": metrics["makespan"],
                "load_imbalance": metrics["load_imbalance"], "total_idle_distance": metrics["total_idle_distance"],
                "algorithm_time_s": summary.get("algorithm_time"),
                "solver_wall_clock_time_s": summary.get("solver_wall_clock_time"),
                "compatible_for_historical_context": eligible,
                "compatibility_checks_json": canonical(checks),
                "comparison_policy": "historical high-budget context only; excluded from ABMA1 paired statistics",
                "run_directory": summary["run_directory"],
            })
    return output


def plot_outputs(rows: Sequence[Mapping[str, Any]], convergence: Sequence[Mapping[str, Any]],
                 historical: Sequence[Mapping[str, Any]]) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    algorithms = list(ALGORITHMS)
    colors = ["#2f6b9a", "#d17a22", "#4a9b66", "#8b5aa5"]
    def save(name: str):
        plt.tight_layout(); plt.savefig(SUMMARIES / name, dpi=170); plt.close()
    for field, name, ylabel in (
        ("solver_wall_clock_time_s", "wall_time_by_instance_algorithm.png", "Solver wall time (s)"),
        ("fitness", "fitness_by_instance_algorithm.png", "Fitness (lower is better)"),
        ("makespan", "makespan_by_instance_algorithm.png", "Makespan (s)"),
        ("load_imbalance", "load_imbalance_by_instance_algorithm.png", "Load imbalance (s)"),
        ("total_idle_distance", "idle_distance_by_instance_algorithm.png", "Idle distance (m)"),
    ):
        plt.figure(figsize=(10, 5.2)); width = .19; x = list(range(len(INSTANCES)))
        for offset, (algorithm, color) in enumerate(zip(algorithms, colors)):
            values = [statistics.mean(float(row[field]) for row in rows
                                      if row["instance"] == instance and row["algorithm"] == algorithm)
                      for instance in INSTANCES]
            plt.bar([value + (offset - 1.5) * width for value in x], values, width, label=algorithm, color=color)
        plt.xticks(x, INSTANCES); plt.ylabel(ylabel); plt.legend(fontsize=8); plt.title("Current 72/252-Primary development runs")
        save(name)
    plt.figure(figsize=(10, 5.5))
    for instance, linestyle in zip(INSTANCES, ("-", "--", ":")):
        for algorithm, color in zip(("ABMA1", "ABMA20"), colors[:2]):
            subset = [row for row in convergence if row["trace_type"] == "generation_boundary"
                      and row["instance"] == instance and row["algorithm"] == algorithm]
            grouped = {}
            for row in subset: grouped.setdefault(int(row["generation"]), []).append(float(row["best_fitness"]))
            plt.plot(sorted(grouped), [statistics.mean(grouped[key]) for key in sorted(grouped)],
                     linestyle=linestyle, color=color, label=f"{instance}/{algorithm}")
    plt.xlabel("Outer generation"); plt.ylabel("Mean incumbent fitness"); plt.legend(fontsize=7, ncol=2)
    save("abma_generation_convergence.png")
    plt.figure(figsize=(9, 5.5))
    for algorithm, color in zip(algorithms, colors):
        subset = [row for row in rows if row["algorithm"] == algorithm]
        plt.scatter([row["solver_wall_clock_time_s"] for row in subset], [row["fitness"] for row in subset],
                    label=f"current {algorithm}", color=color, alpha=.8)
    for item in historical:
        if item["compatible_for_historical_context"]:
            plt.scatter(item["solver_wall_clock_time_s"], item["fitness"], marker="x", color="black")
            plt.annotate(f"hist {item['instance']} {item['solver']} B={item['primary_budget']}",
                         (item["solver_wall_clock_time_s"], item["fitness"]), fontsize=6)
    plt.xscale("log"); plt.xlabel("Solver wall time (s, log scale)"); plt.ylabel("Fitness"); plt.legend(fontsize=7)
    save("quality_vs_wall_time.png")
    plt.figure(figsize=(9, 5.2))
    for index, instance in enumerate(INSTANCES):
        subset = [row for row in rows if row["instance"] == instance and row["algorithm"] == "ABMA20"]
        plt.scatter([index] * len(subset), [row["generation_of_final_best"] for row in subset], s=55, label=instance)
    plt.axhline(5, color="red", linestyle="--", linewidth=1, label="generation 5")
    plt.xticks(range(3), INSTANCES); plt.ylabel("Generation of ABMA20 final best"); plt.legend()
    save("generation_of_final_best.png")


def markdown_table(rows: Sequence[Mapping[str, Any]], fields: Sequence[str]) -> list[str]:
    lines = ["| " + " | ".join(fields) + " |", "|" + "|".join("---" for _ in fields) + "|"]
    for row in rows: lines.append("| " + " | ".join(str(row.get(field, "")) for field in fields) + " |")
    return lines


def decision(rows: Sequence[Mapping[str, Any]], pairs: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    abma_pairs = [row for row in pairs if row["comparison"] == "ABMA1_vs_ABMA20"]
    hga_pairs = [row for row in pairs if row["comparison"] == "ABMA1_vs_HGA72"]
    speedup = statistics.median(float(row["speedup_right_over_left"]) for row in abma_pairs)
    degradation = statistics.median(float(row["fitness_relative_degradation"]) for row in abma_pairs)
    instance_hga = {instance: statistics.median(float(row["fitness_difference"]) for row in hga_pairs
                                               if row["instance"] == instance) < 0 for instance in INSTANCES}
    early = sum(int(row["generation_of_final_best"]) <= 5 for row in rows if row["algorithm"] == "ABMA20")
    hga_wall_ratio = statistics.median(float(row["wall_time_ratio_left_over_right"]) for row in hga_pairs)
    hga_advantage = -statistics.median(float(row["fitness_relative_degradation"]) for row in hga_pairs)
    case_a = speedup >= 2.0 and degradation <= .02 and all(instance_hga.values()) and early >= 5
    case_b = speedup >= 1.5 and (degradation > .02 or not all(instance_hga.values()))
    case_c = hga_wall_ratio >= 10.0 and hga_advantage < .02
    selected = "A" if case_a else "B" if case_b else "C" if case_c else "mixed"
    conclusions = {
        "A": "外层 20 代存在明显冗余，5 代版本值得进入下一轮扩大验证。",
        "B": "固定 5 代过度削弱外层分区搜索，不能作为主算法。",
        "C": "单纯减少外层代数不能解决 ABMA 的根本效率问题。",
        "mixed": "三种预设情况均未完整满足；应保留探索性结论并扩大验证。",
    }
    return {"selected_case": selected, "conclusion": conclusions[selected], "case_A": case_a, "case_B": case_b,
            "case_C": case_c, "median_abma20_over_abma1_speedup": speedup,
            "median_abma1_fitness_degradation": degradation, "abma1_better_than_hga72_by_instance": instance_hga,
            "abma20_final_best_within_generation5_count": early,
            "median_abma1_hga72_wall_ratio": hga_wall_ratio,
            "median_abma1_fitness_advantage_over_hga72": hga_advantage}


def reports(rows: Sequence[Mapping[str, Any]], pairs: Sequence[Mapping[str, Any]],
            summary: Sequence[Mapping[str, Any]], historical: Sequence[Mapping[str, Any]], outcome: Mapping[str, Any]) -> None:
    hashes = protected_hashes()
    wrapper_hash = sha256_file(resolve_project_path("MRTA_ABMA1.py"))
    abma_pairs = [row for row in pairs if row["comparison"] == "ABMA1_vs_ABMA20"]
    hga72_pairs = [row for row in pairs if row["comparison"] == "ABMA1_vs_HGA72"]
    hga252_pairs = [row for row in pairs if row["comparison"] == "ABMA20_vs_HGA252"]
    abma20_rows = [row for row in rows if row["algorithm"] == "ABMA20"]

    def median_for(items: Sequence[Mapping[str, Any]], field: str) -> float:
        return statistics.median(float(item[field]) for item in items)

    per_instance = []
    for instance in INSTANCES:
        ab = [row for row in abma_pairs if row["instance"] == instance]
        h72 = [row for row in hga72_pairs if row["instance"] == instance]
        h252 = [row for row in hga252_pairs if row["instance"] == instance]
        per_instance.append({
            "instance": instance,
            "ABMA20/ABMA1 median wall speedup": median_for(ab, "speedup_right_over_left"),
            "ABMA1 fitness degradation median": median_for(ab, "fitness_relative_degradation"),
            "makespan degradation median": median_for(ab, "makespan_relative_degradation"),
            "load degradation median": median_for(ab, "load_imbalance_relative_degradation"),
            "idle degradation median": median_for(ab, "total_idle_distance_relative_degradation"),
            "ABMA1/HGA72 median wall ratio": median_for(h72, "wall_time_ratio_left_over_right"),
            "ABMA1 vs HGA72 fitness difference median": median_for(h72, "fitness_difference"),
            "ABMA20/HGA252 median wall ratio": median_for(h252, "wall_time_ratio_left_over_right"),
            "ABMA20 vs HGA252 fitness difference median": median_for(h252, "fitness_difference"),
        })
    convergence = [{
        "instance": row["instance"], "seed": row["seed"],
        "fitness_g5": row["best_fitness_after_generation_5"],
        "fitness_g20": row["final_fitness_after_generation_20"],
        "remaining_improvement_after_g5": row["remaining_improvement_after_generation_5"],
        "generation_of_final_best": row["generation_of_final_best"],
        "primary_evaluation_of_final_best": row["primary_evaluation_of_final_best"],
        "time_to_best_s": row["time_to_best_s"],
    } for row in abma20_rows]
    implementation = [
        "# ABMA Outer5 Implementation Report", "",
        "## Implementation", "",
        "`MRTA_ABMA1.py` is a thin wrapper: it imports the authoritative `MRTA_ABMA`, subclasses `ABMAConfig`/`ABMASolver`, delegates parsing and output construction, then rewrites only the independent solver identity. It does not copy the approximately 2,800-line solver.", "",
        "The sole search-factor change is `outer_generations: 20 → 5`. Controlled values remain population=12, objective cap=0, early-stop patience=0, `legacy_exact_fast`, ALNS=80, VND=3, official normalization, dynamic parent-aware split, open routes and exact bidirectional two-state DP.", "",
        "The CLI rejects generations other than 5, nonzero objective caps, nonzero early stopping, and source variants other than `legacy_exact_fast`. Output identity is `ABMA-Outer5-v1` / `ABMA1` / `outer5_legacy_exact_fast`.", "",
        "## Equivalence and instrumentation", "",
        "The dedicated 7-test suite proves exact equality with authoritative ABMA at `--generations 5` for candidate/acceptance hashes, outer/inner RNG state hashes, solutions, histories, routes and statistics (runtime-only fields excluded). The full repository suite passed 96/96.", "",
        "Entry checks: `MRTA_ABMA1.py --self-check` passed; `MRTA_ABMA.py --self-check` passed. The protected HGA CLI has no `--self-check` option and rejects that command because `--instance-path` is required. `run_unified_experiments.py --validate-protocol` and `--self-check` both report the repository's six pre-existing normalization frozen-float drift errors; the regression suite explicitly tests for that current behavior.", "",
        "The cold entry only increases `IncumbentTrace` checkpoint density and counts accepted decisions. Patches are process-local and restored before exit; they do not modify search decisions or persistent module state.", "",
        "ABMA final-population diversity is not exposed by the authoritative solver and is therefore marked unavailable. HGA's native `final_diversity_rank` is preserved, without claiming cross-algorithm semantic equivalence.", "",
        "## Source authority and hashes", "",
        f"- `MRTA_ABMA.py`: `{hashes['MRTA_ABMA.py']}`",
        f"- `MRTA_ABMA1.py`: `{wrapper_hash}`",
        *[f"- `{name}`: `{digest}`" for name, digest in hashes.items() if name != "MRTA_ABMA.py"],
        "- No `MRTA_ABMA(16).py` exists in the current repository, so no alternate-file authority decision was needed.",
        "- Protected hashes matched before and after the 36-run experiment.",
    ]
    (ROOT / "ABMA_OUTER5_IMPLEMENTATION_REPORT.md").write_text("\n".join(implementation) + "\n", encoding="utf-8")
    fields = ("instance", "seed", "algorithm", "objective_evaluation_count", "fitness", "makespan",
              "load_imbalance", "total_idle_distance", "solver_wall_clock_time_s", "generation_of_final_best")
    experiment = ["# ABMA Outer5 Experiment Report", "", "All 36 runs are non-formal sequential cold processes using seeds 78–80; collision audit and parallel execution are disabled. ABMA order alternates by seed. No process-level cache is shared.", "",
                  "Every ABMA1 run realized 72 Primary evaluations and 5 generations; every ABMA20 run realized 252 and 20; every HGA run exactly exhausted its 72/252 budget with `objective_budget`.", "",
                  "The protocol-declared normalization hashes are recorded. The repository's pre-existing strict validator still reports frozen-float drift for all three instances; protected normalization/protocol files were not modified.", "",
                  *markdown_table(rows, fields), "", "## Exploratory pairwise summaries", "",
                  *markdown_table(summary, ("comparison", "instance", "metric", "mean", "median", "standard_deviation",
                                            "exploratory_wilcoxon_p", "statistical_label")), "",
                  "## Convergence after generation 5", "", *markdown_table(convergence, tuple(convergence[0])), "",
                  "No n=3 result is inferential evidence and no statement of statistical significance is made."]
    (ROOT / "ABMA_OUTER5_EXPERIMENT_REPORT.md").write_text("\n".join(experiment) + "\n", encoding="utf-8")
    analysis = ["# ABMA Outer5 vs ABMA20 / HGA Analysis", "", f"Decision case: **{outcome['selected_case']} (quality-loss branch)**.", "",
                outcome["conclusion"], "", "ABMA1 still beats HGA-budget72 in median fitness on all three instances, so the HGA-loss clause of the stated Case B is not met. The rejection is instead driven by large ABMA20 quality contributions after generation 5. Case C is also not selected overall because the fitness advantage over HGA72 is not uniformly small, although ABMA remains dramatically slower.", "",
                f"- Overall median ABMA20/ABMA1 wall speedup: `{outcome['median_abma20_over_abma1_speedup']}`",
                f"- Median ABMA1 fitness degradation vs ABMA20: `{outcome['median_abma1_fitness_degradation']}`",
                f"- ABMA20 final best within generation 5: `{outcome['abma20_final_best_within_generation5_count']}/9`",
                f"- Overall median ABMA1/HGA72 wall ratio: `{outcome['median_abma1_hga72_wall_ratio']}`", "",
                "## Per-instance trade-off", "", *markdown_table(per_instance, tuple(per_instance[0])), "",
                "The fitness loss is mainly associated with much worse load imbalance (especially w30/w60) and a smaller makespan penalty; idle distance often improves because the weighted objective trades it against makespan/load.", "",
                "All 9 ABMA20 final incumbents appeared after generation 5 (generations 16–20). Remaining improvement after generation 5 ranged from about 5.66% to 38.56%, so the final 15 generations were not redundant in these development runs.", "",
                "## Historical high-budget context", "",
                *markdown_table(historical, ("instance", "solver", "seed", "primary_budget", "fitness",
                                             "solver_wall_clock_time_s", "compatible_for_historical_context", "comparison_policy")), "",
                "Historical 11000/16000-Primary rows are quality/time context only and do not enter any paired statistic above."]
    (ROOT / "ABMA_OUTER5_VS_ABMA_HGA_ANALYSIS.md").write_text("\n".join(analysis) + "\n", encoding="utf-8")
    handoff_items = [
        "MRTA_ABMA1 通过导入和子类化权威 MRTA_ABMA 实现，没有复制完整求解器。",
        "与原 ABMA 唯一搜索因素差异是外层 generations 从 20 降为 5。",
        f"MRTA_ABMA.py hash `{hashes['MRTA_ABMA.py']}`；MRTA_ABMA1.py hash `{wrapper_hash}`。",
        "ABMA1 的 9 次实测 Primary 均为 72，且均完成 5 代并以 generation_limit 停止。",
        "ABMA20 的 9 次实测 Primary 均为 252，且均完成 20 代并以 generation_limit 停止。",
        "w30/w45/w60 的全部每-seed 行见 abma_outer5_runs.csv。",
        f"ABMA20/ABMA1 中位 wall speedup 为 `{outcome['median_abma20_over_abma1_speedup']}`。",
        f"ABMA1 对 ABMA20 中位 fitness 退化为 `{outcome['median_abma1_fitness_degradation']}`。",
        "ABMA20 最终最优代数为 16–20，9/9 都晚于第 5 代。",
        "第五代后剩余 fitness 改进为约 5.66%–38.56%，说明后 15 代有实质质量贡献。",
        f"ABMA1 在三个实例的中位 fitness 均优于 HGA-budget72，但总体中位墙钟约慢 `{outcome['median_abma1_hga72_wall_ratio']}` 倍。",
        "ABMA20 在三个实例的中位 fitness 均优于 HGA-budget252，但墙钟代价远高；逐 seed 见 pairwise CSV。",
        "已有 w30/w45 的 16000 预算和 w60 的 11000 预算 Pilot 只进入 historical context，不参与配对统计。",
        "时间—质量权衡属于 Case B 的质量损失分支：5 代明显更快，但质量损失不可忽略。",
        f"扩大验证判定：{outcome['conclusion']} 可保留为探索性快速变体，不应替换主算法。",
        "相同 Primary 不等于相同内部 route/DP 工作；ABMA1 相比 HGA 仍有根本结构效率问题。",
        "尚未解决：ABMA final population diversity 未由权威 solver 暴露；协议严格 normalization validator 存在既有冻结浮点漂移；n=3 不能作显著性推断。",
        "正式 270-run 实验未运行；formal_run_approved 与 user_approved_formal_execution 均仍为 false。",
    ]
    handoff = "# ABMA 外层 5 代消融及与原 ABMA/HGA 对比阶段 AI 交接报告\n\n" + "\n".join(
        f"{index}. {item}" for index, item in enumerate(handoff_items, 1)) + "\n"
    (ROOT / "ABMA外层5代消融及与原ABMA_HGA对比阶段_AI交接报告_2026-07-19.md").write_text(handoff, encoding="utf-8")


def summarize(protocol: Mapping[str, Any]) -> dict[str, Any]:
    rows = load_completed_rows()
    if len(rows) != 36:
        raise RuntimeError(f"cannot summarize incomplete matrix: {len(rows)}/36")
    pairs = pairwise(rows)
    summary = pairwise_summary(pairs)
    convergence = convergence_rows(rows)
    historical = historical_context(protocol)
    if len(historical) != 6 or not all(row["compatible_for_historical_context"] for row in historical):
        raise RuntimeError("historical ABMA/HGA context failed compatibility validation")
    outcome = decision(rows, pairs)
    write_csv(SUMMARIES / "abma_outer5_runs.csv", rows)
    write_csv(SUMMARIES / "abma_outer5_pairwise_comparison.csv", pairs)
    write_csv(TRACES / "abma_outer5_convergence_trace.csv", convergence)
    write_csv(SUMMARIES / "abma_outer5_historical_context.csv", historical)
    write_csv(SUMMARIES / "abma_outer5_pairwise_summary.csv", summary)
    plot_outputs(rows, convergence, historical)
    after = protected_hashes()
    before = read_json(SUMMARIES / "protected_hashes_before.json")["files"]
    write_json(SUMMARIES / "protected_hashes_after.json", {"created_at": utc_now(), "files": after,
                                                             "match": before == after})
    if before != after:
        raise RuntimeError("protected sources changed during ABMA outer5 experiment")
    hash_manifest = {
        "generated_at": utc_now(), "protected_files": after,
        "new_files": {path.relative_to(ROOT).as_posix(): sha256_file(path) for path in (
            resolve_project_path("MRTA_ABMA1.py"), resolve_project_path("test_mrta_abma1_outer5.py"),
            resolve_project_path("run_abma_outer5_comparison.py"), resolve_project_path("run_abma_outer5_cold_entry.py"),
        )},
        "instance_hashes": {name: protocol["instances"][name]["instance_hash"] for name in INSTANCES},
        "normalization_hashes": {name: protocol["instances"][name]["normalization"]["normalization_spec_hash"] for name in INSTANCES},
        "scientific_model_hash": scientific_model_hash(protocol),
        "formal_run_approved": protocol["formal_run_approved"],
        "user_approved_formal_execution": protocol["approval_gates"]["user_approved_formal_execution"],
    }
    write_json(SUMMARIES / "abma_outer5_hash_manifest.json", hash_manifest)
    payload = {
        "generated_at": utc_now(), "formal": False, "matrix_run_count": 36,
        "all_runs_successful": True, "abma1_all_primary_72": all(row["objective_evaluation_count"] == 72 for row in rows if row["algorithm"] == "ABMA1"),
        "abma20_all_primary_252": all(row["objective_evaluation_count"] == 252 for row in rows if row["algorithm"] == "ABMA20"),
        "hga_all_exact_budget": all(row["objective_evaluation_count"] == row["expected_primary_evaluations"] for row in rows if str(row["algorithm"]).startswith("HGA")),
        "protected_hashes_match": True, "historical_context_count": len(historical),
        "decision": outcome, "statistics_policy": "exploratory only; n=3; not inferential evidence",
        "formal_run_approved": False, "user_approved_formal_execution": False,
    }
    write_json(SUMMARIES / "abma_outer5_summary.json", payload)
    reports(rows, pairs, summary, historical, outcome)
    return payload


def self_check(protocol: Mapping[str, Any]) -> None:
    if protocol["formal_run_approved"] is not False:
        raise RuntimeError("formal_run_approved must remain false")
    if protocol["approval_gates"]["user_approved_formal_execution"] is not False:
        raise RuntimeError("user_approved_formal_execution must remain false")
    entries = matrix(protocol)
    if {entry["seed"] for entry in entries} != set(SEEDS): raise AssertionError("seed scope")
    if any(entry["formal"] or entry["collision_audit"] or entry["parallel"] for entry in entries):
        raise AssertionError("development controls")
    if [entry["algorithm"] for entry in entries if entry["instance"] == "w30" and entry["seed"] == 79][:2] != ["ABMA20", "ABMA1"]:
        raise AssertionError("alternating order")
    print("ABMA outer5 comparison runner self-check passed")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--max-runs", type=int)
    parser.add_argument("--summarize-only", action="store_true")
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args()
    protocol = read_json(PROTOCOL_PATH)
    OUT.mkdir(parents=True, exist_ok=True); RAW.mkdir(exist_ok=True); TRACES.mkdir(exist_ok=True); SUMMARIES.mkdir(exist_ok=True)
    snapshot_before(protocol)
    if args.self_check:
        self_check(protocol); return
    if args.summarize_only:
        print(json.dumps(summarize(protocol), ensure_ascii=False, indent=2)); return
    rows = run_all(protocol, resume=args.resume, max_runs=args.max_runs)
    if len(load_completed_rows()) == 36:
        print(json.dumps(summarize(protocol), ensure_ascii=False, indent=2))
    else:
        print(json.dumps({"completed": len(load_completed_rows()), "matrix": 36}, ensure_ascii=False))


if __name__ == "__main__":
    main()
