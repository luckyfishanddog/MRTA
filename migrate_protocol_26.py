#!/usr/bin/env python3
"""One-shot, deterministic migration from protocol 2.5 to 2.6.

The script deliberately does not inspect or modify solver implementations or the
frozen ABMA profile.  It only restructures orchestration authority and freezes
the scale-calibration, collision-postprocess, formal-manifest, and statistics
policies before any scale experiment is executed.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "UNIFIED_EXPERIMENT_PROTOCOL.json"
ARCHIVE = ROOT / "archive" / "historical_protocols" / (
    "UNIFIED_EXPERIMENT_PROTOCOL_v2.5.0_"
    "f6a1d11957e19f4f55eb61f8e006313ce04bca0d64852b29b25417af1e7ed4f8.json"
)


def main() -> None:
    protocol = json.loads(SOURCE.read_text(encoding="utf-8"))
    if protocol.get("protocol_version") != "2.5.0":
        raise SystemExit("migration requires protocol_version 2.5.0")

    historical_budget = copy.deepcopy(protocol["budget"].pop("historical_pilot_evidence"))
    current_w30_budget = copy.deepcopy(protocol["budget"].pop("current_three_solver_pilot_evidence"))
    snapshots = {
        "development": {
            "recorded_at_stage_end": "2026-07-15",
            "abma_development_outcome": protocol.pop("abma_development_outcome"),
            "abma_profile_profiling_evidence": protocol.pop("abma_profile_profiling_evidence"),
            "abma_development_gate_evidence": protocol.pop("abma_development_gate_evidence"),
            "abma_final_profile_status": protocol.pop("abma_final_profile_status"),
        },
        "independent_validation": {
            "recorded_at_stage_end": "2026-07-15",
            "abma_independent_validation": protocol.pop("abma_independent_validation"),
        },
        "w30_budget_pilot": {
            "recorded_at_stage_end": "2026-07-15",
            "three_solver_w30_preflight": protocol.pop("three_solver_w30_preflight"),
            "historical_pilot_evidence": historical_budget,
            "current_three_solver_pilot_evidence": current_w30_budget,
        },
    }

    protocol["protocol_version"] = "2.6.0"
    protocol["status"] = "scale_calibration_and_formal_manifest_preparation_not_formal_execution"
    protocol["current_stage"] = "w45_w60_scale_calibration_collision_postprocess_and_formal_protocol_freeze"
    protocol["previous_protocol"] = {
        "version": "2.5.0",
        "sha256": "f6a1d11957e19f4f55eb61f8e006313ce04bca0d64852b29b25417af1e7ed4f8",
        "archived_path": ARCHIVE.relative_to(ROOT).as_posix(),
    }
    protocol["historical_stage_snapshots"] = snapshots
    protocol["approval_gates"] = {
        "normalization_baselines_computed_and_hashed": True,
        "normalization_unit_tests_passed": True,
        "abma_development_gate_passed": True,
        "abma_final_candidate_frozen": True,
        "abma_validation_completed": True,
        "three_solver_cross_validation_completed": True,
        "w30_budget_pilot_completed": True,
        "scale_calibration_completed": False,
        "formal_experiment_manifest_ready": False,
        "user_approved_formal_execution": False,
    }
    protocol["formal_run_approved"] = False

    protocol["budget"].update({
        "scale_preflight_budget": 2000,
        "scale_target_budget": 16000,
        "scale_projection_multiplier": 8.0,
        "scale_safety_margin_threshold": 1.5,
        "scale_candidate_budget_floor": 4000,
        "scale_candidate_budget_ceiling": 16000,
        "scale_candidate_budget_rounding": 1000,
        "scale_timeout_s": 21600,
        "formal_budget_scheme": "pending_user_selection_between_A_and_B",
        "formal_timeout_scheme": "pending_user_selection_between_global_and_per_instance",
        "early_stopping_policy": "disabled_for_all_scale_and_formal_profiles",
        "formal_budget_implementation_status": "manifest_only; execution_not_approved",
    })
    protocol["scale_calibration"] = {
        "formal": False,
        "ranking_performed": False,
        "instances": ["w45", "w60"],
        "solvers": ["ga_aco", "paper_aligned_hga", "abma"],
        "solver_seed": 42,
        "sequential_execution": True,
        "preflight_budget": 2000,
        "target_budget": 16000,
        "wall_clock_timeout_s": 21600,
        "collision_audit_during_solver": False,
        "candidate_formula": "floor_to_1000(16000*21600/(1.5*max_projected_wall_seconds)), clamped to [4000,16000]",
        "target_is_safe_when": "21600/max_projected_wall_seconds >= 1.5",
        "required_exact_budget_gate": True,
        "calibration_results_location": "external stage outputs; protocol is immutable before execution",
    }
    protocol["collision_audit"] = {
        "enabled_for_smoke": False,
        "enabled_for_formal": False,
        "required_as_separate_postprocess": True,
        "post_processing_only": True,
        "included_in_primary_objective": False,
        "may_change_boundaries_routes_or_directions": False,
        "safety_distance": 0.5,
        "boundary_band_width": 0.5,
        "sampling_interval": 1.0,
        "timing_policy": "collision_audit_time is separate from algorithm_time and solver_wall_clock_time",
        "raw_metric_policy": "raw fitness, makespan, boundaries, routes, and directions remain byte-for-byte unchanged",
        "required_fields": [
            "collision_adjusted_makespan", "added_waiting_time", "conflict_count",
            "unresolved_conflict_count", "collision_audit_time", "audit_status"
        ],
        "failure_policy": "write an explicit failed audit record and never claim collision success",
        "output_policy": "write collision_postprocess.json separately; never overwrite result.json or unified_metrics.json",
    }
    protocol["formal_experiment_design"] = {
        "execution_authorized": False,
        "manifest_only": True,
        "instances": ["w30", "w45", "w60"],
        "solvers": ["ga_aco", "paper_aligned_hga", "abma"],
        "paired_solver_seeds": list(range(42, 72)),
        "expected_run_count": 270,
        "abma_variant": "legacy_exact_fast",
        "budget_options": {
            "A": "one global budget: minimum validated instance budget, rounded down to 1000",
            "B": "validated per-instance budgets shared by all three solvers within each instance",
        },
        "timeout_options": {
            "global": "maximum of all per-instance timeout recommendations",
            "per_instance": "ceil_to_300(max(21600, 1.5*max_observed_wall_seconds+300))",
        },
        "selected_budget_scheme": None,
        "selected_timeout_scheme": None,
        "resume_policy": "skip only a successful run with matching protocol, scientific-source, and command hashes",
        "failure_policy": "retain failed/timeout attempt; never silently substitute or include it in success statistics",
        "collision_policy": "audit frozen successful raw results as a separate postprocess; report raw and adjusted metrics separately",
    }
    protocol["statistics"] = {
        "unit": "paired solver seed within each frozen instance",
        "success_definition": "successful exact-budget raw run with valid independent reconstruction",
        "descriptive_statistics": ["mean", "sample_standard_deviation", "median", "minimum", "maximum", "IQR", "success_rate", "algorithm_time"],
        "reported_metric_families": {
            "raw": ["fitness", "makespan", "load_imbalance", "total_idle_distance"],
            "collision_adjusted": ["collision_adjusted_makespan", "added_waiting_time", "conflict_count", "unresolved_conflict_count"],
        },
        "omnibus_test": "Friedman test per instance on raw fitness across the three paired solvers",
        "pairwise_test": "two-sided paired Wilcoxon signed-rank test for all three solver pairs per instance",
        "multiplicity": "Holm correction within the three pairwise tests of each instance",
        "effect_size": "rank-biserial correlation for paired Wilcoxon comparisons",
        "win_tie_loss": "per-seed raw-fitness W/T/L with absolute tie tolerance 1e-12",
        "confidence_interval": "paired median difference percentile bootstrap, 10000 resamples, deterministic seed 20260715, 95% CI",
        "missing_failure_policy": "report success rate; inferential tests use only complete paired successful seeds and disclose paired count",
        "ranking_policy": "no ranking from calibration or collision-audit Pilot data",
    }
    protocol["sensitivity_design"] = {
        "execution_authorized": False,
        "design_only": True,
        "separate_output_root": "unified_experiments/sensitivity",
        "dimensions": {
            "objective_weights": "one-at-a-time alternatives around [0.7,0.2,0.1], renormalized to sum one",
            "load_scale_floor": "predeclared positive floors without changing the frozen main normalization",
            "baseline_construction": "alternative deterministic baseline constructors in isolated protocol snapshots",
            "collision_separation": "raw optimization and collision postprocess remain distinct metric families",
        },
        "main_experiment_contamination_forbidden": True,
    }
    protocol["known_limitations"] = list(dict.fromkeys(list(protocol["known_limitations"]) + [
        "Protocol 2.6 freezes scale-calibration rules before execution; calibrated values live in separately hashed stage outputs.",
        "Collision audit is a deterministic postprocess Pilot and is not part of solver fitness or ranking.",
        "Formal budget and timeout options require an explicit user choice; formal execution remains fail-closed.",
    ]))

    archive_rel = ARCHIVE.relative_to(ROOT).as_posix()
    if archive_rel not in protocol["source_evidence_files"]:
        protocol["source_evidence_files"].append(archive_rel)

    SOURCE.write_text(json.dumps(protocol, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
