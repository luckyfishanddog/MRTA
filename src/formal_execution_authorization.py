"""Create and verify the independent authorization for the frozen formal matrix."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
FORMAL = ROOT / "formal_atomic"
EXECUTION = FORMAL / "formal_execution"
AUTHORIZATION_PATH = EXECUTION / "FORMAL_EXECUTION_AUTHORIZATION.json"
AUTHORIZATION_HASH_PATH = EXECUTION / "FORMAL_EXECUTION_AUTHORIZATION.sha256"
PROTOCOL_PATH = FORMAL / "EPRK_ATOMIC_FORMAL_PROTOCOL.json"
MANIFEST_PATH = FORMAL / "formal_manifest.json"
PROFILE_PATH = FORMAL / "eprk_ma_final_profile.json"
MODEL_FREEZE_PATH = FORMAL / "ATOMIC_MODEL_FREEZE_MANIFEST.json"
BENCHMARK_PATH = FORMAL / "atomic_benchmark_manifest.json"
COLLISION_PATH = FORMAL / "ATOMIC_COLLISION_POLICY.json"
EXPECTED_PR_HEAD = "02491f127a32e2f497efa973ae4a6752c5a18f0d"
EXPECTED_SOURCE_BRANCH = "fix/atomic-formal-preflight-normalization-waiver"
EXPECTED_EXECUTION_BRANCH = "experiment/eprk-hga-atomic-formal-comparison"
EXPECTED_SEEDS = list(range(201, 231))
EXPECTED_MODES = ["equal_primary", "equal_time"]
EXPECTED_RUN_COUNT = 4680


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def build_authorization() -> dict[str, Any]:
    protocol = load_json(PROTOCOL_PATH)
    manifest = load_json(MANIFEST_PATH)
    if protocol["approval_gates"]["formal_run_approved"] is not False:
        raise RuntimeError("frozen protocol must remain independently fail-closed")
    if manifest.get("formal_execution_authorized") is not False:
        raise RuntimeError("frozen manifest must remain independently fail-closed")
    payload = {
        "authorization_version": "1.0.0",
        "authorization_status": "approved",
        "authorization_scope": "EPRK-vs-HGA atomic formal comparison",
        "repository": "luckyfishanddog/MRTA",
        "pr_number": 2,
        "pr_head_sha": EXPECTED_PR_HEAD,
        "source_branch": EXPECTED_SOURCE_BRANCH,
        "execution_branch": EXPECTED_EXECUTION_BRANCH,
        "formal_protocol_path": "formal_atomic/EPRK_ATOMIC_FORMAL_PROTOCOL.json",
        "formal_protocol_sha256": sha256_file(PROTOCOL_PATH),
        "formal_manifest_path": "formal_atomic/formal_manifest.json",
        "formal_manifest_sha256": sha256_file(MANIFEST_PATH),
        "eprk_profile_path": "formal_atomic/eprk_ma_final_profile.json",
        "eprk_profile_sha256": sha256_file(PROFILE_PATH),
        "atomic_model_freeze_path": "formal_atomic/ATOMIC_MODEL_FREEZE_MANIFEST.json",
        "atomic_model_freeze_sha256": sha256_file(MODEL_FREEZE_PATH),
        "benchmark_suite_path": "formal_atomic/atomic_benchmark_manifest.json",
        "benchmark_suite_sha256": sha256_file(BENCHMARK_PATH),
        "collision_policy_path": "formal_atomic/ATOMIC_COLLISION_POLICY.json",
        "collision_policy_sha256": sha256_file(COLLISION_PATH),
        "authorized_solver_seeds": EXPECTED_SEEDS,
        "authorized_modes": EXPECTED_MODES,
        "authorized_run_count": EXPECTED_RUN_COUNT,
        "authorized_solvers": ["eprk", "hga_atomic"],
        "user_approval_source": "current conversation dated 2026-07-17",
        "no_parameter_changes": True,
        "no_scientific_model_changes": True,
        "authorization_timestamp": datetime.now(timezone.utc).isoformat(),
    }
    return payload


def create_authorization() -> tuple[dict[str, Any], str]:
    payload = build_authorization()
    write_json(AUTHORIZATION_PATH, payload)
    digest = sha256_file(AUTHORIZATION_PATH)
    AUTHORIZATION_HASH_PATH.write_text(digest + "\n", encoding="ascii")
    return payload, digest


def verify_authorization(path: Path = AUTHORIZATION_PATH) -> tuple[dict[str, Any], list[str]]:
    errors: list[str] = []
    hash_path = path.with_suffix(".sha256")
    if not path.is_file() or not hash_path.is_file():
        return {}, ["authorization or companion hash is missing"]
    payload = load_json(path)
    if hash_path.read_text(encoding="ascii").strip() != sha256_file(path):
        errors.append("authorization companion hash mismatch")
    expected_scalars = {
        "authorization_status": "approved",
        "authorization_scope": "EPRK-vs-HGA atomic formal comparison",
        "repository": "luckyfishanddog/MRTA",
        "pr_number": 2,
        "pr_head_sha": EXPECTED_PR_HEAD,
        "source_branch": EXPECTED_SOURCE_BRANCH,
        "execution_branch": EXPECTED_EXECUTION_BRANCH,
        "authorized_run_count": EXPECTED_RUN_COUNT,
        "no_parameter_changes": True,
        "no_scientific_model_changes": True,
    }
    for key, expected in expected_scalars.items():
        if payload.get(key) != expected:
            errors.append(f"authorization field mismatch: {key}")
    if payload.get("authorized_solver_seeds") != EXPECTED_SEEDS:
        errors.append("authorized solver seeds mismatch")
    if payload.get("authorized_modes") != EXPECTED_MODES:
        errors.append("authorized modes mismatch")
    if payload.get("authorized_solvers") != ["eprk", "hga_atomic"]:
        errors.append("authorized solvers mismatch")
    bound_files = {
        "formal_protocol_sha256": PROTOCOL_PATH,
        "formal_manifest_sha256": MANIFEST_PATH,
        "eprk_profile_sha256": PROFILE_PATH,
        "atomic_model_freeze_sha256": MODEL_FREEZE_PATH,
        "benchmark_suite_sha256": BENCHMARK_PATH,
        "collision_policy_sha256": COLLISION_PATH,
    }
    for key, target in bound_files.items():
        if payload.get(key) != sha256_file(target):
            errors.append(f"authorization-bound hash mismatch: {key}")
    protocol = load_json(PROTOCOL_PATH)
    manifest = load_json(MANIFEST_PATH)
    if protocol["approval_gates"].get("formal_run_approved") is not False:
        errors.append("frozen protocol authorization flag was modified")
    if manifest.get("formal_execution_authorized") is not False:
        errors.append("frozen manifest authorization flag was modified")
    if manifest.get("run_count") != EXPECTED_RUN_COUNT or len(manifest.get("entries", [])) != EXPECTED_RUN_COUNT:
        errors.append("formal manifest does not contain exactly 4680 entries")
    return payload, errors


if __name__ == "__main__":
    payload, digest = create_authorization()
    print(json.dumps({
        "authorization_status": payload["authorization_status"],
        "authorized_run_count": payload["authorized_run_count"],
        "authorization_sha256": digest,
    }, ensure_ascii=False, indent=2))
