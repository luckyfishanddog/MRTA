"""Build the independent EPRK atomic formal protocol and frozen manifest."""

from __future__ import annotations

import csv
import hashlib
import json
import math
import platform
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parent
OUT = ROOT / "formal_atomic"
PROTOCOL_PATH = OUT / "EPRK_ATOMIC_FORMAL_PROTOCOL.json"
PROTOCOL_HASH_PATH = OUT / "EPRK_ATOMIC_FORMAL_PROTOCOL.sha256"
COLLISION_PATH = OUT / "ATOMIC_COLLISION_POLICY.json"
COLLISION_HASH_PATH = OUT / "ATOMIC_COLLISION_POLICY.sha256"
MANIFEST_PATH = OUT / "formal_manifest.json"
MANIFEST_CSV_PATH = OUT / "formal_manifest.csv"
MANIFEST_HASH_PATH = OUT / "formal_manifest.sha256"
REMEDIATION_DIR = OUT / "remediation"
TIME_LIMITS_PATH = REMEDIATION_DIR / "time_limits_by_instance.csv"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def write_json(path: Path, value: Any) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return sha256_file(path)


def write_hash(path: Path, digest: str) -> None:
    path.write_text(digest + "\n", encoding="utf-8")


def git_commit() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def collision_policy() -> tuple[dict[str, Any], str]:
    policy = {
        "policy_id": "atomic_collision_postprocess_v1",
        "version": "1.0.0",
        "post_processing_only": True,
        "included_in_search_objective": False,
        "included_in_algorithm_time": False,
        "included_in_solver_wall_clock_time": False,
        "included_in_equal_time_limit": False,
        "may_change_raw_fitness": False,
        "may_change_routes_or_directions": False,
        "raw_result_hash_must_remain_unchanged": True,
        "collision_success_requires_zero_unresolved": True,
        "safety_distance_m": 0.5,
        "boundary_band_width_m": 0.5,
        "sampling_interval_s": 1.0,
        "collision_model_source": "collision_aware_schedule.py",
        "collision_model_source_sha256": sha256_file(ROOT / "collision_aware_schedule.py"),
        "motion_model_source": "MRTA_GA_ACO.py",
        "motion_model_source_sha256": sha256_file(ROOT / "MRTA_GA_ACO.py"),
        "required_outputs": [
            "raw_makespan",
            "collision_adjusted_makespan",
            "added_waiting_time",
            "conflict_count",
            "unresolved_conflict_count",
            "collision_audit_time_s",
            "collision_audit_status",
        ],
    }
    digest = write_json(COLLISION_PATH, policy)
    write_hash(COLLISION_HASH_PATH, digest)
    return policy, digest


def qualification_gates() -> dict[str, Any]:
    return {
        "locked_before_results": True,
        "A_correctness": {
            "minimum_success_rate": 0.99,
            "require_all_hashes_match": True,
            "require_no_illegal_routes": True,
            "require_zero_unresolved_collisions": True,
            "require_all_successful_results_recomputable": True,
        },
        "B_equal_time_primary_endpoint": {
            "metric": "normalized_fitness",
            "minimum_overall_median_improvement_percent": 2.0,
            "wilcoxon_p_less_than": 0.05,
            "minimum_rank_biserial_favoring_eprk": 0.30,
            "bootstrap_95_ci_upper_less_than": 0.0,
            "minimum_instance_win_fraction": 0.70,
            "maximum_family_median_degradation_percent": 1.0,
        },
        "C_equal_primary": {
            "option_1": {
                "minimum_overall_median_improvement_percent": 1.0,
                "wilcoxon_p_less_than": 0.05,
            },
            "option_2": {
                "bootstrap_95_ci_upper_at_most_percent": 1.0,
                "require_not_significantly_worse": True,
                "minimum_better_or_tied_instance_fraction": 0.60,
            },
        },
        "D_significant_loss": {
            "maximum_holm_significant_hga_instance_fraction": 0.10,
            "maximum_per_family_holm_significant_hga_instance_fraction": 0.25,
        },
        "E_collision": {
            "overall_collision_adjusted_makespan_median_not_worse": True,
            "maximum_family_median_degradation_percent": 1.0,
            "require_zero_total_unresolved_conflicts": True,
        },
        "F_computational_efficiency": {
            "maximum_overall_median_eprk_hga_wall_time_ratio": 2.0,
            "maximum_overall_median_route_evaluations_per_primary": 5.0,
            "maximum_overall_median_direction_dp_per_primary": 2.0,
            "hidden_work_amplification_forbidden": True,
        },
        "G_stability": {
            "required_competitive_sizes": [30, 60, 100],
            "families_that_must_not_all_fail": [
                "long_weld_rich", "zero_travel_chain", "boundary_dense", "load_skewed"
            ],
            "single_family_only_advantage_forbidden": True,
        },
        "qualification_rule": "A_and_B_and_C_and_D_and_E_and_F_and_G",
    }


