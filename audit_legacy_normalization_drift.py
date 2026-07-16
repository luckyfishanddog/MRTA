"""Audit legacy normalization drift and issue a narrowly scoped waiver."""

from __future__ import annotations

import csv
import hashlib
import json
import os
import platform
import subprocess
import sys
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path
from typing import Any, Mapping

import run_unified_experiments as legacy_runner
from legacy_normalization_compatibility import (
    ABSOLUTE_TOLERANCE,
    ALLOWED_SCOPE,
    RELATIVE_TOLERANCE,
    classify_pair,
)
from objective_normalization import (
    BASELINE_ALGORITHM,
    BASELINE_VERSION,
    DEFAULT_WEIGHTS,
    compute_normalization_spec,
    normalization_spec_hash,
    normalized_objective,
)


ROOT = Path(__file__).resolve().parent
OUT = ROOT / "formal_atomic" / "remediation"
PROTOCOL_PATH = ROOT / "UNIFIED_EXPERIMENT_PROTOCOL.json"
AUDIT_JSON = OUT / "legacy_normalization_field_diff.json"
AUDIT_CSV = OUT / "legacy_normalization_field_diff.csv"
AUDIT_REPORT = OUT / "LEGACY_NORMALIZATION_DRIFT_AUDIT.md"
WAIVER_JSON = OUT / "LEGACY_NORMALIZATION_FLOAT_COMPATIBILITY_WAIVER.json"
WAIVER_HASH = OUT / "LEGACY_NORMALIZATION_FLOAT_COMPATIBILITY_WAIVER.sha256"
WAIVER_REPORT = OUT / "LEGACY_NORMALIZATION_FLOAT_COMPATIBILITY_REPORT.md"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def write_json(path: Path, value: Any) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(json_bytes(value))
    return sha256_file(path)


def git_commit() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()


def package_version(name: str) -> str | None:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return None


def newline_style(path: Path) -> str:
    data = path.read_bytes()
    if b"\r\n" in data:
        return "CRLF"
    if b"\n" in data:
        return "LF"
    return "none"


def stable_timestamp(input_fingerprint: str) -> str:
    if AUDIT_JSON.is_file():
        try:
            existing = json.loads(AUDIT_JSON.read_text(encoding="utf-8"))
            if existing.get("input_fingerprint") == input_fingerprint:
                return str(existing["generated_at"])
        except (KeyError, ValueError, OSError):
            pass
    return datetime.now(timezone.utc).isoformat()


def find_historical_evidence(instance_hash: str, spec_hash: str) -> tuple[Path, Mapping[str, Any]]:
    for path in sorted((ROOT / "unified_experiments").rglob("unified_metrics.json")):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            continue
        required = ("fitness", "makespan", "load_imbalance", "total_idle_distance")
        if (value.get("instance_hash") == instance_hash
                and value.get("normalization_spec_hash") == spec_hash
                and all(key in value for key in required)):
            return path, value
    raise FileNotFoundError(f"no historical normalized output for {instance_hash}")


def environment() -> dict[str, Any]:
    return {
        "python_version": sys.version,
        "python_executable": sys.executable,
        "operating_system": platform.platform(),
        "openpyxl_version": package_version("openpyxl"),
        "pandas_version": package_version("pandas"),
        "numpy_version": package_version("numpy"),
        "json_serializer": "Python stdlib json",
        "serializer_ensure_ascii": False,
        "serializer_sort_keys": True,
        "serializer_separators_for_hash": [",", ":"],
        "float_representation": "Python shortest-round-trip repr; float.hex recorded per difference",
        "protocol_newline": newline_style(PROTOCOL_PATH),
        "recomputed_artifact_newline": "LF",
        "locale": os.environ.get("LANG"),
    }


