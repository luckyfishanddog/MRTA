"""Fail-closed finalization for the authorized 4680-run formal experiment."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "formal_atomic" / "formal_execution"
STATE = OUT / "execution_checkpoint.json"
LOGS = OUT / "finalization_logs"
SUMMARY = OUT / "FINALIZATION_SUMMARY.json"
EXPECTED_RUNS = 4680
MAX_STALE_SECONDS = 15 * 60


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_json(path: Path, value: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    for attempt in range(50):
        try:
            temporary.replace(path)
            break
        except PermissionError:
            if attempt == 49:
                raise
            time.sleep(0.1)


def run_step(name: str, command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    print(f"[{utc_now()}] START {name}", flush=True)
    completed = subprocess.run(command, cwd=ROOT, text=True, capture_output=True)
    (LOGS / f"{name}.stdout.log").write_text(completed.stdout, encoding="utf-8")
    (LOGS / f"{name}.stderr.log").write_text(completed.stderr, encoding="utf-8")
    if completed.returncode:
        raise RuntimeError(f"{name} failed with return code {completed.returncode}")
    print(f"[{utc_now()}] PASS {name}", flush=True)
    return completed


def wait_for_matrix() -> dict[str, Any]:
    last_reported = -1
    while True:
        if not STATE.is_file():
            raise RuntimeError("execution checkpoint is missing")
        state = load_json(STATE)
        success = len(state.get("success_run_ids", []))
        failed = len(state.get("failed_run_ids", []))
        if success != last_reported:
            print(f"[{utc_now()}] matrix progress {success}/{EXPECTED_RUNS}; failed={failed}", flush=True)
            last_reported = success
        if failed:
            raise RuntimeError(f"formal matrix contains {failed} failed run ids")
        if state.get("complete"):
            if success != EXPECTED_RUNS:
                raise RuntimeError(f"complete checkpoint has {success}, expected {EXPECTED_RUNS}")
            return state
        updated = datetime.fromisoformat(state["updated_at"])
        age = (datetime.now(timezone.utc) - updated).total_seconds()
        if age > MAX_STALE_SECONDS:
            raise RuntimeError(f"formal runner checkpoint stale for {age:.1f} seconds")
        time.sleep(60)


def csv_count(name: str) -> int:
    with (OUT / name).open(encoding="utf-8-sig", newline="") as handle:
        return sum(1 for _ in csv.DictReader(handle))


def write_inventory() -> list[dict[str, Any]]:
    excluded = {"formal_raw_results.zip"}
    rows = []
    for path in sorted(OUT.rglob("*")):
        if not path.is_file() or path.name in excluded or path.name.startswith("formal_raw_results.zip.part"):
            continue
        rows.append({
            "path": path.relative_to(ROOT).as_posix(),
            "size_bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        })
    target = OUT / "artifact_inventory.csv"
    with target.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=("path", "size_bytes", "sha256"))
        writer.writeheader()
        writer.writerows(rows)
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wait", action="store_true", help="wait for the formal runner to complete")
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    LOGS.mkdir(parents=True, exist_ok=True)
    started = utc_now()
    try:
        state = wait_for_matrix() if args.wait else load_json(STATE)
        if not state.get("complete") or len(state.get("success_run_ids", [])) != EXPECTED_RUNS:
            raise RuntimeError("formal matrix is not complete")

        run_step("01_rebuild", [sys.executable, "src/run_eprk_atomic_formal.py", "rebuild"])
        raw_count = csv_count("raw_runs.csv")
        collision_count = csv_count("collision_runs.csv")
        failure_count = csv_count("run_failures.csv")
        if (raw_count, collision_count, failure_count) != (EXPECTED_RUNS, EXPECTED_RUNS, 0):
            raise RuntimeError(
                f"rebuilt table counts are raw={raw_count}, collision={collision_count}, failures={failure_count}"
            )

        run_step("02_analysis", [sys.executable, "src/analyze_eprk_hga_formal_results.py"])
        run_step("03_recomputation", [sys.executable, "src/audit_formal_result_recomputation.py"])
        run_step("04_archive", [sys.executable, "src/archive_formal_raw_results.py"])
        run_step("05_integrity_after", [sys.executable, "src/formal_atomic_integrity.py", "after",
                                         "--output-dir", "formal_atomic/formal_execution"])
        run_step("06_integrity_verify", [sys.executable, "src/formal_atomic_integrity.py", "verify",
                                          "--output-dir", "formal_atomic/formal_execution"])
        tests = run_step("07_unit_tests", [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"])
        preflight = run_step("08_preflight", [
            sys.executable, "src/run_atomic_formal_preflight.py",
            "--protocol", "formal_atomic/EPRK_ATOMIC_FORMAL_PROTOCOL.json",
            "--waiver", "formal_atomic/remediation/LEGACY_NORMALIZATION_FLOAT_COMPATIBILITY_WAIVER.json",
            "--protected-before", "formal_atomic/formal_execution/protected_hashes_before.json",
        ])
        run_step("09_reports", [sys.executable, "src/generate_eprk_hga_formal_reports.py"])
        run_step("10_diff_check", ["git", "diff", "--check"])

        test_text = tests.stdout + "\n" + tests.stderr
        match = re.search(r"Ran (\d+) tests", test_text)
        test_count = int(match.group(1)) if match else None
        preflight_payload = json.loads(preflight.stdout)
        qualification = load_json(OUT / "qualification_gates.json")
        recomputation = load_json(OUT / "independent_recomputation_audit.json")
        before = load_json(OUT / "protected_hashes_before.json")
        after = load_json(OUT / "protected_hashes_after.json")
        pngs = sorted(path.name for path in OUT.glob("*.png"))
        if len(pngs) != 18:
            raise RuntimeError(f"expected 18 charts, found {len(pngs)}")
        inventory = write_inventory()
        payload = {
            "status": "passed",
            "started_at": started,
            "completed_at": utc_now(),
            "formal_run_count": raw_count,
            "collision_run_count": collision_count,
            "failed_attempt_count": failure_count,
            "unit_test_count": test_count,
            "preflight": preflight_payload,
            "protected_hashes_match": before["files"] == after["files"],
            "independent_recomputation_passed": recomputation["passed"],
            "independent_recomputation_sample_count": recomputation["sample_run_count"],
            "chart_count": len(pngs),
            "charts": pngs,
            "artifact_inventory_entry_count": len(inventory),
            "eprk_main_algorithm_qualified": qualification["eprk_main_algorithm_qualified"],
        }
        atomic_json(SUMMARY, payload)
        print(json.dumps(payload, ensure_ascii=False, indent=2), flush=True)
    except Exception as exc:
        atomic_json(SUMMARY, {
            "status": "failed", "started_at": started, "failed_at": utc_now(),
            "error_type": type(exc).__name__, "error": str(exc),
        })
        raise


if __name__ == "__main__":
    main()
