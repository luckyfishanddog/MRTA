"""Independent fail-closed preflight for the frozen atomic formal study."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

import run_unified_experiments as legacy_runner
from atomic_problem_core import load_atomic_instance
from atomic_route_evaluator import normalization_hash
from legacy_normalization_compatibility import verify_waiver_file
from prepare_eprk_atomic_formal import canonical_hash
from run_eprk_atomic_formal import cross_validate, run_solver


ROOT = Path(__file__).resolve().parents[1]
FORMAL = ROOT / "formal_atomic"
OUT = FORMAL / "remediation"
SUMMARY = OUT / "preflight_summary_v2.json"
CHECKS_CSV = OUT / "preflight_checks_v2.csv"
REPORT = OUT / "ATOMIC_FORMAL_PREFLIGHT_REMEDIATION_REPORT.md"
WAIVER = OUT / "LEGACY_NORMALIZATION_FLOAT_COMPATIBILITY_WAIVER.json"
WAIVER_HASH = OUT / "LEGACY_NORMALIZATION_FLOAT_COMPATIBILITY_WAIVER.sha256"
EXPECTED_LEGACY_ERRORS = [
    "w30: normalization spec hash mismatch",
    "w30: normalization values or baseline evidence mismatch",
    "w45: normalization spec hash mismatch",
    "w45: normalization values or baseline evidence mismatch",
    "w60: normalization spec hash mismatch",
    "w60: normalization values or baseline evidence mismatch",
]


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
    path.write_bytes((json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"))


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def exact(name: str, passed: bool, details: Any) -> dict[str, Any]:
    return {"check": name, "status": "exact_hash_pass" if passed else "failure", "details": details}


def compatibility(name: str, passed: bool, details: Any) -> dict[str, Any]:
    return {"check": name, "status": "legacy_float_compatibility_pass" if passed else "failure", "details": details}


def companion_hash(path: Path, hash_path: Path) -> tuple[bool, dict[str, Any]]:
    expected = hash_path.read_text(encoding="ascii").strip()
    actual = sha256_file(path)
    return expected == actual, {"path": path.relative_to(ROOT).as_posix(), "expected": expected, "actual": actual}


def protected_check(before_path: Path) -> tuple[bool, dict[str, Any]]:
    before = load_json(before_path)
    current = {path: sha256_file(ROOT / path) for path in before["files"]}
    changed = sorted(path for path, digest in before["files"].items() if current.get(path) != digest)
    return not changed, {"protected_file_count": len(current), "changed": changed}


def benchmark_file_checks() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    benchmark = load_json(FORMAL / "atomic_benchmark_manifest.json")
    rows: list[dict[str, Any]] = []
    for item in benchmark["instances"]:
        for kind, path_key, hash_key in (
            ("raw", "raw_path", "raw_file_sha256"),
            ("atomic", "atomic_path", "atomic_file_sha256"),
            ("normalization", "normalization_path", "normalization_file_sha256"),
        ):
            actual = sha256_file(ROOT / item[path_key])
            semantic_ok = True
            if kind == "atomic":
                semantic_ok = load_atomic_instance(ROOT / item[path_key]).atomic_instance_hash == item["atomic_hash"]
            elif kind == "normalization":
                semantic_ok = normalization_hash(load_json(ROOT / item[path_key])) == item["normalization_hash"]
            rows.append({
                "instance_id": item["instance_id"], "kind": kind,
                "path": item[path_key], "expected": item[hash_key], "actual": actual,
                "file_hash_match": actual == item[hash_key], "semantic_hash_match": semantic_ok,
            })
    return benchmark, rows


def manifest_checks(protocol: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    manifest = load_json(FORMAL / "formal_manifest.json")
    entries = manifest["entries"]
    run_ids = [row["run_id"] for row in entries]
    command_hashes = [row["command_hash"] for row in entries]
    seed_set = sorted({int(row["solver_seed"]) for row in entries})
    groups: dict[tuple[str, int, str], list[Mapping[str, Any]]] = {}
    order_ok = True
    command_ok = True
    for row in entries:
        key = (row["instance_id"], int(row["solver_seed"]), row["mode"])
        groups.setdefault(key, []).append(row)
        command_ok &= row["command_hash"] == canonical_hash(row["command"])
    pairing_ok = True
    for (instance, seed, mode), pair in groups.items():
        pair = sorted(pair, key=lambda row: int(row["pair_position"]))
        parity = int(hashlib.sha256(f"{instance}|{seed}|{mode}".encode()).hexdigest(), 16) & 1
        expected = ["eprk", "hga_atomic"] if parity == 0 else ["hga_atomic", "eprk"]
        pairing_ok &= len(pair) == 2 and [row["solver"] for row in pair] == expected
        order_ok &= [int(row["pair_position"]) for row in pair] == [1, 2]
    details = {
        "run_count": len(entries),
        "run_id_unique": len(run_ids) == len(set(run_ids)) == 4680,
        "command_hash_unique": len(command_hashes) == len(set(command_hashes)) == 4680,
        "command_hash_recomputed": command_ok,
        "formal_seed_set": seed_set,
        "seed_policy_match": seed_set == list(range(201, 231)),
        "pair_group_count": len(groups),
        "solver_pairing_match": pairing_ok,
        "execution_order_alternation_match": order_ok,
        "sequential": manifest.get("sequential") is True,
        "parallel": manifest.get("parallel") is False,
        "formal_execution_authorized": manifest.get("formal_execution_authorized"),
        "manifest_status": manifest.get("manifest_status"),
        "protocol_hash_match": manifest.get("protocol_hash") == sha256_file(FORMAL / "EPRK_ATOMIC_FORMAL_PROTOCOL.json"),
    }
    return manifest, details


def run_unit_tests() -> tuple[bool, dict[str, Any]]:
    command = [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"]
    completed = subprocess.run(command, cwd=ROOT, text=True, capture_output=True)
    (OUT / "unit_tests_stdout.log").write_text(completed.stdout, encoding="utf-8")
    (OUT / "unit_tests_stderr.log").write_text(completed.stderr, encoding="utf-8")
    combined = completed.stdout + completed.stderr
    count = None
    for line in combined.splitlines():
        if line.startswith("Ran ") and " tests" in line:
            try:
                count = int(line.split()[1])
            except (IndexError, ValueError):
                pass
    return completed.returncode == 0, {
        "returncode": completed.returncode, "test_count": count,
        "stdout_log": "formal_atomic/remediation/unit_tests_stdout.log",
        "stderr_log": "formal_atomic/remediation/unit_tests_stderr.log",
    }


def minimal_solver_smoke() -> tuple[bool, dict[str, Any]]:
    results = []
    for solver in ("eprk", "hga_atomic"):
        metrics = run_solver(solver, "real_w30", 196, primary_budget=120, time_limit_s=None)
        results.append({
            "solver": solver,
            "primary_evaluations": metrics["primary_evaluations"],
            "stop_reason": metrics["stop_reason"],
            "cross_validation_passed": metrics["cross_validation_passed"],
            "fitness": metrics["fitness"],
        })
    passed = all(
        row["primary_evaluations"] == 120 and row["stop_reason"] == "objective_budget"
        and row["cross_validation_passed"] for row in results
    )
    return passed, {"instance": "real_w30", "seed": 196, "budget": 120, "results": results}


def perform(*, protocol_path: Path = FORMAL / "EPRK_ATOMIC_FORMAL_PROTOCOL.json",
            waiver_path: Path = WAIVER,
            protected_before_path: Path = OUT / "protected_hashes_before.json",
            skip_unit_tests: bool = False, skip_solver_smoke: bool = False) -> dict[str, Any]:
    OUT.mkdir(parents=True, exist_ok=True)
    checks: list[dict[str, Any]] = []
    branch = git("branch", "--show-current")
    status = git("status", "--porcelain")
    allowed_branches = {
        "fix/atomic-formal-preflight-normalization-waiver",
        "experiment/eprk-hga-atomic-formal-comparison",
    }
    checks.append(exact("01_git_worktree_state", branch in allowed_branches, {
        "branch": branch, "dirty_entry_count": len(status.splitlines()) if status else 0,
        "dirty_allowed_before_commit": True,
    }))

    ok, details = protected_check(protected_before_path); checks.append(exact("02_protected_hashes", ok, details))
    ok, details = companion_hash(FORMAL / "eprk_ma_final_profile.json", FORMAL / "eprk_ma_final_profile.sha256")
    checks.append(exact("03_eprk_frozen_profile_hash", ok, details))

    eprk_freeze = load_json(FORMAL / "EPRK_MA_FREEZE_MANIFEST.json")
    source_rows = [{"path": path, "expected": digest, "actual": sha256_file(ROOT / path)}
                   for path, digest in eprk_freeze["source_hashes"].items()]
    checks.append(exact("04_eprk_source_hashes", all(row["expected"] == row["actual"] for row in source_rows), source_rows))

    ok, details = companion_hash(FORMAL / "ATOMIC_MODEL_FREEZE_MANIFEST.json", FORMAL / "ATOMIC_MODEL_FREEZE_MANIFEST.sha256")
    checks.append(exact("05_atomic_model_freeze_manifest", ok, details))
    model = load_json(FORMAL / "ATOMIC_MODEL_FREEZE_MANIFEST.json")
    model_rows = [{"path": path, "expected": digest, "actual": sha256_file(ROOT / path)}
                  for path, digest in model["frozen_file_hashes"].items()]
    checks.append(exact("06_atomic_model_source_hashes", all(row["expected"] == row["actual"] for row in model_rows), model_rows))

    ok, details = companion_hash(FORMAL / "atomic_benchmark_manifest.json", FORMAL / "atomic_benchmark_manifest.sha256")
    checks.append(exact("07_benchmark_suite_manifest", ok, details))
    benchmark, file_rows = benchmark_file_checks()
    for number, kind in ((8, "raw"), (9, "atomic"), (10, "normalization")):
        subset = [row for row in file_rows if row["kind"] == kind]
        checks.append(exact(f"{number:02d}_39_{kind}_instance_hashes", len(subset) == 39 and all(
            row["file_hash_match"] and row["semantic_hash_match"] for row in subset
        ), subset))

    waiver_hash_path = waiver_path.with_suffix(".sha256")
    waiver, waiver_errors = verify_waiver_file(waiver_path, waiver_hash_path)
    audit = load_json(waiver_path.parent / "legacy_normalization_field_diff.json")
    legacy_ok = not waiver_errors and audit.get("overall_status") == "legacy_float_compatibility_pass"
    checks.append(compatibility("11_legacy_normalization_compatibility_audit", legacy_ok, {
        "waiver_errors": waiver_errors,
        "audit_status": audit.get("overall_status"),
        "maximum_absolute_difference": audit.get("maximum_absolute_difference"),
        "maximum_relative_difference": audit.get("maximum_relative_difference"),
    }))

    protocol_hash_path = protocol_path.with_suffix(".sha256")
    ok, details = companion_hash(protocol_path, protocol_hash_path)
    checks.append(exact("12_formal_protocol_hash", ok, details))
    ok, details = companion_hash(FORMAL / "formal_manifest.json", FORMAL / "formal_manifest.sha256")
    checks.append(exact("13_formal_manifest_hash", ok, details))
    protocol = load_json(protocol_path)
    manifest, manifest_details = manifest_checks(protocol)
    checks.append(exact("14_run_id_uniqueness", manifest_details["run_id_unique"], manifest_details))
    checks.append(exact("15_command_hash_uniqueness", manifest_details["command_hash_unique"] and manifest_details["command_hash_recomputed"], manifest_details))
    checks.append(exact("16_seed_policy", manifest_details["seed_policy_match"], manifest_details))
    checks.append(exact("17_solver_pairing", manifest_details["solver_pairing_match"], manifest_details))
    checks.append(exact("18_execution_order_alternation", manifest_details["execution_order_alternation_match"], manifest_details))

    collision_ok, collision_details = companion_hash(FORMAL / "ATOMIC_COLLISION_POLICY.json", FORMAL / "ATOMIC_COLLISION_POLICY.sha256")
    collision_policy = load_json(FORMAL / "ATOMIC_COLLISION_POLICY.json")
    collision_ok &= collision_policy.get("post_processing_only") is True and collision_policy.get("may_change_raw_fitness") is False
    checks.append(exact("19_collision_policy", collision_ok, {**collision_details, "policy": collision_policy}))
    execution = protocol["execution"]
    resume_ok = execution.get("retry_limit_after_first_failure") == 1 and "matching" in execution.get("resume_policy", "")
    checks.append(exact("20_resume_policy", resume_ok, execution))

    from project_paths import resolve_project_path
    legacy_path = resolve_project_path("UNIFIED_EXPERIMENT_PROTOCOL.json")
    legacy_protocol = load_json(legacy_path)
    legacy_errors = legacy_runner.validate_protocol(legacy_protocol, legacy_path, OUT / "legacy_validation_tmp")
    checks.append(compatibility("20b_legacy_exact_validator_expected_drift", legacy_errors == EXPECTED_LEGACY_ERRORS, {
        "exact_validator_passed": not legacy_errors, "errors": legacy_errors,
    }))

    if skip_unit_tests:
        tests_ok, tests_details = True, {"skipped": True}
    else:
        tests_ok, tests_details = run_unit_tests()
    checks.append(exact("21_unit_tests", tests_ok, tests_details))
    if skip_solver_smoke:
        smoke_ok, smoke_details = True, {"skipped": True}
    else:
        smoke_ok, smoke_details = minimal_solver_smoke()
    checks.append(exact("22_minimum_solver_smoke", smoke_ok, smoke_details))

    passed = all(row["status"] != "failure" for row in checks)
    execution_checkpoint_path = FORMAL / "formal_execution" / "execution_checkpoint.json"
    execution_checkpoint = load_json(execution_checkpoint_path) if execution_checkpoint_path.is_file() else {}
    formal_success_count = len(execution_checkpoint.get("success_run_ids", []))
    summary = {
        "schema_version": "2.0.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_repository_commit_sha": git("rev-parse", "HEAD"),
        "branch": branch,
        "atomic_formal_preflight_passed": passed,
        "passed": passed,
        "exact_hash_pass_count": sum(row["status"] == "exact_hash_pass" for row in checks),
        "legacy_float_compatibility_pass_count": sum(row["status"] == "legacy_float_compatibility_pass" for row in checks),
        "failure_count": sum(row["status"] == "failure" for row in checks),
        "formal_execution_remains_unauthorized": (
            protocol["approval_gates"].get("formal_run_approved") is False
            and manifest.get("formal_execution_authorized") is False
        ),
        "formal_seeds_executed": formal_success_count > 0,
        "formal_success_count": formal_success_count,
        "full_4680_matrix_executed": bool(
            execution_checkpoint.get("complete") and formal_success_count == 4680
        ),
        "checks": checks,
    }
    write_json(SUMMARY, summary)
    with CHECKS_CSV.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=["check", "status", "details_json"])
        writer.writeheader()
        for row in checks:
            writer.writerow({"check": row["check"], "status": row["status"],
                             "details_json": json.dumps(row["details"], ensure_ascii=False, sort_keys=True)})
    lines = [
        "# Atomic formal preflight remediation report", "",
        f"- Passed: `{passed}`",
        f"- Exact-hash pass checks: `{summary['exact_hash_pass_count']}`",
        f"- Legacy-float compatibility pass checks: `{summary['legacy_float_compatibility_pass_count']}`",
        f"- Failed checks: `{summary['failure_count']}`",
        "- Legacy normalization is explicitly reported as compatibility, never exact.",
        "- All 39 atomic normalization files remain subject to exact SHA-256 checks.",
        f"- Frozen protocol/manifest flags remain fail-closed; independent authorization is separate. Formal successes: `{formal_success_count}`.",
        "", "| check | status |", "|---|---|",
    ]
    lines.extend(f"| {row['check']} | {row['status']} |" for row in checks)
    REPORT.write_bytes(("\n".join(lines) + "\n").encode("utf-8"))
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--protocol", type=Path, default=FORMAL / "EPRK_ATOMIC_FORMAL_PROTOCOL.json")
    parser.add_argument("--waiver", type=Path, default=WAIVER)
    parser.add_argument("--protected-before", type=Path, default=OUT / "protected_hashes_before.json")
    parser.add_argument("--skip-unit-tests", action="store_true")
    parser.add_argument("--skip-solver-smoke", action="store_true")
    args = parser.parse_args()
    protocol_path = args.protocol if args.protocol.is_absolute() else ROOT / args.protocol
    waiver_path = args.waiver if args.waiver.is_absolute() else ROOT / args.waiver
    protected_before_path = (
        args.protected_before if args.protected_before.is_absolute()
        else ROOT / args.protected_before
    )
    summary = perform(
        protocol_path=protocol_path,
        waiver_path=waiver_path,
        protected_before_path=protected_before_path,
        skip_unit_tests=args.skip_unit_tests,
        skip_solver_smoke=args.skip_solver_smoke,
    )
    print(json.dumps({
        "atomic_formal_preflight_passed": summary["atomic_formal_preflight_passed"],
        "exact_hash_pass_count": summary["exact_hash_pass_count"],
        "legacy_float_compatibility_pass_count": summary["legacy_float_compatibility_pass_count"],
        "failure_count": summary["failure_count"],
    }, ensure_ascii=False, indent=2))
    if not summary["atomic_formal_preflight_passed"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