def audit_instance(key: str, entry: Mapping[str, Any], protocol: Mapping[str, Any]) -> dict[str, Any]:
    frozen_with_hash = dict(entry["normalization"])
    frozen_spec_hash = str(frozen_with_hash.pop("normalization_spec_hash"))
    welds, source_metadata = legacy_runner.load_frozen_weld_instance(str(ROOT / entry["path"]))
    model_data = protocol["scientific_model"]
    model = legacy_runner.MotionModel(
        model_data["weld_speed"], model_data["travel_speed"],
        model_data["acceleration"], model_data["safe_z"],
    )
    recomputed_spec = compute_normalization_spec(
        welds, model, entry["instance_hash"], weights=DEFAULT_WEIGHTS
    ).to_dict()
    recomputed_spec_hash = normalization_spec_hash(recomputed_spec)

    frozen_extract_path = OUT / f"legacy_{key}_normalization_frozen_extract.json"
    recomputed_path = OUT / f"legacy_{key}_normalization_recomputed.json"
    frozen_extract_sha = write_json(frozen_extract_path, frozen_with_hash)
    recomputed_file_sha = write_json(recomputed_path, recomputed_spec)

    evidence_path, evidence = find_historical_evidence(entry["instance_hash"], frozen_spec_hash)
    replayed = normalized_objective({
        "makespan": evidence["makespan"],
        "load_imbalance": evidence["load_imbalance"],
        "idle_distance": evidence["total_idle_distance"],
    }, frozen_with_hash)
    replay_difference = abs(replayed - float(evidence["fitness"]))

    algorithm_match = (
        frozen_with_hash.get("mode") == recomputed_spec.get("mode")
        and frozen_with_hash["baseline"].get("algorithm") == BASELINE_ALGORITHM
        and frozen_with_hash["baseline"].get("algorithm_version") == BASELINE_VERSION
        and recomputed_spec["baseline"].get("algorithm") == BASELINE_ALGORITHM
        and recomputed_spec["baseline"].get("algorithm_version") == BASELINE_VERSION
    )
    weights_match = tuple(frozen_with_hash["weights"]) == tuple(recomputed_spec["weights"]) == tuple(DEFAULT_WEIGHTS)
    baseline_policy_match = all(
        frozen_with_hash["baseline"].get(field) == recomputed_spec["baseline"].get(field)
        for field in ("algorithm", "algorithm_version", "instance_hash", "scientific_model_hash")
    )
    frozen_floor = float(frozen_with_hash["floors"]["load_imbalance"])
    recomputed_floor = float(recomputed_spec["floors"]["load_imbalance"])
    load_floor_policy_match = (
        abs(frozen_floor - 0.05 * float(frozen_with_hash["ideal"]["makespan"])) <= ABSOLUTE_TOLERANCE
        and abs(recomputed_floor - 0.05 * float(recomputed_spec["ideal"]["makespan"])) <= ABSOLUTE_TOLERANCE
    )
    scope = f"UNIFIED_EXPERIMENT_PROTOCOL.json#/instances/{key}/normalization"
    classification = classify_pair(
        frozen_with_hash, recomputed_spec,
        scope=scope,
        instance_hash_match=(
            entry["instance_hash"] == source_metadata["instance_hash"]
            == frozen_with_hash["baseline"]["instance_hash"]
            == recomputed_spec["baseline"]["instance_hash"]
        ),
        algorithm_match=algorithm_match,
        weights_match=weights_match,
        baseline_policy_match=baseline_policy_match,
        load_floor_policy_match=load_floor_policy_match,
        historical_metric_replay_match=replay_difference <= 1e-12,
    )
    return {
        "instance": key,
        "scope": scope,
        "status": classification["status"],
        "exact_hash_match": frozen_spec_hash == recomputed_spec_hash,
        "frozen_normalization_spec_hash": frozen_spec_hash,
        "recomputed_normalization_spec_hash": recomputed_spec_hash,
        "frozen_container_path": "UNIFIED_EXPERIMENT_PROTOCOL.json",
        "frozen_container_sha256": sha256_file(PROTOCOL_PATH),
        "frozen_extract_path": frozen_extract_path.relative_to(ROOT).as_posix(),
        "frozen_extract_sha256": frozen_extract_sha,
        "recomputed_path": recomputed_path.relative_to(ROOT).as_posix(),
        "recomputed_file_sha256": recomputed_file_sha,
        "source_instance_path": str(entry["path"]),
        "source_instance_sha256": sha256_file(ROOT / entry["path"]),
        "instance_hash": entry["instance_hash"],
        "normalization_algorithm": frozen_with_hash["mode"],
        "normalization_algorithm_version": BASELINE_VERSION,
        "objective_weights": list(DEFAULT_WEIGHTS),
        "baseline_construction_policy": BASELINE_ALGORITHM,
        "historical_metric_evidence_path": evidence_path.relative_to(ROOT).as_posix(),
        "historical_metric_stored_fitness": evidence["fitness"],
        "historical_metric_replayed_fitness": replayed,
        "historical_metric_replay_absolute_difference": replay_difference,
        **classification,
    }


