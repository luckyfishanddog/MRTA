"""Sequential calibrated formal runner for EPRK-MA and atomic HGA.

The legacy protocol and legacy 270-run manifest are read-only.  This runner
uses only files under formal_atomic for new state and is resumable per run ID.
"""

from __future__ import annotations

import argparse
import csv
import gc
import hashlib
import json
import math
import os
import platform
import shutil
import statistics
import subprocess
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import collision_aware_schedule
import MRTA_GA_ACO as gaaco
from atomic_problem_core import load_atomic_instance
from atomic_route_evaluator import AtomicRouteEvaluator, normalization_hash
from mrta_problem_core import DirectedWeld
from MRTA_EPRK_MA import EPRKConfig, EPRKMASolver
from MRTA_HGA_ATOMIC_CONTROL import HGAAtomicSolver
from prepare_eprk_atomic_formal import (
    MANIFEST_PATH,
    OUT,
    PROTOCOL_PATH,
    TIME_LIMITS_PATH,
    finalize_protocol,
    generate_manifest,
    sha256_file,
)

try:
    import psutil
except ImportError:  # pragma: no cover
    psutil = None


ROOT = Path(__file__).resolve().parent
BENCHMARK_PATH = OUT / "atomic_benchmark_manifest.json"
CALIBRATION_DIR = OUT / "calibration"
CALIBRATION_SUMMARY_PATH = OUT / "calibration_summary.json"
PREFLIGHT_PATH = OUT / "preflight_summary.json"
RUNS_DIR = OUT / "runs"
QUARANTINE_DIR = OUT / "quarantine"
CHECKPOINT_DIR = OUT / "checkpoints"
EXECUTION_STATE_PATH = OUT / "execution_state.json"
FORMAL_PROFILE_HASH = "de75ad629aeeff608cab96097fe96aed091237348b9f1fe3e6f94c68442771f2"
CALIBRATION_SEEDS = (191, 192, 193)
REPRESENTATIVES = (
    "real_w30", "real_w45", "real_w60",
    "uniform_n60_r1", "clustered_n60_r1", "boundary_dense_n60_r1",
    "long_weld_rich_n60_r1", "zero_travel_chain_n60_r1", "load_skewed_n60_r1",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def write_csv(path: Path, rows: Sequence[Mapping[str, Any]], fields: Sequence[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fields), extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def current_rss_mb() -> float:
    if psutil is None:
        return float("nan")
    return psutil.Process().memory_info().rss / 1024 ** 2


def cpu_affinity() -> list[int] | None:
    if psutil is None or not hasattr(psutil.Process(), "cpu_affinity"):
        return None
    return list(psutil.Process().cpu_affinity())


def environment_evidence() -> dict[str, Any]:
    memory = psutil.virtual_memory().total if psutil is not None else None
    cpu = platform.processor() or os.environ.get("PROCESSOR_IDENTIFIER", "")
    return {
        "python_version": sys.version,
        "python_executable": sys.executable,
        "operating_system": platform.platform(),
        "cpu": cpu,
        "logical_cpu_count": os.cpu_count(),
        "cpu_affinity": cpu_affinity(),
        "total_memory_bytes": memory,
        "source_repository_commit_sha": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "environment_variables": {
            key: os.environ.get(key)
            for key in ("PYTHONHASHSEED", "OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS")
        },
        "sequential": True,
        "parallel": False,
    }


def benchmark() -> dict[str, Any]:
    return load_json(BENCHMARK_PATH)


def benchmark_entries() -> dict[str, dict[str, Any]]:
    return {item["instance_id"]: item for item in benchmark()["instances"]}


def context(instance_id: str):
    item = benchmark_entries()[instance_id]
    instance = load_atomic_instance(ROOT / item["atomic_path"])
    spec = load_json(ROOT / item["normalization_path"])
    if instance.atomic_instance_hash != item["atomic_hash"]:
        raise ValueError("atomic manifest hash mismatch")
    if normalization_hash(spec) != item["normalization_hash"]:
        raise ValueError("normalization manifest hash mismatch")
    return item, instance, spec


def frozen_config() -> EPRKConfig:
    profile_path = OUT / "eprk_ma_final_profile.json"
    if sha256_file(profile_path) != FORMAL_PROFILE_HASH:
        raise RuntimeError("frozen EPRK profile hash mismatch")
    profile = load_json(profile_path)
    return EPRKConfig.from_mapping(profile["configuration"])


def verify_frozen_sources() -> None:
    freeze = load_json(OUT / "EPRK_MA_FREEZE_MANIFEST.json")
    if sha256_file(OUT / "eprk_ma_final_profile.json") != freeze["profile_sha256"]:
        raise RuntimeError("frozen profile changed")
    for name, digest in freeze["source_hashes"].items():
        if sha256_file(ROOT / name) != digest:
            raise RuntimeError(f"frozen EPRK source changed: {name}")
    model = load_json(OUT / "ATOMIC_MODEL_FREEZE_MANIFEST.json")
    for name, digest in model["frozen_file_hashes"].items():
        if sha256_file(ROOT / name) != digest:
            raise RuntimeError(f"frozen atomic source changed: {name}")


def cross_validate(instance_id: str, metrics: Mapping[str, Any]) -> tuple[bool, list[str]]:
    item, instance, spec = context(instance_id)
    errors: list[str] = []
    try:
        if metrics.get("atomic_instance_hash") != item["atomic_hash"]:
            errors.append("atomic instance hash mismatch")
        if metrics.get("normalization_hash") != item["normalization_hash"]:
            errors.append("normalization hash mismatch")
        routes = metrics.get("routes")
        flags = metrics.get("direction_flags")
        if not isinstance(routes, list) or len(routes) != 4:
            errors.append("result does not have four routes")
            return False, errors
        if not isinstance(flags, list) or len(flags) != 4 or any(len(r) != len(f) for r, f in zip(routes, flags)):
            errors.append("route/direction lengths differ")
            return False, errors
        flat = [wid for route in routes for wid in route]
        if len(flat) != len(set(flat)):
            errors.append("duplicate atomic weld")
        if set(flat) != set(instance.by_id):
            errors.append("missing or unknown atomic weld")
        upper = int(metrics["upper_event_index"])
        lower = int(metrics["lower_event_index"])
        expected = (
            instance.upper_events[upper].left_atomic_ids,
            instance.upper_events[upper].right_atomic_ids,
            instance.lower_events[lower].left_atomic_ids,
            instance.lower_events[lower].right_atomic_ids,
        )
        if any(set(routes[i]) != set(expected[i]) for i in range(4)):
            errors.append("route crosses selected event assignment")
        result = AtomicRouteEvaluator(instance, spec).evaluate_system(routes)
        for key, actual in (
            ("fitness", result.fitness),
            ("makespan", result.makespan),
            ("load_imbalance", result.load_imbalance),
            ("idle_distance", result.idle_distance),
        ):
            if not math.isclose(float(metrics[key]), float(actual), rel_tol=1e-10, abs_tol=1e-8):
                errors.append(f"{key} recomputation mismatch")
        if [list(value) for value in result.direction_flags] != flags:
            errors.append("direction DP recomputation mismatch")
    except Exception as exc:  # pragma: no cover - retained as evidence
        errors.append(f"cross-validation exception: {type(exc).__name__}: {exc}")
    return not errors, errors


def run_solver(solver: str, instance_id: str, seed: int, *, primary_budget: int | None,
               time_limit_s: float | None) -> dict[str, Any]:
    verify_frozen_sources()
    _, instance, spec = context(instance_id)
    before_rss = current_rss_mb()
    outer_start = time.perf_counter()
    if solver == "eprk":
        engine = EPRKMASolver(
            instance, spec, seed, frozen_config(),
            primary_budget=primary_budget, time_limit_s=time_limit_s,
        )
    elif solver == "hga_atomic":
        engine = HGAAtomicSolver(
            instance, spec, seed,
            primary_budget=primary_budget, time_limit_s=time_limit_s,
        )
    else:
        raise ValueError(solver)
    best = engine.run()
    wall = time.perf_counter() - outer_start
    metrics = engine.metrics(best)
    metrics["solver_id"] = solver
    metrics["instance_id"] = instance_id
    metrics["solver_wall_clock_time_s"] = wall
    metrics["rss_mb"] = max(before_rss, current_rss_mb())
    metrics["profile_hash"] = FORMAL_PROFILE_HASH if solver == "eprk" else sha256_file(ROOT / "MRTA_HGA_ATOMIC_CONTROL.py")
    valid, errors = cross_validate(instance_id, metrics)
    metrics["cross_validation_passed"] = valid
    metrics["cross_validation_errors"] = errors
    return metrics


def _calibration_target(budget: int, instance: str, solver: str, seed: int) -> Path:
    return CALIBRATION_DIR / f"budget_{budget}" / instance / solver / f"seed_{seed}.json"


def calibration_one(budget: int, instance: str, solver: str, seed: int) -> dict[str, Any]:
    target = _calibration_target(budget, instance, solver, seed)
    if target.is_file():
        saved = load_json(target)
        if (saved.get("status") == "success" and saved.get("requested_primary_budget") == budget
                and saved.get("cross_validation_passed") and saved.get("profile_hash")
                == (FORMAL_PROFILE_HASH if solver == "eprk" else sha256_file(ROOT / "MRTA_HGA_ATOMIC_CONTROL.py"))):
            saved["resumed_existing_result"] = True
            return saved
    started = utc_now()
    metrics = run_solver(solver, instance, seed, primary_budget=budget, time_limit_s=300.0)
    metrics.update({
        "phase": "formal_resource_calibration",
        "status": "success",
        "started_at": started,
        "finished_at": utc_now(),
        "calibration_budget": budget,
        "calibration_time_cap_s": 300.0,
    })
    atomic_write_json(target, metrics)
    gc.collect()
    return metrics


def _reference_for(instance_id: str, family: str) -> str:
    if family == "real":
        return instance_id
    return f"{family}_n60_r1"


def _ceil_to_10(value: float) -> int:
    return int(math.ceil(value / 10.0) * 10)


def calibrate() -> dict[str, Any]:
    verify_frozen_sources()
    all_rows: list[dict[str, Any]] = []
    selected_budget = 20000
    for seed in CALIBRATION_SEEDS:
        for instance in REPRESENTATIVES:
            order = ("eprk", "hga_atomic") if int(hashlib.sha256(f"{instance}|{seed}|calibration".encode()).hexdigest(), 16) % 2 == 0 else ("hga_atomic", "eprk")
            for solver in order:
                all_rows.append(calibration_one(20000, instance, solver, seed))
    if any(int(row["primary_evaluations"]) != 20000 or row["stop_reason"] != "objective_budget" for row in all_rows):
        selected_budget = 10000
        all_rows = []
        for seed in CALIBRATION_SEEDS:
            for instance in REPRESENTATIVES:
                order = ("eprk", "hga_atomic") if int(hashlib.sha256(f"{instance}|{seed}|calibration".encode()).hexdigest(), 16) % 2 == 0 else ("hga_atomic", "eprk")
                for solver in order:
                    all_rows.append(calibration_one(10000, instance, solver, seed))
    if any(int(row["primary_evaluations"]) != selected_budget or row["stop_reason"] != "objective_budget"
           or not row["cross_validation_passed"] for row in all_rows):
        raise RuntimeError("selected calibration budget did not complete cleanly")
    by_key = {(row["instance_id"], int(row["seed"]), row["solver_id"]): row for row in all_rows}
    reference_limits: dict[str, dict[str, Any]] = {}
    for instance in REPRESENTATIVES:
        paired_max = [max(
            float(by_key[(instance, seed, "eprk")]["solver_wall_clock_time_s"]),
            float(by_key[(instance, seed, "hga_atomic")]["solver_wall_clock_time_s"]),
        ) for seed in CALIBRATION_SEEDS]
        raw = 20.0 * statistics.median(paired_max)
        limit = _ceil_to_10(min(300.0, max(30.0, raw)))
        reference_limits[instance] = {
            "paired_max_wall_seconds": paired_max,
            "median_paired_max_wall_seconds": statistics.median(paired_max),
            "raw_time_limit_seconds": raw,
            "time_limit_seconds": limit,
        }
    limit_rows = []
    for item in benchmark()["instances"]:
        reference = _reference_for(item["instance_id"], item["family"])
        limit_rows.append({
            "instance_id": item["instance_id"],
            "family": item["family"],
            "nominal_weld_count": item["nominal_weld_count"],
            "replicate": item["replicate"],
            "calibration_reference_instance": reference,
            "calibration_primary_budget": selected_budget,
            "median_max_solver_wall_seconds": reference_limits[reference]["median_paired_max_wall_seconds"],
            "raw_time_limit_seconds": reference_limits[reference]["raw_time_limit_seconds"],
            "time_limit_seconds": reference_limits[reference]["time_limit_seconds"],
        })
    write_csv(TIME_LIMITS_PATH, limit_rows, list(limit_rows[0]))
    summary = {
        "completed": True,
        "initial_primary_budget": 20000,
        "selected_equal_primary_budget": selected_budget,
        "fallback_triggered": selected_budget == 10000,
        "representative_instances": list(REPRESENTATIVES),
        "calibration_seeds": list(CALIBRATION_SEEDS),
        "run_count": len(all_rows),
        "all_cross_validation_passed": all(row["cross_validation_passed"] for row in all_rows),
        "reference_time_limits": reference_limits,
        "time_limit_mapping_policy": "real instances self-reference; synthetic instances use family n60 replicate1",
        "time_limits_path": "formal_atomic/time_limits_by_instance.csv",
        "environment": environment_evidence(),
        "rows": all_rows,
    }
    atomic_write_json(CALIBRATION_SUMMARY_PATH, summary)
    lines = [
        "# EPRK/HGA 原子正式实验资源校准报告",
        "",
        f"- 选定等 Primary 预算：`{selected_budget}`",
        f"- 是否触发 10000 fallback：`{selected_budget == 10000}`",
        f"- 校准实际运行数：`{len(all_rows)}`",
        "- 校准仅决定资源，不修改算法参数。",
        "- 真实实例使用自身校准；其他合成实例使用同 family 的 n60/replicate1 校准时间。",
        "",
        "| reference | median max wall (s) | raw T (s) | frozen T (s) |",
        "|---|---:|---:|---:|",
    ]
    for instance, value in reference_limits.items():
        lines.append(f"| {instance} | {value['median_paired_max_wall_seconds']:.6f} | {value['raw_time_limit_seconds']:.6f} | {value['time_limit_seconds']} |")
    (OUT / "time_limit_calibration_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({
        "selected_equal_primary_budget": selected_budget,
        "calibration_run_count": len(all_rows),
        "reference_time_limits": {key: value["time_limit_seconds"] for key, value in reference_limits.items()},
    }, ensure_ascii=False, indent=2))
    return summary


