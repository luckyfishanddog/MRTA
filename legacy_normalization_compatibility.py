"""Strict, scope-limited compatibility checks for legacy normalization drift.

This module never mutates a frozen normalization.  It only classifies a pair
as exact, compatible floating-point drift, or failure.
"""

from __future__ import annotations

import math
import copy
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence


ABSOLUTE_TOLERANCE = 1e-12
RELATIVE_TOLERANCE = 1e-12
ALLOWED_SCOPE = {
    "UNIFIED_EXPERIMENT_PROTOCOL.json#/instances/w30/normalization",
    "UNIFIED_EXPERIMENT_PROTOCOL.json#/instances/w45/normalization",
    "UNIFIED_EXPERIMENT_PROTOCOL.json#/instances/w60/normalization",
}
FORBIDDEN_SCOPE_PREFIXES = (
    "formal_atomic/instances/normalization/",
    "formal_atomic/runs/",
    "formal_atomic/remediation/smoke_runs/",
    "eprk_development_outputs/",
)


def is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def relative_difference(left: float, right: float) -> float:
    difference = abs(left - right)
    if difference == 0.0:
        return 0.0
    return difference / max(abs(left), abs(right), 1e-300)


def compare_values(frozen: Any, recomputed: Any, path: str = "$") -> dict[str, Any]:
    """Return a complete structural and value comparison."""
    result: dict[str, Any] = {
        "field_sets_match": True,
        "list_order_match": True,
        "numeric_differences": [],
        "non_numeric_differences": [],
        "missing_fields": [],
        "extra_fields": [],
        "non_finite_fields": [],
        "sign_changes": [],
    }

    def walk(left: Any, right: Any, current: str) -> None:
        if isinstance(left, Mapping) and isinstance(right, Mapping):
            left_keys = set(left)
            right_keys = set(right)
            missing = sorted(left_keys - right_keys)
            extra = sorted(right_keys - left_keys)
            if missing or extra:
                result["field_sets_match"] = False
                result["missing_fields"].extend(f"{current}/{key}" for key in missing)
                result["extra_fields"].extend(f"{current}/{key}" for key in extra)
            for key in sorted(left_keys & right_keys):
                walk(left[key], right[key], f"{current}/{key}")
            return
        if isinstance(left, (list, tuple)) and isinstance(right, (list, tuple)):
            if len(left) != len(right):
                result["list_order_match"] = False
                result["non_numeric_differences"].append({
                    "path": current,
                    "frozen_value": f"length={len(left)}",
                    "recomputed_value": f"length={len(right)}",
                    "reason": "list_length_mismatch",
                })
            for index, (left_item, right_item) in enumerate(zip(left, right)):
                walk(left_item, right_item, f"{current}/{index}")
            return
        if is_number(left) and is_number(right):
            frozen_number = float(left)
            recomputed_number = float(right)
            if not math.isfinite(frozen_number) or not math.isfinite(recomputed_number):
                result["non_finite_fields"].append(current)
                return
            if frozen_number != recomputed_number:
                absolute = abs(frozen_number - recomputed_number)
                relative = relative_difference(frozen_number, recomputed_number)
                row = {
                    "path": current,
                    "frozen_value": frozen_number,
                    "recomputed_value": recomputed_number,
                    "absolute_difference": absolute,
                    "relative_difference": relative,
                    "frozen_float_hex": frozen_number.hex(),
                    "recomputed_float_hex": recomputed_number.hex(),
                    "within_absolute_tolerance": absolute <= ABSOLUTE_TOLERANCE,
                    "within_relative_tolerance": relative <= RELATIVE_TOLERANCE,
                }
                result["numeric_differences"].append(row)
                if frozen_number and recomputed_number and math.copysign(1.0, frozen_number) != math.copysign(1.0, recomputed_number):
                    result["sign_changes"].append(current)
            return
        if type(left) is not type(right) or left != right:
            result["non_numeric_differences"].append({
                "path": current,
                "frozen_value": left,
                "recomputed_value": right,
                "reason": "type_or_value_mismatch",
            })

    walk(frozen, recomputed, path)
    numeric = result["numeric_differences"]
    result.update({
        "maximum_absolute_difference": max((row["absolute_difference"] for row in numeric), default=0.0),
        "maximum_relative_difference": max((row["relative_difference"] for row in numeric), default=0.0),
        "numeric_only": not result["non_numeric_differences"] and not result["missing_fields"] and not result["extra_fields"],
        "all_numeric_differences_within_tolerance": all(
            row["within_absolute_tolerance"] and row["within_relative_tolerance"] for row in numeric
        ),
    })
    return result