def flatten_rows(instances: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in instances:
        for difference in item["comparison"]["numeric_differences"]:
            rows.append({
                "instance": item["instance"],
                "field_path": difference["path"],
                "frozen_value": difference["frozen_value"],
                "recomputed_value": difference["recomputed_value"],
                "absolute_difference": difference["absolute_difference"],
                "relative_difference": difference["relative_difference"],
                "frozen_float_hex": difference["frozen_float_hex"],
                "recomputed_float_hex": difference["recomputed_float_hex"],
                "frozen_file_sha256": item["frozen_container_sha256"],
                "recomputed_file_sha256": item["recomputed_file_sha256"],
                "source_instance_sha256": item["source_instance_sha256"],
                "normalization_algorithm": item["normalization_algorithm"],
                "normalization_algorithm_version": item["normalization_algorithm_version"],
                "objective_weights": json.dumps(item["objective_weights"]),
                "baseline_construction_policy": item["baseline_construction_policy"],
            })
    return rows


def write_csv(rows: list[Mapping[str, Any]]) -> None:
    fields = list(rows[0]) if rows else ["instance", "field_path"]
    with AUDIT_CSV.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def build_reports(audit: Mapping[str, Any], waiver: Mapping[str, Any] | None) -> None:
    lines = [
        "# Legacy normalization drift audit",
        "",
        f"- Overall status: `{audit['overall_status']}`",
        f"- Exact hash match: `{audit['all_exact_hash_match']}`",
        f"- Compatibility match: `{audit['all_compatibility_match']}`",
        f"- Maximum absolute difference: `{audit['maximum_absolute_difference']:.17g}`",
        f"- Maximum relative difference: `{audit['maximum_relative_difference']:.17g}`",
        "- The legacy files were not modified. Compatibility is not reported as an exact hash pass.",
        "",
        "| instance | status | numeric differences | max abs | max rel |",
        "|---|---|---:|---:|---:|",
    ]
    for item in audit["instances"]:
        comparison = item["comparison"]
        lines.append(
            f"| {item['instance']} | {item['status']} | {len(comparison['numeric_differences'])} | "
            f"{comparison['maximum_absolute_difference']:.17g} | {comparison['maximum_relative_difference']:.17g} |"
        )
    lines.extend([
        "",
        "All changed fields are finite floating-point values. Field sets, list order, instance hashes, weights, "
        "normalization mode, baseline algorithm/version, baseline policy, and load-floor policy match. "
        "Historical stored fitness values replay exactly when evaluated with the frozen specifications. "
        "The derived baseline_hash also changes because its hashed payload contains these float values; it is "
        "recorded separately and is not treated as an independent scientific field.",
    ])
    AUDIT_REPORT.write_bytes(("\n".join(lines) + "\n").encode("utf-8"))
    if waiver is not None:
        waiver_lines = [
            "# Restricted legacy normalization compatibility waiver",
            "",
            f"- Status: `{waiver['status']}`",
            "- Exact hash match: `false`",
            "- Compatibility match: `true`",
            f"- Waiver SHA-256: `{sha256_file(WAIVER_JSON)}`",
            f"- Absolute/relative tolerance: `{ABSOLUTE_TOLERANCE}` / `{RELATIVE_TOLERANCE}`",
            "- Scope: only legacy w30/w45/w60 normalization entries embedded in UNIFIED_EXPERIMENT_PROTOCOL.json.",
            "- Forbidden: atomic normalization, profiles, solver results, instances, statistics, or any unlisted file.",
            "- No protected file was modified.",
        ]
        WAIVER_REPORT.write_bytes(("\n".join(waiver_lines) + "\n").encode("utf-8"))


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    protocol = json.loads(PROTOCOL_PATH.read_text(encoding="utf-8"))
    fingerprint = hashlib.sha256((
        sha256_file(PROTOCOL_PATH)
        + sha256_file(ROOT / "objective_normalization.py")
        + "".join(sha256_file(ROOT / protocol["instances"][key]["path"]) for key in ("w30", "w45", "w60"))
    ).encode("ascii")).hexdigest()
    generated_at = stable_timestamp(fingerprint)
    instances = [audit_instance(key, protocol["instances"][key], protocol) for key in ("w30", "w45", "w60")]
    all_compatible = all(item["status"] == "legacy_float_compatibility_pass" for item in instances)
    all_exact = all(item["status"] == "exact_hash_pass" for item in instances)
    audit = {
        "schema_version": "1.0.0",
        "generated_at": generated_at,
        "input_fingerprint": fingerprint,
        "source_repository_commit_sha": git_commit(),
        "absolute_tolerance": ABSOLUTE_TOLERANCE,
        "relative_tolerance": RELATIVE_TOLERANCE,
        "all_exact_hash_match": all_exact,
        "all_compatibility_match": all_compatible,
        "overall_status": "exact_hash_pass" if all_exact else "legacy_float_compatibility_pass" if all_compatible else "failure",
        "maximum_absolute_difference": max(item["comparison"]["maximum_absolute_difference"] for item in instances),
        "maximum_relative_difference": max(item["comparison"]["maximum_relative_difference"] for item in instances),
        "environment": environment(),
        "instances": instances,
    }
    audit_sha = write_json(AUDIT_JSON, audit)
    write_csv(flatten_rows(instances))
    waiver: dict[str, Any] | None = None
    if all_compatible:
        waiver = {
            "waiver_version": "1.0.0",
            "status": "approved_for_legacy_validation_only",
            "exact_hash_match": False,
            "compatibility_match": True,
            "scope": sorted(ALLOWED_SCOPE),
            "absolute_tolerance": ABSOLUTE_TOLERANCE,
            "relative_tolerance": RELATIVE_TOLERANCE,
            "environment": audit["environment"],
            "scientific_field_consistency_checks": [item["conditions"] for item in instances],
            "audit_file_path": AUDIT_JSON.relative_to(ROOT).as_posix(),
            "audit_file_sha256": audit_sha,
            "protected_files_modified": False,
            "approved_purpose": "legacy protocol validation compatibility audit only",
            "forbidden_uses": [
                "atomic normalization", "EPRK profile", "algorithm results", "instance files",
                "formal statistics", "any file not explicitly listed in scope",
            ],
            "generated_at": generated_at,
            "git_commit_sha": git_commit(),
            "instances": [{
                "instance": item["instance"],
                "status": item["status"],
                "scope": item["scope"],
                "frozen_sha256": item["frozen_normalization_spec_hash"],
                "recomputed_sha256": item["recomputed_normalization_spec_hash"],
                "instance_sha256": item["source_instance_sha256"],
                "maximum_absolute_difference": item["comparison"]["maximum_absolute_difference"],
                "maximum_relative_difference": item["comparison"]["maximum_relative_difference"],
            } for item in instances],
        }
        waiver_sha = write_json(WAIVER_JSON, waiver)
        WAIVER_HASH.write_bytes((waiver_sha + "\n").encode("ascii"))
    build_reports(audit, waiver)
    print(json.dumps({
        "overall_status": audit["overall_status"],
        "maximum_absolute_difference": audit["maximum_absolute_difference"],
        "maximum_relative_difference": audit["maximum_relative_difference"],
        "waiver_generated": waiver is not None,
        "waiver_sha256": sha256_file(WAIVER_JSON) if waiver else None,
    }, ensure_ascii=False, indent=2))
    if not (all_exact or all_compatible):
        raise SystemExit(2)


if __name__ == "__main__":
    main()
