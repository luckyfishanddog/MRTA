#!/usr/bin/env python3
"""Finalize protocol 2.6 gates and evidence after successful calibration."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent
PATH = ROOT / "UNIFIED_EXPERIMENT_PROTOCOL.json"


def main() -> None:
    protocol = json.loads(PATH.read_text(encoding="utf-8"))
    if protocol.get("protocol_version") != "2.6.0":
        raise SystemExit("finalization requires protocol 2.6.0")
    if protocol.get("formal_run_approved") is not False:
        raise SystemExit("formal_run_approved must remain false")
    protocol["status"] = "scale_calibration_completed_manifest_ready_formal_execution_not_approved"
    protocol["approval_gates"]["scale_calibration_completed"] = True
    protocol["approval_gates"]["formal_experiment_manifest_ready"] = True
    protocol["approval_gates"]["user_approved_formal_execution"] = False
    protocol["formal_run_approved"] = False
    protocol["scale_calibration_outcome"] = {
        "completed": True,
        "formal": False,
        "ranking_performed": False,
        "execution_protocol_hash": "19084d42c77dd61803f622f7dd3792c50fea0f387106740c6f6d6bcedc437202",
        "w45": {
            "preflight_budget": 2000,
            "projected_16000_max_wall_seconds": 9919.462147199549,
            "projected_16000_safety_ratio": 2.1775374188103624,
            "validated_budget": 16000,
            "max_observed_solver_wall_seconds": 10019.285029400024,
            "summary": "w45_scale_pilot_summary.json"
        },
        "w60": {
            "preflight_budget": 2000,
            "projected_16000_max_wall_seconds": 20737.095319999382,
            "projected_16000_safety_ratio": 1.041611646505208,
            "validated_budget": 11000,
            "max_observed_solver_wall_seconds": 13669.840594200068,
            "summary": "w60_scale_pilot_summary.json"
        },
        "budget_options": "cross_instance_budget_options.json",
        "timeout_global_seconds": 21600,
        "timeout_per_instance_seconds": {"w30": 21600, "w45": 21600, "w60": 21600}
    }
    protocol["collision_audit_pilot_evidence"] = {
        "completed": True,
        "formal": False,
        "ranking_performed": False,
        "audited_run_count": 9,
        "all_raw_files_unchanged": True,
        "audit_failure_count": 0,
        "unresolved_conflict_count_total": 0,
        "summary": "collision_audit_pilot_summary.json",
        "report": "COLLISION_AUDIT_POLICY_REPORT.md"
    }
    protocol["formal_manifest_status"] = {
        "ready": True,
        "execution_authorized": False,
        "expected_run_count": 270,
        "budget_selection_pending": True,
        "json": "formal_experiment_manifest.json",
        "csv": "formal_experiment_manifest.csv",
        "execution_plan": "FORMAL_EXPERIMENT_EXECUTION_PLAN.md"
    }
    evidence = [
        "w45_scale_preflight_runs.csv", "w45_scale_pilot_summary.json", "W45_SCALE_CALIBRATION_REPORT.md",
        "w60_scale_preflight_runs.csv", "w60_scale_pilot_summary.json", "W60_SCALE_CALIBRATION_REPORT.md",
        "cross_instance_budget_options.json", "CROSS_INSTANCE_BUDGET_RECOMMENDATION.md",
        "collision_audit_pilot_runs.csv", "collision_audit_pilot_summary.json", "COLLISION_AUDIT_POLICY_REPORT.md",
        "formal_experiment_manifest.json", "formal_experiment_manifest.csv", "FORMAL_EXPERIMENT_EXECUTION_PLAN.md",
        "双滑轨四悬臂机器人任务分配及焊缝排序.docx"
    ]
    for item in evidence:
        if item not in protocol["source_evidence_files"]:
            protocol["source_evidence_files"].append(item)
    PATH.write_text(json.dumps(protocol, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