def statistical_protocol() -> dict[str, Any]:
    return {
        "primary_endpoint": "equal_time_normalized_fitness",
        "relative_gap_percent": "(EPRK-HGA)/HGA*100; negative favors EPRK",
        "seed_pairing": "same instance, mode and solver seed",
        "instance_level": {
            "test": "paired Wilcoxon signed-rank",
            "alternative": "two-sided",
            "zero_method": "wilcox",
            "tie_tolerance": 1e-12,
            "effect_size": "rank-biserial correlation, positive favors EPRK",
            "bootstrap": "10000 paired resamples of seed pairs",
            "bootstrap_seed": 20260716,
            "confidence_level": 0.95,
            "multiple_comparison": "Holm within each mode across all instances",
        },
        "family_level": {
            "unit": "per-instance median relative gap across 30 seeds",
            "test": "Wilcoxon signed-rank across instance medians",
            "bootstrap": "10000 instance-level resamples",
        },
        "overall": {
            "unit": "39 per-instance median relative gaps",
            "test": "Wilcoxon signed-rank",
            "friedman_forbidden_for_two_algorithms": True,
            "bootstrap": "10000 instance-level resamples",
        },
    }


def draft_protocol() -> dict[str, Any]:
    freeze = load_json(OUT / "EPRK_MA_FREEZE_MANIFEST.json")
    model = load_json(OUT / "ATOMIC_MODEL_FREEZE_MANIFEST.json")
    benchmark = load_json(OUT / "atomic_benchmark_manifest.json")
    _, collision_hash = collision_policy()
    return {
        "protocol_name": "EPRK-MA frozen atomic formal comparison",
        "protocol_version": "1.0.0",
        "status": "draft_pending_preflight_and_calibration",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_repository": "luckyfishanddog/MRTA",
        "source_repository_commit_sha": git_commit(),
        "legacy_protocol_independent": True,
        "legacy_protocol_path": "UNIFIED_EXPERIMENT_PROTOCOL.json",
        "legacy_protocol_sha256": sha256_file(ROOT / "UNIFIED_EXPERIMENT_PROTOCOL.json"),
        "legacy_manifest_path": "formal_experiment_manifest.json",
        "legacy_manifest_sha256": sha256_file(ROOT / "formal_experiment_manifest.json"),
        "user_authorization_source": "2026-07-16 explicit atomic formal-evaluation request",
        "approval_gates": {
            "protected_hashes_before_recorded": (OUT / "protected_hashes_before.json").is_file(),
            "eprk_candidate_frozen": True,
            "atomic_model_frozen": True,
            "benchmark_suite_ready": True,
            "all_instance_smoke_passed": all(
                item["smoke_eprk_passed"] and item["smoke_hga_passed"]
                for item in benchmark["instances"]
            ),
            "preflight_tests_passed": False,
            "calibration_completed": False,
            "formal_manifest_ready": False,
            "user_approved_formal_execution": False,
            "formal_run_approved": False,
        },
        "algorithms": {
            "eprk": {
                "display_name": "EPRK-MA-Frozen",
                "implementation": "MRTA_EPRK_MA.py",
                "solver_version": freeze["solver_version"],
                "profile_path": freeze["profile_path"],
                "profile_sha256": freeze["profile_sha256"],
                "source_hashes": freeze["source_hashes"],
                "parameter_changes_after_holdout": False,
            },
            "hga_atomic": {
                "display_name": "Paper-Aligned-HGA-Atomic-Control",
                "implementation": "MRTA_HGA_ATOMIC_CONTROL.py",
                "solver_version": "HGA-Atomic-Control-v0.1-development",
                "implementation_sha256": sha256_file(ROOT / "MRTA_HGA_ATOMIC_CONTROL.py"),
                "description_guard": "project-adapted control, not line-by-line reproduction of Liu et al. original C++ source",
                "eprk_mechanism_transfer_forbidden": True,
            },
        },
        "atomic_model": {
            "model_id": model["model_id"],
            "freeze_manifest_path": "formal_atomic/ATOMIC_MODEL_FREEZE_MANIFEST.json",
            "freeze_manifest_sha256": sha256_file(OUT / "ATOMIC_MODEL_FREEZE_MANIFEST.json"),
            "frozen_file_hashes": model["frozen_file_hashes"],
            "normalization_mode": model["normalization_mode"],
            "normalization_spec_sha256": model["normalization_spec_sha256"],
        },
        "benchmark": {
            "suite_id": benchmark["suite_id"],
            "manifest_path": "formal_atomic/atomic_benchmark_manifest.json",
            "manifest_sha256": sha256_file(OUT / "atomic_benchmark_manifest.json"),
            "instance_count": benchmark["instance_count"],
            "instances": [
                {
                    "instance_id": item["instance_id"],
                    "family": item["family"],
                    "nominal_weld_count": item["nominal_weld_count"],
                    "replicate": item["replicate"],
                    "source_hash": item["source_hash"],
                    "raw_file_sha256": item["raw_file_sha256"],
                    "atomic_hash": item["atomic_hash"],
                    "atomic_file_sha256": item["atomic_file_sha256"],
                    "normalization_hash": item["normalization_hash"],
                    "normalization_file_sha256": item["normalization_file_sha256"],
                }
                for item in benchmark["instances"]
            ],
        },
        "seeds": {
            "forbidden_development": list(range(101, 111)),
            "forbidden_legacy_formal": list(range(42, 72)),
            "calibration": [191, 192, 193],
            "formal_paired": list(range(201, 231)),
            "instance_generation": list(range(3001, 3037)),
        },
        "calibration": {
            "representative_instances": [
                "real_w30", "real_w45", "real_w60",
                "uniform_n60_r1", "clustered_n60_r1", "boundary_dense_n60_r1",
                "long_weld_rich_n60_r1", "zero_travel_chain_n60_r1", "load_skewed_n60_r1",
            ],
            "initial_primary_budget": 20000,
            "maximum_seconds_to_complete_initial_budget": 300.0,
            "global_fallback_primary_budget": 10000,
            "algorithm_parameters_may_change": False,
            "selected_equal_primary_budget": None,
            "completed": False,
        },
        "equal_time": {
            "reference_policy_for_noncalibration_instances": "use the same synthetic family's n60 replicate1 calibration; real instances use their own calibration",
            "raw_rule": "20 * median_seed(max(EPRK wall, HGA wall))",
            "rounding": "ceil to 10 seconds",
            "clamp_seconds": [30, 300],
            "same_limit_for_both_algorithms_within_instance": True,
            "only_stop_between_complete_primary_candidates": True,
            "collision_excluded": True,
            "statistics_excluded": True,
            "time_limits_path": None,
        },
        "collision": {
            "policy_path": "formal_atomic/ATOMIC_COLLISION_POLICY.json",
            "policy_sha256": collision_hash,
        },
        "execution": {
            "modes": ["equal_primary", "equal_time"],
            "expected_optimization_run_count": 4680,
            "sequential": True,
            "parallel": False,
            "collision_audit_during_solve": False,
            "early_stopping": False,
            "pair_order_rule": "SHA256(instance|seed|mode) parity; even EPRK then HGA, odd HGA then EPRK",
            "retry_limit_after_first_failure": 1,
            "resume_policy": "skip only successful matching protocol/source/profile/instance/normalization/command hashes",
            "hash_mismatch_policy": "move mismatched result to formal_atomic/quarantine",
            "failure_policy": "retain both attempts; never substitute another seed or silently exclude",
            "checkpoint_frequency_runs": 100,
        },
        "statistics": statistical_protocol(),
        "qualification_gates": qualification_gates(),
        "output_schema": {
            "raw_result": "one immutable result.json per attempt",
            "collision_result": "separate collision_postprocess.json",
            "required_identity": [
                "run_id", "mode", "instance_id", "family", "solver", "solver_seed",
                "protocol_hash", "atomic_instance_hash", "normalization_hash", "profile_hash", "command_hash",
            ],
            "required_metrics": [
                "fitness", "makespan", "load_imbalance", "idle_distance",
                "primary_evaluations", "solver_wall_clock_time_s",
                "route_evaluations_per_primary", "direction_dp_per_primary", "rss_mb",
            ],
        },
    }


