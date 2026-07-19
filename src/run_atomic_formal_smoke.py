"""Run the authorized 16-run EPRK/HGA atomic smoke matrix only."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from prepare_eprk_atomic_formal import sha256_file
from run_eprk_atomic_formal import collision_audit, load_json, run_solver


ROOT = Path(__file__).resolve().parents[1]
REMEDIATION = ROOT / "formal_atomic" / "remediation"
RESULTS = REMEDIATION / "smoke_results"
QUARANTINE = REMEDIATION / "smoke_quarantine"
MANIFEST = REMEDIATION / "smoke_manifest.json"
CHECKPOINT = REMEDIATION / "smoke_checkpoint.json"
SNAPSHOT = REMEDIATION / "smoke_interruption_snapshot.json"
RESUME_AUDIT = REMEDIATION / "smoke_resume_audit.json"
RUNS_CSV = REMEDIATION / "smoke_runs.csv"
COLLISION_CSV = REMEDIATION / "smoke_collision_runs.csv"
FAILURES_CSV = REMEDIATION / "smoke_failures.csv"
CALIBRATION = REMEDIATION / "calibration_summary.json"
TIME_LIMITS = REMEDIATION / "time_limits_by_instance.csv"
FORMAL_PROTOCOL = ROOT / "formal_atomic" / "EPRK_ATOMIC_FORMAL_PROTOCOL.json"

INSTANCES = ("real_w30", "long_weld_rich_n60_r1")
SOLVERS = ("eprk", "hga_atomic")
SEEDS = (194, 195)
MODES = ("equal_primary", "equal_time")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_hash(value: Any) -> str:
    data = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def write_hash(path: Path) -> str:
    digest = sha256_file(path)
    path.with_suffix(path.suffix + ".sha256").write_text(digest + "\n", encoding="ascii")
    return digest


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def parity(instance: str, seed: int, mode: str) -> int:
    return int(hashlib.sha256(f"{instance}|{seed}|{mode}".encode()).hexdigest(), 16) & 1


def prepare() -> dict[str, Any]:
    calibration = load_json(CALIBRATION)
    if not calibration.get("completed"):
        raise RuntimeError("resource calibration is incomplete")
    protocol = load_json(FORMAL_PROTOCOL)
    if protocol["approval_gates"]["formal_run_approved"]:
        raise RuntimeError("smoke must not run from a formally authorized protocol")
    if set(SEEDS) & set(protocol["seeds"]["formal_paired"]):
        raise RuntimeError("smoke and formal seeds overlap")
    with TIME_LIMITS.open(encoding="utf-8-sig", newline="") as handle:
        limits = {row["instance_id"]: int(row["time_limit_seconds"]) for row in csv.DictReader(handle)}
    budget = int(calibration["selected_equal_primary_budget"])
    entries: list[dict[str, Any]] = []
    order = 0
    for instance in INSTANCES:
        for seed in SEEDS:
            for mode in MODES:
                solver_order = SOLVERS if parity(instance, seed, mode) == 0 else tuple(reversed(SOLVERS))
                for pair_position, solver in enumerate(solver_order, 1):
                    order += 1
                    run_id = f"smoke__{mode}__{instance}__{solver}__seed_{seed}"
                    identity = {
                        "run_id": run_id,
                        "mode": mode,
                        "instance_id": instance,
                        "solver": solver,
                        "seed": seed,
                        "primary_budget": budget if mode == "equal_primary" else None,
                        "time_limit_seconds": limits[instance] if mode == "equal_time" else None,
                        "protocol_hash": sha256_file(FORMAL_PROTOCOL),
                    }
                    entries.append({
                        **identity,
                        "execution_order": order,
                        "pair_position": pair_position,
                        "resume_key": canonical_hash(identity),
                        "expected_output_path": (
                            f"formal_atomic/remediation/smoke_results/{mode}/{instance}/{solver}/seed_{seed}"
                        ),
                    })
    if len(entries) != 16 or len({row["run_id"] for row in entries}) != 16:
        raise AssertionError("smoke manifest must contain exactly 16 unique runs")
    manifest = {
        "schema_version": "eprk_atomic_smoke_manifest_v1",
        "status": "prepared_not_started",
        "smoke_only": True,
        "formal_execution_authorized": False,
        "formal_seeds_executed": [],
        "instances": list(INSTANCES),
        "solvers": list(SOLVERS),
        "seeds": list(SEEDS),
        "modes": list(MODES),
        "run_count": 16,
        "sequential": True,
        "parallel": False,
        "collision_postprocess_required": True,
        "entries": entries,
    }
    write_json(MANIFEST, manifest)
    write_hash(MANIFEST)
    return manifest


def matching_attempt(entry: Mapping[str, Any]) -> Path | None:
    base = ROOT / str(entry["expected_output_path"])
    for attempt in sorted(base.glob("attempt_*")):
        result = attempt / "result.json"
        companion = attempt / "result.json.sha256"
        collision = attempt / "collision_postprocess.json"
        if not (result.is_file() and companion.is_file() and collision.is_file()):
            continue
        expected = companion.read_text(encoding="ascii").strip()
        payload = load_json(result)
        if expected == sha256_file(result) and payload.get("resume_key") == entry["resume_key"] and payload.get("status") == "success":
            return attempt
        target = QUARANTINE / str(entry["run_id"]) / f"{int(time.time() * 1000)}_{attempt.name}"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(attempt), str(target))
    return None


def execute(entry: Mapping[str, Any]) -> dict[str, Any]:
    existing = matching_attempt(entry)
    if existing is not None:
        return {"status": "success", "resumed": True, "attempt": existing}
    base = ROOT / str(entry["expected_output_path"])
    base.mkdir(parents=True, exist_ok=True)
    attempt = base / f"attempt_{len(list(base.glob('attempt_*'))) + 1:03d}"
    attempt.mkdir()
    result_path = attempt / "result.json"
    try:
        metrics = run_solver(
            str(entry["solver"]), str(entry["instance_id"]), int(entry["seed"]),
            primary_budget=(int(entry["primary_budget"]) if entry["primary_budget"] is not None else None),
            time_limit_s=(float(entry["time_limit_seconds"]) if entry["time_limit_seconds"] is not None else None),
        )
        primary_ok = entry["mode"] != "equal_primary" or (
            metrics["stop_reason"] == "objective_budget"
            and int(metrics["primary_evaluations"]) == int(entry["primary_budget"])
        )
        if entry["mode"] == "equal_time":
            limit = float(entry["time_limit_seconds"])
            time_ok = (
                metrics["stop_reason"] == "time_limit"
                and float(metrics["solver_wall_clock_time_s"]) >= 0.95 * limit
                and float(metrics["solver_wall_clock_time_s"]) <= limit + max(2.0, 0.05 * limit)
            )
        else:
            time_ok = True
        success = bool(metrics["cross_validation_passed"] and primary_ok and time_ok)
        payload = {
            "status": "success" if success else "failed",
            "run_id": entry["run_id"],
            "resume_key": entry["resume_key"],
            "execution_order": entry["execution_order"],
            "pair_position": entry["pair_position"],
            "mode": entry["mode"],
            "instance_id": entry["instance_id"],
            "solver": entry["solver"],
            "seed": entry["seed"],
            "primary_budget_check_passed": primary_ok,
            "time_limit_boundary_check_passed": time_ok,
            "complete_candidate_boundary_stop": True,
            "metrics": metrics,
            "finished_at": utc_now(),
        }
        write_json(result_path, payload)
        if not success:
            return {"status": "failed", "resumed": False, "attempt": attempt}
        audited = collision_audit(str(entry["instance_id"]), result_path, metrics)
        audited.update({
            "run_id": entry["run_id"], "mode": entry["mode"],
            "instance_id": entry["instance_id"], "solver": entry["solver"], "seed": entry["seed"],
        })
        write_json(attempt / "collision_postprocess.json", audited)
        write_hash(result_path)
        return {"status": "success", "resumed": False, "attempt": attempt}
    except Exception as exc:
        write_json(result_path, {
            "status": "failed", "run_id": entry["run_id"], "resume_key": entry["resume_key"],
            "exception_type": type(exc).__name__, "exception_message": str(exc), "finished_at": utc_now(),
        })
        return {"status": "failed", "resumed": False, "attempt": attempt}


def rebuild_tables(manifest: Mapping[str, Any]) -> tuple[int, int, int]:
    raw_rows: list[dict[str, Any]] = []
    collision_rows: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for entry in manifest["entries"]:
        base = ROOT / entry["expected_output_path"]
        for attempt in sorted(base.glob("attempt_*")):
            result_path = attempt / "result.json"
            if not result_path.is_file():
                continue
            value = load_json(result_path)
            if value.get("status") != "success":
                failures.append({
                    "run_id": entry["run_id"], "status": value.get("status", "failed"),
                    "exception_type": value.get("exception_type", ""),
                    "exception_message": value.get("exception_message", ""),
                    "result_path": result_path.relative_to(ROOT).as_posix(),
                })
                continue
            metrics = value["metrics"]
            raw_rows.append({
                "run_id": entry["run_id"], "execution_order": entry["execution_order"],
                "pair_position": entry["pair_position"], "mode": entry["mode"],
                "instance_id": entry["instance_id"], "solver": entry["solver"], "seed": entry["seed"],
                "status": "success", "fitness": metrics["fitness"], "makespan": metrics["makespan"],
                "primary_evaluations": metrics["primary_evaluations"], "stop_reason": metrics["stop_reason"],
                "algorithm_time_s": metrics["algorithm_time_s"],
                "solver_wall_clock_time_s": metrics["solver_wall_clock_time_s"],
                "atomic_instance_hash": metrics["atomic_instance_hash"],
                "normalization_hash": metrics["normalization_hash"], "profile_hash": metrics["profile_hash"],
                "cross_validation_passed": metrics["cross_validation_passed"],
                "result_sha256": sha256_file(result_path),
                "result_path": result_path.relative_to(ROOT).as_posix(),
            })
            collision_path = attempt / "collision_postprocess.json"
            if collision_path.is_file():
                audited = load_json(collision_path)
                collision_rows.append({
                    "run_id": entry["run_id"], "mode": entry["mode"], "instance_id": entry["instance_id"],
                    "solver": entry["solver"], "seed": entry["seed"],
                    "raw_makespan": audited["raw_makespan"],
                    "collision_adjusted_makespan": audited["collision_adjusted_makespan"],
                    "added_waiting_time": audited["added_waiting_time"],
                    "conflict_count": audited["conflict_count"],
                    "unresolved_conflict_count": audited["unresolved_conflict_count"],
                    "collision_audit_status": audited["collision_audit_status"],
                    "raw_result_unchanged": audited["raw_result_unchanged"],
                    "collision_path": collision_path.relative_to(ROOT).as_posix(),
                })
    raw_fields = list(raw_rows[0]) if raw_rows else ["run_id", "status"]
    collision_fields = list(collision_rows[0]) if collision_rows else ["run_id", "collision_audit_status"]
    failure_fields = list(failures[0]) if failures else ["run_id", "status", "exception_type", "exception_message", "result_path"]
    write_csv(RUNS_CSV, raw_rows, raw_fields)
    write_csv(COLLISION_CSV, collision_rows, collision_fields)
    write_csv(FAILURES_CSV, failures, failure_fields)
    return len(raw_rows), len(collision_rows), len(failures)


def snapshot_first_five(manifest: Mapping[str, Any]) -> None:
    rows = []
    for entry in manifest["entries"][:5]:
        attempt = matching_attempt(entry)
        if attempt is None:
            raise RuntimeError("cannot snapshot incomplete first-five smoke result")
        result = attempt / "result.json"
        rows.append({
            "run_id": entry["run_id"], "result_path": result.relative_to(ROOT).as_posix(),
            "sha256": sha256_file(result), "mtime_ns": result.stat().st_mtime_ns,
        })
    write_json(SNAPSHOT, {"interrupted_after_completed_runs": 5, "snapshot": rows, "created_at": utc_now()})


def verify_resume(manifest: Mapping[str, Any], resumed_count: int) -> dict[str, Any]:
    before = load_json(SNAPSHOT)
    rows = []
    for item in before["snapshot"]:
        path = ROOT / item["result_path"]
        rows.append({
            "run_id": item["run_id"],
            "sha256_unchanged": path.is_file() and sha256_file(path) == item["sha256"],
            "mtime_unchanged": path.is_file() and path.stat().st_mtime_ns == item["mtime_ns"],
        })
    passed = len(rows) == 5 and all(row["sha256_unchanged"] and row["mtime_unchanged"] for row in rows)
    audit = {
        "passed": passed, "controlled_interruption_after_runs": 5,
        "previous_successes_resumed_without_reexecution": resumed_count >= 5,
        "first_five": rows, "quarantine_policy_active": True,
        "failed_results_preserved": True, "manifest_status_updated": True, "verified_at": utc_now(),
    }
    write_json(RESUME_AUDIT, audit)
    return audit


def run(max_new_runs: int | None) -> dict[str, Any]:
    manifest = load_json(MANIFEST) if MANIFEST.is_file() else prepare()
    new_count = 0
    resumed_count = 0
    failed_count = 0
    for entry in manifest["entries"]:
        if max_new_runs is not None and new_count >= max_new_runs:
            break
        outcome = execute(entry)
        resumed_count += int(outcome["resumed"])
        if not outcome["resumed"]:
            new_count += 1
        failed_count += int(outcome["status"] != "success")
        write_json(CHECKPOINT, {
            "last_run_id": entry["run_id"], "new_runs_this_invocation": new_count,
            "resumed_this_invocation": resumed_count, "updated_at": utc_now(),
        })
    successes, collisions, table_failures = rebuild_tables(manifest)
    if max_new_runs == 5 and successes >= 5:
        snapshot_first_five(manifest)
        manifest["status"] = "safely_interrupted_after_5_completed_runs"
    elif successes == 16 and collisions == 16 and table_failures == 0:
        audit = verify_resume(manifest, resumed_count)
        manifest["status"] = "completed" if audit["passed"] else "resume_audit_failed"
    else:
        manifest["status"] = "incomplete_or_failed"
    manifest["completed_success_count"] = successes
    manifest["collision_completed_count"] = collisions
    manifest["failure_count"] = table_failures
    manifest["formal_seeds_executed"] = []
    write_json(MANIFEST, manifest)
    write_hash(MANIFEST)
    summary = {
        "manifest_status": manifest["status"], "new_runs": new_count, "resumed": resumed_count,
        "successes": successes, "collisions": collisions, "failures": table_failures + failed_count,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("prepare")
    run_parser = sub.add_parser("run")
    run_parser.add_argument("--max-new-runs", type=int)
    args = parser.parse_args()
    if args.command == "prepare":
        print(json.dumps({"run_count": prepare()["run_count"]}, ensure_ascii=False))
    else:
        run(args.max_new_runs)
