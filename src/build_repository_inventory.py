"""Generate conservative, reproducible repository cleanup inventories."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path

from project_paths import resolve_project_path


ROOT = Path(__file__).resolve().parents[1]
CURRENT_AUTHORITATIVE = {
    "ABMA结构性等价加速与开发门禁复验阶段_AI交接报告_2026-07-15.md",
    "ABMA_STRUCTURAL_ACCELERATION_REPORT.md",
}
CURRENT_SOURCE = {
    "MRTA_ABMA.py", "MRTA_GA_ACO.py", "MRTA_HGA_PAPER_ALIGNED_CONTROL.py",
    "run_unified_experiments.py", "mrta_problem_core.py",
    "objective_normalization.py", "generate_welds.py",
    "solver_output_metrics.py", "collision_aware_schedule.py",
    "build_repository_inventory.py",
}
CURRENT_PROFILE = {"abma_final_profile.json", "abma_final_profile.sha256"}
CURRENT_EVIDENCE_PREFIXES = (
    "abma_profile_", "abma_exact_", "abma_candidate_",
    "unified_experiments/abma_structural_acceleration_",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def classify(relative: str, size: int, protocol_refs: set[str]) -> tuple[str, str, str]:
    name = Path(relative).name
    suffix = Path(relative).suffix.lower()
    lower = relative.lower()
    if relative == "UNIFIED_EXPERIMENT_PROTOCOL.json":
        return "current_protocol", "retain", "current protocol and active source evidence"
    if name in CURRENT_PROFILE:
        return "current_profile", "retain", "frozen ABMA profile or checksum"
    if name in CURRENT_AUTHORITATIVE:
        return "current_authoritative", "retain", "current authoritative stage report"
    if name in CURRENT_SOURCE:
        return "current_source", "retain", "active source module"
    if name.startswith("test_") and suffix == ".py":
        return "current_test", "retain", "active regression test"
    if relative in protocol_refs:
        return "current_evidence", "retain", "referenced by current protocol source evidence"
    if relative.startswith("data/instances/selected/"):
        return "current_evidence", "retain", "frozen selected instance"
    if any(relative.startswith(prefix) for prefix in CURRENT_EVIDENCE_PREFIXES):
        return "current_evidence", "retain", "exact-fast profiling/development evidence"
    if name == "双滑轨四悬臂机器人任务分配及焊缝排序.docx":
        return "current_authoritative", "retain", "current Word thesis"
    if name in {"MRTA_DE_LKH.py", "route_local_search.py"}:
        return "historical_excluded_solver", "retain_historical", "DE+LKH is excluded but historical source is retained"
    if lower.startswith("unified_experiments/"):
        return "historical_result", "retain_or_archive_in_place", "experiment paths are embedded in immutable evidence"
    if lower.startswith(("normalization_smoke_", "output_schema_smoke_", "abma_budget_control_smoke/")):
        return "historical_result", "retain_or_archive_in_place", "historical smoke evidence may contain recorded paths"
    if "__pycache__" in lower or suffix == ".pyc":
        return "cache_file", "delete", "regenerable Python bytecode cache"
    if suffix in {".tmp", ".swp", ".swo"} or name.endswith("~"):
        return "temporary_file", "delete", "temporary/editor file"
    if size == 0:
        return "temporary_file", "delete_if_unreferenced", "empty file"
    if suffix == ".md":
        return "historical_report", "retain_historical", "stage/report history retained for traceability"
    if suffix in {".docx", ".pdf", ".xlsx"}:
        return "unknown_requires_review", "retain", "binary document/data requires explicit review"
    if name.startswith(("MRTA_ABMA(", "UNIFIED_EXPERIMENT_PROTOCOL(", "run_unified_experiments(")):
        return "obsolete_generated_output", "compare_then_archive_or_delete", "numbered active-file copy"
    return "orphan_file", "retain_pending_review", "not proven safe to delete"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    protocol = json.loads(resolve_project_path("UNIFIED_EXPERIMENT_PROTOCOL.json").read_text(encoding="utf-8"))
    protocol_refs = set(protocol.get("source_evidence_files", []))
    files = sorted(
        path for path in ROOT.rglob("*")
        if path.is_file() and ".git" not in path.relative_to(ROOT).parts
        and path.name != args.output
    )
    text_sources: dict[str, str] = {}
    for path in files:
        if path.suffix.lower() in {".py", ".json", ".md", ".txt"} and path.stat().st_size <= 5_000_000:
            try:
                text_sources[path.relative_to(ROOT).as_posix()] = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                pass
    hashes: dict[str, list[str]] = defaultdict(list)
    records = []
    for path in files:
        relative = path.relative_to(ROOT).as_posix()
        size = path.stat().st_size
        digest = sha256(path)
        hashes[digest].append(relative)
        category, action, reason = classify(relative, size, protocol_refs)
        basename = path.name
        referenced_by = sorted(
            source for source, content in text_sources.items()
            if source != relative and (relative in content or basename in content)
        )
        records.append({
            "path": relative, "size": size, "sha256": digest,
            "category": category, "referenced_by": referenced_by,
            "planned_action": action, "reason": reason,
        })
    duplicate_groups = [paths for paths in hashes.values() if len(paths) > 1]
    payload = {
        "schema_version": "1.0.0",
        "root": str(ROOT),
        "file_count": len(records),
        "total_bytes": sum(item["size"] for item in records),
        "duplicate_sha256_groups": duplicate_groups,
        "files": records,
    }
    (ROOT / args.output).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