def preflight() -> dict[str, Any]:
    commands = [
        [sys.executable, "-m", "unittest", "discover", "-v"],
        [sys.executable, "run_unified_experiments.py", "--validate-protocol"],
        [sys.executable, "run_unified_experiments.py", "--self-check"],
    ]
    results = []
    log_dir = OUT / "preflight_logs"; log_dir.mkdir(parents=True, exist_ok=True)
    for index, command in enumerate(commands, 1):
        completed = subprocess.run(command, cwd=ROOT, text=True, capture_output=True)
        (log_dir / f"command_{index:02d}_stdout.log").write_text(completed.stdout, encoding="utf-8")
        (log_dir / f"command_{index:02d}_stderr.log").write_text(completed.stderr, encoding="utf-8")
        results.append({
            "command": command,
            "returncode": completed.returncode,
            "stdout_log": f"formal_atomic/preflight_logs/command_{index:02d}_stdout.log",
            "stderr_log": f"formal_atomic/preflight_logs/command_{index:02d}_stderr.log",
        })
    legacy = load_json(ROOT / "UNIFIED_EXPERIMENT_PROTOCOL.json")
    passed = all(item["returncode"] == 0 for item in results)
    passed = passed and not legacy["formal_run_approved"] and not legacy["approval_gates"]["user_approved_formal_execution"]
    verify_frozen_sources()
    summary = {
        "passed": passed,
        "completed_at": utc_now(),
        "commands": results,
        "legacy_formal_run_approved_remains_false": not legacy["formal_run_approved"],
        "legacy_user_approved_remains_false": not legacy["approval_gates"]["user_approved_formal_execution"],
        "environment": environment_evidence(),
    }
    atomic_write_json(PREFLIGHT_PATH, summary)
    if not passed:
        raise RuntimeError("formal preflight failed")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


