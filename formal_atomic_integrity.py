"""Integrity snapshots for the frozen atomic formal-evaluation stage."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent
OUT = ROOT / "formal_atomic"

FIXED_PATHS = [
    "MRTA_ABMA.py",
    "MRTA_HGA_PAPER_ALIGNED_CONTROL.py",
    "MRTA_GA_ACO.py",
    "mrta_problem_core.py",
    "objective_normalization.py",
    "collision_aware_schedule.py",
    "abma_final_profile.json",
    "abma_final_profile.sha256",
    "UNIFIED_EXPERIMENT_PROTOCOL.json",
    "formal_experiment_manifest.json",
    "data/instances/selected/instance_w30.xlsx",
    "data/instances/selected/instance_w45.xlsx",
    "data/instances/selected/instance_w60.xlsx",
    "data/instances/atomic_l5_l1/instance_w30_atomic.json",
    "data/instances/atomic_l5_l1/instance_w45_atomic.json",
    "data/instances/atomic_l5_l1/instance_w60_atomic.json",
    # The following become frozen scientific/algorithm evidence in this stage.
    "MRTA_EPRK_MA.py",
    "MRTA_HGA_ATOMIC_CONTROL.py",
    "atomic_problem_core.py",
    "atomic_weld_preprocessor.py",
    "atomic_route_evaluator.py",
    "build_atomic_normalization.py",
    "eprk_ma_candidate_profile.json",
    "eprk_ma_candidate_profile.sha256",
    "atomic_normalization_spec.json",
    "atomic_normalization_spec.sha256",
    "atomic_normalization_baselines.csv",
]

RESULT_GLOBS = [
    "eprk_*.csv",
    "tiny_atomic_exact_results.json",
    "eprk_development_outputs/**/*.json",
]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def commit_sha() -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()


def protected_paths() -> list[Path]:
    paths = {ROOT / relative for relative in FIXED_PATHS}
    for pattern in RESULT_GLOBS:
        paths.update(path for path in ROOT.glob(pattern) if path.is_file())
    missing = [path for path in paths if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"protected files missing: {missing}")
    return sorted(paths, key=lambda path: path.relative_to(ROOT).as_posix())


def snapshot(label: str) -> dict[str, object]:
    files = {
        path.relative_to(ROOT).as_posix(): sha256_file(path)
        for path in protected_paths()
    }
    payload: dict[str, object] = {
        "schema_version": "1.0.0",
        "snapshot": label,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_repository_commit_sha": commit_sha(),
        "protected_file_count": len(files),
        "files": files,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    target = OUT / f"protected_hashes_{label}.json"
    target.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return payload


def verify() -> None:
    before_path = OUT / "protected_hashes_before.json"
    after_path = OUT / "protected_hashes_after.json"
    before = json.loads(before_path.read_text(encoding="utf-8"))
    after = json.loads(after_path.read_text(encoding="utf-8"))
    if before["files"] != after["files"]:
        changed = sorted(
            key
            for key in set(before["files"]) | set(after["files"])
            if before["files"].get(key) != after["files"].get(key)
        )
        raise RuntimeError(f"protected files changed: {changed}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("before", "after", "verify"))
    args = parser.parse_args()
    if args.command == "verify":
        verify()
        print("protected hashes match")
    else:
        payload = snapshot(args.command)
        print(json.dumps({
            "snapshot": args.command,
            "protected_file_count": payload["protected_file_count"],
            "source_repository_commit_sha": payload["source_repository_commit_sha"],
        }, ensure_ascii=False))


if __name__ == "__main__":
    main()
