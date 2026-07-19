"""Freeze the reviewed EPRK candidate and atomic scientific model."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from project_paths import resolve_project_path


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "formal_atomic"
EXPECTED_PROFILE_HASH = "de75ad629aeeff608cab96097fe96aed091237348b9f1fe3e6f94c68442771f2"
EXPECTED_NORMALIZATION_HASH = "031cfa7c8fe1668e38c463f6142807d09a3f159d6c627f2cf8d5164c4aaf4dcb"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path: Path, value: Any) -> str:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return sha256_file(path)


def write_hash(path: Path, digest: str) -> None:
    path.write_text(digest + "\n", encoding="utf-8")


def git_commit() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    frozen_at = datetime.now(timezone.utc).isoformat()

    candidate = resolve_project_path("eprk_ma_candidate_profile.json")
    final_profile = OUT / "eprk_ma_final_profile.json"
    shutil.copyfile(candidate, final_profile)
    if candidate.read_bytes() != final_profile.read_bytes():
        raise RuntimeError("final profile is not a byte-for-byte copy")
    profile_hash = sha256_file(final_profile)
    if profile_hash != EXPECTED_PROFILE_HASH:
        raise RuntimeError(f"unexpected EPRK profile hash: {profile_hash}")
    write_hash(OUT / "eprk_ma_final_profile.sha256", profile_hash)

    development_protocol_hash = sha256_file(resolve_project_path("EPRK_MA_DEVELOPMENT_PROTOCOL.json"))
    freeze_manifest = {
        "status": "frozen_for_atomic_formal_evaluation",
        "algorithm_name": "EPRK-MA",
        "formal_algorithm_name": "EPRK-MA-Frozen",
        "solver_version": "EPRK-MA-v0.1-development",
        "profile_path": "formal_atomic/eprk_ma_final_profile.json",
        "profile_sha256": profile_hash,
        "byte_identical_to_candidate_profile": True,
        "source_hashes": {
            name: sha256_file(resolve_project_path(name))
            for name in (
                "MRTA_EPRK_MA.py",
                "atomic_problem_core.py",
                "atomic_route_evaluator.py",
                "atomic_weld_preprocessor.py",
                "build_atomic_normalization.py",
            )
        },
        "development_protocol_path": "EPRK_MA_DEVELOPMENT_PROTOCOL.json",
        "development_protocol_sha256": development_protocol_hash,
        "development_gate_evidence_paths": [
            "EPRK_MA_DEVELOPMENT_REPORT.md",
            "eprk_development_runs.csv",
            "eprk_equal_time_runs.csv",
            "eprk_development_outputs/development_gate_summary.json",
        ],
        "holdout_evidence_paths": [
            "eprk_holdout_runs.csv",
            "eprk_development_outputs/holdout_summary.json",
        ],
        "freeze_timestamp": frozen_at,
        "no_tuning_after_holdout": True,
        "holdout_has_been_viewed": True,
        "future_change_policy": {
            "requires_new_algorithm_version": True,
            "requires_new_profile": True,
            "requires_new_development_gate": True,
            "requires_new_holdout": True,
            "may_reuse_this_formal_evidence": False,
        },
        "source_repository": "luckyfishanddog/MRTA",
        "source_repository_commit_sha": git_commit(),
    }
    freeze_path = OUT / "EPRK_MA_FREEZE_MANIFEST.json"
    freeze_hash = write_json(freeze_path, freeze_manifest)
    write_hash(OUT / "EPRK_MA_FREEZE_MANIFEST.sha256", freeze_hash)

    model_files = (
        "ATOMIC_WELD_MODEL_SPEC.md",
        "atomic_weld_preprocessor.py",
        "atomic_problem_core.py",
        "atomic_route_evaluator.py",
        "build_atomic_normalization.py",
    )
    normalization_hash = sha256_file(resolve_project_path("atomic_normalization_spec.json"))
    if normalization_hash != EXPECTED_NORMALIZATION_HASH:
        raise RuntimeError(f"unexpected atomic normalization hash: {normalization_hash}")
    model_manifest = {
        "status": "frozen_for_atomic_formal_evaluation",
        "model_id": "atomic_weld_partition_routing_v1",
        "model_version": "1.0.0",
        "frozen_at": frozen_at,
        "source_repository_commit_sha": git_commit(),
        "platform": {"width_m": 20.0, "height_m": 12.0},
        "fixed_y_cut_m": 6.0,
        "preprocessing_order": ["split_at_y_6", "horizontal_projection_lmax_lmin_split"],
        "long_weld_policy": {
            "split_measure": "horizontal_projection",
            "lmax_m": 5.0,
            "lmin_m": 1.0,
        },
        "search_time_splitting": False,
        "atomic_welds_may_cross_region": False,
        "boundary_selection": "legal_discrete_events_only",
        "direction_mode": "exact_two_state_dynamic_programming",
        "route_type": "open",
        "include_initial_positioning": False,
        "include_final_return": False,
        "setup_time_s": 0.0,
        "post_processing_time_s": 0.0,
        "zero_travel_endpoint_tolerance_m": 1e-9,
        "objective_weights": [0.7, 0.2, 0.1],
        "normalization_mode": "ideal_baseline_range_v1_per_instance",
        "collision_policy": "separate_postprocess_not_in_search_objective",
        "normalization_spec_path": "atomic_normalization_spec.json",
        "normalization_spec_sha256": normalization_hash,
        "frozen_file_hashes": {name: sha256_file(resolve_project_path(name)) for name in model_files},
    }
    model_path = OUT / "ATOMIC_MODEL_FREEZE_MANIFEST.json"
    model_hash = write_json(model_path, model_manifest)
    write_hash(OUT / "ATOMIC_MODEL_FREEZE_MANIFEST.sha256", model_hash)

    (OUT / "EPRK_MA_FREEZE_REPORT.md").write_text(
        "# EPRK-MA 候选冻结报告\n\n"
        f"- 冻结时间：`{frozen_at}`\n"
        "- 状态：`frozen_for_atomic_formal_evaluation`\n"
        "- 正式名称：`EPRK-MA-Frozen`\n"
        f"- profile SHA-256：`{profile_hash}`\n"
        f"- freeze manifest SHA-256：`{freeze_hash}`\n"
        "- profile 为候选文件的字节级副本，未加入或改写任何字段。\n"
        "- holdout 已查看；冻结后禁止调参。任何参数或源代码变化均要求新版本、新 profile、新开发门禁和新 holdout。\n",
        encoding="utf-8",
    )
    (OUT / "ATOMIC_MODEL_FREEZE_REPORT.md").write_text(
        "# 固定原子焊缝科学模型冻结报告\n\n"
        f"- 冻结时间：`{frozen_at}`\n"
        "- 模型：`atomic_weld_partition_routing_v1`\n"
        f"- model freeze manifest SHA-256：`{model_hash}`\n"
        f"- normalization SHA-256：`{normalization_hash}`\n"
        "- y=6 先切分，随后按水平投影执行 Lmax=5 m/Lmin=1 m 预切分；搜索期间禁止再次切分。\n"
        "- 开放路线、无初始定位/返回、setup/post=0、精确二状态方向 DP、每实例独立归一化。\n"
        "- 碰撞审计是独立后处理，不进入搜索目标。\n",
        encoding="utf-8",
    )

    print(json.dumps({
        "profile_sha256": profile_hash,
        "freeze_manifest_sha256": freeze_hash,
        "atomic_model_manifest_sha256": model_hash,
        "source_repository_commit_sha": git_commit(),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