def finalize() -> dict[str, Any]:
    calibration = load_json(CALIBRATION_SUMMARY_PATH)
    preflight_summary = load_json(PREFLIGHT_PATH)
    protocol = finalize_protocol(calibration, preflight_summary)
    manifest = generate_manifest(protocol)
    print(json.dumps({
        "protocol_hash": sha256_file(PROTOCOL_PATH),
        "manifest_hash": sha256_file(MANIFEST_PATH),
        "manifest_run_count": manifest["run_count"],
        "formal_run_approved": protocol["approval_gates"]["formal_run_approved"],
    }, ensure_ascii=False, indent=2))
    return manifest


def collision_audit(instance_id: str, raw_result_path: Path, metrics: Mapping[str, Any]) -> dict[str, Any]:
    before = sha256_file(raw_result_path)
    _, instance, _ = context(instance_id)
    sequences = []
    for route, flags in zip(metrics["routes"], metrics["direction_flags"]):
        sequences.append([DirectedWeld(instance.by_id[wid], bool(flag)) for wid, flag in zip(route, flags)])
    gaaco.TIME_MODEL = "corrected"
    gaaco.SPLIT_TIMING_MODE = "parent_aware"
    gaaco.CORRECTED_WELD_SPEED = 0.0108
    gaaco.CORRECTED_TRAVEL_SPEED = 0.2
    gaaco.CORRECTED_ACC = 0.5
    gaaco.CORRECTED_SAFE_Z = 0.3
    started = time.perf_counter()
    audited = collision_aware_schedule.audit_boundary_collisions_for_four_robots(
        sequences,
        float(metrics["upper_boundary_x"]),
        float(metrics["lower_boundary_x"]),
        safety_distance=0.5,
        band_width=0.5,
        dt=1.0,
    )
    audit_time = time.perf_counter() - started
    after = sha256_file(raw_result_path)
    if before != after:
        raise RuntimeError("collision audit changed raw result")
    if float(audited["collision_adjusted_makespan"]) + 1e-8 < float(metrics["makespan"]):
        raise RuntimeError("collision-adjusted makespan below raw makespan")
    audited.update({
        "collision_audit_status": "success" if int(audited["unresolved_conflict_count"]) == 0 else "completed_with_unresolved",
        "collision_audit_time_s": audit_time,
        "raw_makespan": float(metrics["makespan"]),
        "raw_fitness": float(metrics["fitness"]),
        "raw_result_sha256_before": before,
        "raw_result_sha256_after": after,
        "raw_result_unchanged": True,
        "post_processing_only": True,
        "routes_and_directions_unchanged": True,
    })
    return audited