def prepare_draft() -> dict[str, Any]:
    protocol = draft_protocol()
    digest = write_json(PROTOCOL_PATH, protocol)
    write_hash(PROTOCOL_HASH_PATH, digest)
    print(json.dumps({"status": protocol["status"], "protocol_sha256": digest}, ensure_ascii=False))
    return protocol


def finalize_protocol(calibration_summary: Mapping[str, Any], preflight: Mapping[str, Any]) -> dict[str, Any]:
    protocol = draft_protocol()
    preflight_passed = bool(
        preflight.get("atomic_formal_preflight_passed", preflight.get("passed", False))
    )
    if not preflight_passed:
        raise RuntimeError("preflight did not pass")
    if not calibration_summary.get("completed"):
        raise RuntimeError("calibration did not complete")
    selected_budget = int(calibration_summary["selected_equal_primary_budget"])
    if selected_budget not in (10000, 20000):
        raise ValueError("unexpected selected primary budget")
    protocol["status"] = "calibrated_smoke_ready_full_formal_not_authorized"
    protocol["finalized_at"] = datetime.now(timezone.utc).isoformat()
    protocol["approval_gates"].update({
        "preflight_tests_passed": True,
        "calibration_completed": True,
        "formal_manifest_ready": True,
        "user_approved_formal_execution": False,
        "formal_run_approved": False,
    })
    protocol["calibration"].update({
        "selected_equal_primary_budget": selected_budget,
        "completed": True,
        "summary_path": "formal_atomic/remediation/calibration_summary.json",
        "summary_sha256": sha256_file(REMEDIATION_DIR / "calibration_summary.json"),
    })
    protocol["equal_time"].update({
        "time_limits_path": "formal_atomic/remediation/time_limits_by_instance.csv",
        "time_limits_sha256": sha256_file(TIME_LIMITS_PATH),
    })
    protocol["preflight"] = dict(preflight)
    digest = write_json(PROTOCOL_PATH, protocol)
    write_hash(PROTOCOL_HASH_PATH, digest)
    return protocol


