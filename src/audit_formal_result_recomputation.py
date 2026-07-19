"""Independently recompute a frozen, cross-family/size/seed formal sample."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from prepare_eprk_atomic_formal import sha256_file
from run_eprk_atomic_formal import cross_validate, load_json


ROOT = Path(__file__).resolve().parents[1]
FORMAL = ROOT / "formal_atomic"
OUT = FORMAL / "formal_execution"
MANIFEST = FORMAL / "formal_manifest.json"
INSTANCES = ("boundary_dense_n30_r1", "long_weld_rich_n60_r1", "load_skewed_n100_r1")
SEEDS = (201, 207, 213, 219, 230)
MODES = ("equal_primary", "equal_time")
SOLVERS = ("eprk", "hga_atomic")


def main() -> dict[str, Any]:
    manifest = load_json(MANIFEST)
    wanted = {
        (instance, mode, seed, solver)
        for instance in INSTANCES for mode in MODES for seed in SEEDS for solver in SOLVERS
    }
    entries = {
        (row["instance_id"], row["mode"], int(row["solver_seed"]), row["solver"]): row
        for row in manifest["entries"] if (row["instance_id"], row["mode"], int(row["solver_seed"]), row["solver"]) in wanted
    }
    if set(entries) != wanted:
        raise RuntimeError("independent recomputation sample is incomplete in manifest")
    rows = []
    for key in sorted(wanted):
        entry = entries[key]
        base = ROOT / entry["expected_output_path"]
        success = None
        for attempt in sorted(base.glob("attempt_*")):
            result_path = attempt / "result.json"
            companion = attempt / "result.json.sha256"
            collision_path = attempt / "collision_postprocess.json"
            if result_path.is_file() and companion.is_file() and collision_path.is_file():
                value = load_json(result_path)
                if value.get("status") == "success" and value.get("resume_key") == entry["resume_key"]:
                    success = (attempt, result_path, companion, collision_path, value)
                    break
        if success is None:
            raise RuntimeError(f"sample result missing: {entry['run_id']}")
        attempt, result_path, companion, collision_path, value = success
        metrics = value["metrics"]
        recomputed, errors = cross_validate(entry["instance_id"], metrics)
        collision = load_json(collision_path)
        hash_ok = companion.read_text(encoding="ascii").strip() == sha256_file(result_path)
        row = {
            "run_id": entry["run_id"], "family": entry["family"],
            "nominal_weld_count": entry["nominal_weld_count"], "instance_id": entry["instance_id"],
            "mode": entry["mode"], "solver": entry["solver"], "seed": entry["solver_seed"],
            "result_hash_match": hash_ok, "independent_cross_validation_passed": recomputed,
            "cross_validation_errors_json": json.dumps(errors, ensure_ascii=False),
            "raw_result_unchanged": collision.get("raw_result_unchanged") is True,
            "unresolved_conflict_count": collision.get("unresolved_conflict_count"),
            "result_path": result_path.relative_to(ROOT).as_posix(),
        }
        rows.append(row)
    passed = (
        len(rows) == 60
        and all(row["result_hash_match"] and row["independent_cross_validation_passed"]
                and row["raw_result_unchanged"] for row in rows)
    )
    csv_path = OUT / "independent_recomputation_audit.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader(); writer.writerows(rows)
    payload = {
        "passed": passed, "sample_run_count": len(rows), "families": 3, "sizes": [30, 60, 100],
        "seeds": list(SEEDS), "solvers": list(SOLVERS), "modes": list(MODES), "rows": rows,
    }
    (OUT / "independent_recomputation_audit.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps({"passed": passed, "sample_run_count": len(rows)}, ensure_ascii=False))
    if not passed:
        raise SystemExit(2)
    return payload


if __name__ == "__main__":
    main()