def _matching_success(base: Path, resume_key: str) -> Path | None:
    for attempt in sorted(base.glob("attempt_*")):
        result_path = attempt / "result.json"
        collision_path = attempt / "collision_postprocess.json"
        if result_path.is_file():
            value = load_json(result_path)
            if value.get("status") == "success" and value.get("resume_key") == resume_key and collision_path.is_file():
                return attempt
    return None


def _quarantine_mismatches(base: Path, resume_key: str, run_id: str) -> None:
    for attempt in sorted(base.glob("attempt_*")):
        result_path = attempt / "result.json"
        if not result_path.is_file():
            continue
        value = load_json(result_path)
        if value.get("resume_key") != resume_key:
            target = QUARANTINE_DIR / run_id / f"{int(time.time() * 1000)}_{attempt.name}"
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(attempt), str(target))


def execute_entry(entry: Mapping[str, Any]) -> dict[str, Any]:
    protocol = load_json(PROTOCOL_PATH)
    if not protocol["approval_gates"]["formal_run_approved"]:
        raise RuntimeError("new atomic formal protocol is fail-closed")
    if sha256_file(PROTOCOL_PATH) != entry["protocol_hash"]:
        raise RuntimeError("manifest protocol hash mismatch")
    base = ROOT / entry["expected_output_path"]
    base.mkdir(parents=True, exist_ok=True)
    existing = _matching_success(base, entry["resume_key"])
    if existing:
        return {"run_id": entry["run_id"], "status": "success", "resumed": True, "attempt_path": existing.relative_to(ROOT).as_posix()}
    _quarantine_mismatches(base, entry["resume_key"], entry["run_id"])
    existing_attempts = sorted(base.glob("attempt_*"))
    if len(existing_attempts) >= 2:
        return {"run_id": entry["run_id"], "status": "failed", "resumed": True, "reason": "retry limit already exhausted"}
    last: dict[str, Any] = {}
    for attempt_index in range(len(existing_attempts) + 1, 3):
        attempt = base / f"attempt_{attempt_index:03d}"
        attempt.mkdir(parents=True, exist_ok=False)
        started = utc_now()
        result_path = attempt / "result.json"
        try:
            metrics = run_solver(
                str(entry["solver"]), str(entry["instance_id"]), int(entry["solver_seed"]),
                primary_budget=int(entry["requested_primary"]) if entry.get("requested_primary") is not None else None,
                time_limit_s=float(entry["time_limit_seconds"]) if entry.get("time_limit_seconds") is not None else None,
            )
            primary_ok = entry["mode"] != "equal_primary" or (
                int(metrics["primary_evaluations"]) == int(entry["requested_primary"])
                and metrics["stop_reason"] == "objective_budget"
            )
            if entry["mode"] == "equal_time":
                limit = float(entry["time_limit_seconds"])
                time_ok = metrics["stop_reason"] == "time_limit" and 0.95 * limit <= float(metrics["solver_wall_clock_time_s"]) <= limit + max(2.0, 0.05 * limit)
            else:
                time_ok = True
            success = bool(metrics["cross_validation_passed"] and primary_ok and time_ok)
            payload = {
                "status": "success" if success else "failed",
                "run_id": entry["run_id"],
                "resume_key": entry["resume_key"],
                "command_hash": entry["command_hash"],
                "protocol_hash": entry["protocol_hash"],
                "collision_policy_hash": entry["collision_policy_hash"],
                "mode": entry["mode"],
                "family": entry["family"],
                "instance_id": entry["instance_id"],
                "solver": entry["solver"],
                "solver_display_name": entry["solver_display_name"],
                "solver_version": entry["solver_version"],
                "solver_seed": entry["solver_seed"],
                "profile_hash": entry["profile_hash"],
                "started_at": started,
                "finished_at": utc_now(),
                "attempt": attempt_index,
                "complete_candidate_boundary_stop": True,
                "primary_budget_check_passed": primary_ok,
                "time_limit_boundary_check_passed": time_ok,
                "environment": environment_evidence(),
                "metrics": metrics,
            }
            atomic_write_json(result_path, payload)
            if not success:
                payload["failure_reason"] = "run success criteria did not pass"
                atomic_write_json(result_path, payload)
                last = {"run_id": entry["run_id"], "status": "failed", "attempt_path": attempt.relative_to(ROOT).as_posix()}
                continue
            audited = collision_audit(entry["instance_id"], result_path, metrics)
            audited.update({
                "run_id": entry["run_id"],
                "mode": entry["mode"],
                "instance_id": entry["instance_id"],
                "family": entry["family"],
                "solver": entry["solver"],
                "solver_seed": entry["solver_seed"],
                "protocol_hash": entry["protocol_hash"],
                "collision_policy_hash": entry["collision_policy_hash"],
            })
            atomic_write_json(attempt / "collision_postprocess.json", audited)
            return {"run_id": entry["run_id"], "status": "success", "resumed": False, "attempt_path": attempt.relative_to(ROOT).as_posix()}
        except Exception as exc:  # preserve both failed attempts
            failure = {
                "status": "failed",
                "run_id": entry["run_id"],
                "resume_key": entry["resume_key"],
                "attempt": attempt_index,
                "started_at": started,
                "finished_at": utc_now(),
                "exception_type": type(exc).__name__,
                "exception_message": str(exc),
                "traceback": traceback.format_exc(),
            }
            atomic_write_json(result_path, failure)
            last = {"run_id": entry["run_id"], "status": "failed", "attempt_path": attempt.relative_to(ROOT).as_posix(), "reason": str(exc)}
        finally:
            gc.collect()
    return last