def _parity(instance: str, seed: int, mode: str) -> int:
    return int(hashlib.sha256(f"{instance}|{seed}|{mode}".encode("utf-8")).hexdigest(), 16) & 1


def _command_hash(command: Sequence[str]) -> str:
    return canonical_hash(list(command))


def generate_manifest(protocol: Mapping[str, Any], *, structural: bool = False) -> dict[str, Any]:
    if not structural and not protocol["calibration"].get("completed"):
        raise RuntimeError("resource calibration is incomplete")
    protocol_hash = sha256_file(PROTOCOL_PATH)
    benchmark = load_json(OUT / "atomic_benchmark_manifest.json")
    entries_by_id = {item["instance_id"]: item for item in benchmark["instances"]}
    limits = ({row["instance_id"]: int(row["time_limit_seconds"])
               for row in csv.DictReader(TIME_LIMITS_PATH.open(encoding="utf-8-sig"))}
              if not structural else {})
    budget = (int(protocol["calibration"]["selected_equal_primary_budget"])
              if not structural else int(protocol["calibration"]["initial_primary_budget"]))
    profile_hash = protocol["algorithms"]["eprk"]["profile_sha256"]
    hga_hash = protocol["algorithms"]["hga_atomic"]["implementation_sha256"]
    collision_hash = protocol["collision"]["policy_sha256"]
    rows: list[dict[str, Any]] = []
    order = 0
    for instance_id in benchmark["instance_order"]:
        item = entries_by_id[instance_id]
        for seed in protocol["seeds"]["formal_paired"]:
            for mode in protocol["execution"]["modes"]:
                solver_order = ("eprk", "hga_atomic") if _parity(instance_id, seed, mode) == 0 else ("hga_atomic", "eprk")
                for pair_position, solver in enumerate(solver_order, 1):
                    order += 1
                    run_id = f"{mode}__{instance_id}__{solver}__seed_{seed}"
                    requested_primary = budget if mode == "equal_primary" else None
                    time_limit = limits[instance_id] if mode == "equal_time" and not structural else None
                    command = [
                        "python", "run_eprk_atomic_formal.py", "execute", "--run-id", run_id,
                    ]
                    command_hash = _command_hash(command)
                    solver_identity = profile_hash if solver == "eprk" else hga_hash
                    identity = {
                        "protocol_hash": protocol_hash,
                        "source_hash": item["source_hash"],
                        "atomic_hash": item["atomic_hash"],
                        "normalization_hash": item["normalization_hash"],
                        "solver_identity_hash": solver_identity,
                        "solver_seed": seed,
                        "mode": mode,
                        "requested_primary": requested_primary,
                        "time_limit_seconds": time_limit,
                        "command_hash": command_hash,
                        "collision_policy_hash": collision_hash,
                    }
                    rows.append({
                        "execution_order": order,
                        "pair_position": pair_position,
                        "run_id": run_id,
                        "mode": mode,
                        "family": item["family"],
                        "instance_id": instance_id,
                        "nominal_weld_count": item["nominal_weld_count"],
                        "replicate": item["replicate"],
                        "instance_hash": item["source_hash"],
                        "raw_file_sha256": item["raw_file_sha256"],
                        "atomic_instance_hash": item["atomic_hash"],
                        "atomic_file_sha256": item["atomic_file_sha256"],
                        "normalization_hash": item["normalization_hash"],
                        "normalization_file_sha256": item["normalization_file_sha256"],
                        "solver": solver,
                        "solver_display_name": protocol["algorithms"][solver]["display_name"],
                        "solver_version": protocol["algorithms"][solver]["solver_version"],
                        "profile_hash": solver_identity,
                        "solver_seed": seed,
                        "requested_primary": requested_primary,
                        "time_limit_seconds": time_limit,
                        "protocol_hash": protocol_hash,
                        "expected_output_path": f"formal_atomic/runs/{mode}/{instance_id}/{solver}/seed_{seed}",
                        "resume_key": canonical_hash(identity),
                        "command": command,
                        "command_hash": command_hash,
                        "collision_policy_hash": collision_hash,
                    })
    if len(rows) != 4680 or len({row["run_id"] for row in rows}) != 4680:
        raise AssertionError("formal manifest must contain 4680 unique runs")
    manifest = {
        "manifest_status": "structural_precalibration" if structural else "calibrated_full_matrix_locked_not_authorized",
        "resource_calibration_pending": structural,
        "formal_execution_authorized": False,
        "protocol_hash": protocol_hash,
        "run_count": len(rows),
        "instance_count": 39,
        "solver_count": 2,
        "paired_seed_count": 30,
        "mode_count": 2,
        "sequential": True,
        "parallel": False,
        "entries": rows,
    }
    manifest_hash = write_json(MANIFEST_PATH, manifest)
    write_hash(MANIFEST_HASH_PATH, manifest_hash)
    fields = [key for key in rows[0] if key != "command"] + ["command_json"]
    with MANIFEST_CSV_PATH.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            value = {key: row.get(key) for key in fields}
            value["command_json"] = json.dumps(row["command"], ensure_ascii=False, separators=(",", ":"))
            writer.writerow(value)
    return manifest


def prepare_structural_manifest() -> dict[str, Any]:
    protocol = load_json(PROTOCOL_PATH)
    if protocol["approval_gates"].get("formal_run_approved"):
        raise RuntimeError("structural manifest cannot be generated from an execution-approved protocol")
    return generate_manifest(protocol, structural=True)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("draft", "structural-manifest"))
    args = parser.parse_args()
    if args.command == "draft":
        prepare_draft()
    else:
        result = prepare_structural_manifest()
        print(json.dumps({"manifest_status": result["manifest_status"], "run_count": result["run_count"]}))