def classify_pair(
    frozen: Mapping[str, Any],
    recomputed: Mapping[str, Any],
    *,
    scope: str,
    instance_hash_match: bool,
    algorithm_match: bool,
    weights_match: bool,
    baseline_policy_match: bool,
    load_floor_policy_match: bool,
    historical_metric_replay_match: bool,
    exact_hash_match: bool | None = None,
) -> dict[str, Any]:
    frozen_semantic = copy.deepcopy(dict(frozen))
    recomputed_semantic = copy.deepcopy(dict(recomputed))
    frozen_baseline_hash = frozen_semantic.get("baseline", {}).pop("baseline_hash", None)
    recomputed_baseline_hash = recomputed_semantic.get("baseline", {}).pop("baseline_hash", None)
    comparison = compare_values(frozen_semantic, recomputed_semantic)
    exact = (frozen == recomputed) if exact_hash_match is None else bool(exact_hash_match)
    scope_allowed = scope in ALLOWED_SCOPE and not scope.startswith(FORBIDDEN_SCOPE_PREFIXES)
    conditions = {
        "scope_allowed": scope_allowed,
        "numeric_fields_only": comparison["numeric_only"],
        "field_sets_match": comparison["field_sets_match"],
        "list_order_match": comparison["list_order_match"],
        "instance_hash_match": bool(instance_hash_match),
        "normalization_algorithm_and_version_match": bool(algorithm_match),
        "weights_match": bool(weights_match),
        "baseline_construction_policy_match": bool(baseline_policy_match),
        "load_floor_policy_match": bool(load_floor_policy_match),
        "all_numeric_differences_within_tolerance": comparison["all_numeric_differences_within_tolerance"],
        "no_nan_inf_or_sign_change": not comparison["non_finite_fields"] and not comparison["sign_changes"],
        "historical_metric_replay_match": bool(historical_metric_replay_match),
        "derived_baseline_hash_fields_present": (
            isinstance(frozen_baseline_hash, str) and len(frozen_baseline_hash) == 64
            and isinstance(recomputed_baseline_hash, str) and len(recomputed_baseline_hash) == 64
        ),
    }
    compatible = not exact and all(conditions.values())
    status = "exact_hash_pass" if exact else "legacy_float_compatibility_pass" if compatible else "failure"
    return {
        "status": status,
        "exact_match": exact,
        "compatibility_match": compatible,
        "scope": scope,
        "conditions": conditions,
        "comparison": comparison,
        "derived_integrity_differences": [{
            "path": "$/baseline/baseline_hash",
            "frozen_value": frozen_baseline_hash,
            "recomputed_value": recomputed_baseline_hash,
            "reason": "digest_of_payload_containing_compatible_float_drift",
        }] if frozen_baseline_hash != recomputed_baseline_hash else [],
    }


def validate_waiver_payload(payload: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if payload.get("status") != "approved_for_legacy_validation_only":
        errors.append("waiver status is not approved_for_legacy_validation_only")
    if payload.get("exact_hash_match") is not False:
        errors.append("waiver must record exact_hash_match=false")
    if payload.get("compatibility_match") is not True:
        errors.append("waiver must record compatibility_match=true")
    scope = set(payload.get("scope", []))
    if scope != ALLOWED_SCOPE:
        errors.append("waiver scope is not exactly the three legacy normalizations")
    if payload.get("absolute_tolerance") != ABSOLUTE_TOLERANCE:
        errors.append("waiver absolute tolerance changed")
    if payload.get("relative_tolerance") != RELATIVE_TOLERANCE:
        errors.append("waiver relative tolerance changed")
    if payload.get("protected_files_modified") is not False:
        errors.append("waiver does not affirm protected_files_modified=false")
    for item in payload.get("instances", []):
        if item.get("status") != "legacy_float_compatibility_pass":
            errors.append(f"non-compatible instance in waiver: {item.get('instance')}")
    return errors


def verify_waiver_file(path: Path, hash_path: Path) -> tuple[dict[str, Any] | None, list[str]]:
    errors: list[str] = []
    try:
        expected = hash_path.read_text(encoding="ascii").strip()
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if expected != actual:
            errors.append("waiver hash mismatch")
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return None, [f"waiver read failure: {type(exc).__name__}: {exc}"]
    errors.extend(validate_waiver_payload(payload))
    return payload, errors