RAW_FIELDS = [
    "run_id", "mode", "family", "instance_id", "nominal_weld_count", "replicate",
    "solver", "solver_display_name", "solver_version", "solver_seed", "profile_hash",
    "status", "attempt", "fitness", "makespan", "load_imbalance", "idle_distance",
    "primary_evaluations", "requested_primary_budget", "stop_reason", "algorithm_time_s",
    "solver_wall_clock_time_s", "rss_mb", "route_evaluation_requests", "direction_dp_calls",
    "route_evaluations_per_primary", "direction_dp_per_primary", "phenotype_cache_hits",
    "atomic_instance_hash", "normalization_hash", "cross_validation_passed", "protocol_hash",
    "resume_key", "result_path",
]
COLLISION_FIELDS = [
    "run_id", "mode", "family", "instance_id", "solver", "solver_seed",
    "raw_makespan", "collision_adjusted_makespan", "added_waiting_time", "conflict_count",
    "unresolved_conflict_count", "collision_audit_time_s", "collision_audit_status",
    "raw_result_unchanged", "collision_path",
]
FAILURE_FIELDS = [
    "run_id", "attempt", "status", "exception_type", "exception_message", "result_path",
]


def rebuild_tables() -> dict[str, int]:
    manifest = load_json(MANIFEST_PATH)
    raw_rows: list[dict[str, Any]] = []
    collision_rows: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    success_ids: set[str] = set()
    for entry in manifest["entries"]:
        base = ROOT / entry["expected_output_path"]
        for attempt in sorted(base.glob("attempt_*")):
            result_path = attempt / "result.json"
            if not result_path.is_file():
                continue
            value = load_json(result_path)
            if value.get("status") != "success":
                failures.append({**value, "result_path": result_path.relative_to(ROOT).as_posix()})
                continue
            metrics = value["metrics"]
            row = {key: value.get(key, entry.get(key)) for key in RAW_FIELDS}
            for key in RAW_FIELDS:
                if key in metrics:
                    row[key] = metrics[key]
            row["result_path"] = result_path.relative_to(ROOT).as_posix()
            raw_rows.append(row); success_ids.add(entry["run_id"])
            collision_path = attempt / "collision_postprocess.json"
            if collision_path.is_file():
                collision = load_json(collision_path)
                collision["collision_path"] = collision_path.relative_to(ROOT).as_posix()
                collision_rows.append(collision)
    write_csv(OUT / "raw_runs.csv", raw_rows, RAW_FIELDS)
    write_csv(OUT / "collision_runs.csv", collision_rows, COLLISION_FIELDS)
    write_csv(OUT / "run_failures.csv", failures, FAILURE_FIELDS)
    return {"success": len(success_ids), "collision": len(collision_rows), "failed_attempts": len(failures)}


def run_manifest(max_runs: int | None = None) -> dict[str, Any]:
    manifest = load_json(MANIFEST_PATH)
    state = load_json(EXECUTION_STATE_PATH) if EXECUTION_STATE_PATH.is_file() else {
        "started_at": utc_now(), "processed_run_ids": [], "success_run_ids": [], "failed_run_ids": []
    }
    processed = set(state["processed_run_ids"])
    count = 0
    initial_affinity = cpu_affinity()
    for entry in manifest["entries"]:
        if entry["run_id"] in processed:
            continue
        if max_runs is not None and count >= max_runs:
            break
        if cpu_affinity() != initial_affinity:
            raise RuntimeError("CPU affinity changed during sequential formal execution")
        outcome = execute_entry(entry)
        processed.add(entry["run_id"]); state["processed_run_ids"] = sorted(processed)
        target_key = "success_run_ids" if outcome["status"] == "success" else "failed_run_ids"
        if entry["run_id"] not in state[target_key]:
            state[target_key].append(entry["run_id"])
        state["last_run_id"] = entry["run_id"]
        state["updated_at"] = utc_now()
        atomic_write_json(EXECUTION_STATE_PATH, state)
        count += 1
        if len(processed) % 100 == 0:
            counts = rebuild_tables()
            atomic_write_json(CHECKPOINT_DIR / f"checkpoint_{len(processed):05d}.json", {
                "completed_manifest_entries": len(processed), "counts": counts,
                "last_run_id": entry["run_id"], "created_at": utc_now(),
            })
    counts = rebuild_tables()
    state["counts"] = counts
    state["manifest_run_count"] = manifest["run_count"]
    state["complete"] = len(processed) == manifest["run_count"]
    state["updated_at"] = utc_now()
    atomic_write_json(EXECUTION_STATE_PATH, state)
    print(json.dumps({"processed_this_call": count, "processed_total": len(processed), **counts, "complete": state["complete"]}, ensure_ascii=False, indent=2))
    return state


def entry_by_run_id(run_id: str) -> dict[str, Any]:
    for entry in load_json(MANIFEST_PATH)["entries"]:
        if entry["run_id"] == run_id:
            return entry
    raise KeyError(run_id)


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("preflight")
    sub.add_parser("calibrate")
    sub.add_parser("finalize")
    execute_parser = sub.add_parser("execute")
    execute_parser.add_argument("--run-id", required=True)
    run_parser = sub.add_parser("run-manifest")
    run_parser.add_argument("--max-runs", type=int)
    sub.add_parser("rebuild")
    args = parser.parse_args()
    if args.command == "preflight":
        preflight()
    elif args.command == "calibrate":
        calibrate()
    elif args.command == "finalize":
        finalize()
    elif args.command == "execute":
        print(json.dumps(execute_entry(entry_by_run_id(args.run_id)), ensure_ascii=False, indent=2))
    elif args.command == "run-manifest":
        run_manifest(args.max_runs)
    else:
        print(json.dumps(rebuild_tables(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
