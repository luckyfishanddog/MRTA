"""Publish only a successfully finalized formal comparison to a stacked draft PR."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "formal_atomic" / "formal_execution"
FINALIZATION = OUT / "FINALIZATION_SUMMARY.json"
PUBLISH = OUT / "PUBLISH_SUMMARY.json"
PR_BODY = OUT / "STACKED_PR_BODY.md"
REPOSITORY = "luckyfishanddog/MRTA"
BRANCH = "experiment/eprk-hga-atomic-formal-comparison"
BASE = "fix/atomic-formal-preflight-normalization-waiver"
ROOT_FILES = (
    ".gitignore",
    "src/analyze_eprk_hga_formal_results.py",
    "src/archive_formal_raw_results.py",
    "src/audit_formal_result_recomputation.py",
    "src/finalize_eprk_hga_formal_execution.py",
    "src/formal_execution_authorization.py",
    "src/generate_eprk_hga_formal_reports.py",
    "src/orchestrate_eprk_hga_formal_background.py",
    "src/publish_eprk_hga_formal_results.py",
    "src/run_atomic_formal_preflight.py",
    "src/run_eprk_atomic_formal.py",
    "tests/test_analyze_eprk_hga_formal_results.py",
    "tests/test_formal_execution_authorization.py",
    "EPRK-MA与HGA扩展实例正式对比及主算法资格判定阶段_AI交接报告_2026-07-17.md",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


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


def run(command: Sequence[str], *, env: dict[str, str] | None = None,
        input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(command, cwd=ROOT, env=env, input=input_text, text=True, capture_output=True)
    if completed.returncode:
        raise RuntimeError(f"command failed ({completed.returncode}): {' '.join(command)}\n{completed.stderr}")
    return completed


def wait_for_finalization() -> dict[str, Any]:
    while True:
        if FINALIZATION.is_file():
            payload = json.loads(FINALIZATION.read_text(encoding="utf-8"))
            if payload.get("status") == "passed":
                return payload
            if payload.get("status") == "failed":
                raise RuntimeError(f"finalization failed: {payload.get('error')}")
        time.sleep(60)


def gh_environment() -> dict[str, str]:
    credential = run(
        ["git", "credential", "fill"],
        input_text="protocol=https\nhost=github.com\n\n",
    ).stdout
    values = {}
    for line in credential.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
    token = values.get("password")
    if not token:
        raise RuntimeError("GitHub credential helper did not return a token")
    env = os.environ.copy()
    env["GH_TOKEN"] = token
    return env


def validate_staged_scope() -> list[str]:
    names = [line for line in run(["git", "diff", "--cached", "--name-only"]).stdout.splitlines() if line]
    if not names:
        raise RuntimeError("no formal deliverables were staged")
    allowed_exact = set(ROOT_FILES)
    for name in names:
        allowed = (
            name in allowed_exact
            or name.startswith("formal_atomic/formal_execution/")
            or name.startswith("formal_atomic/remediation/")
        )
        forbidden = (
            name.lower().endswith(".docx")
            or name.startswith("formal_atomic/runs/")
            or "formal_raw_results.zip" in name
            or name.lower().endswith(".log")
        )
        if not allowed or forbidden:
            raise RuntimeError(f"staged path outside authorized publication scope: {name}")
    return names


def main() -> None:
    finalization = wait_for_finalization()
    qualification = json.loads((OUT / "qualification_gates.json").read_text(encoding="utf-8"))
    archive = json.loads((OUT / "formal_raw_archive_index.json").read_text(encoding="utf-8"))
    PR_BODY.write_text(
        "# EPRK-MA vs Paper-Aligned-HGA-Atomic-Control formal comparison\n\n"
        "This is a stacked draft PR based on PR #2's remediation branch.\n\n"
        f"- Formal matrix: {finalization['formal_run_count']} successful runs; "
        f"{finalization['failed_attempt_count']} failed attempts\n"
        f"- Collision postprocess rows: {finalization['collision_run_count']}\n"
        f"- Unit tests: {finalization['unit_test_count']} passed\n"
        f"- Independent recomputation: {finalization['independent_recomputation_sample_count']}/60 passed\n"
        f"- Protected hashes match: {finalization['protected_hashes_match']}\n"
        f"- Charts: {finalization['chart_count']}\n"
        f"- Main-algorithm qualification: {qualification['eprk_main_algorithm_qualified']}\n\n"
        "The several-GB per-run raw directory and local ZIP parts are intentionally not committed because Git LFS "
        f"is not configured. Their inventory and SHA-256 index are committed; local archive SHA-256: "
        f"`{archive['archive_sha256']}`.\n\n"
        "The unrelated local Word document is explicitly excluded.\n",
        encoding="utf-8",
    )

    branch = run(["git", "branch", "--show-current"]).stdout.strip()
    if branch != BRANCH:
        raise RuntimeError(f"unexpected publication branch: {branch}")
    run(["git", "add", "--", *ROOT_FILES, "formal_atomic/formal_execution", "formal_atomic/remediation"])
    staged = validate_staged_scope()
    run(["git", "diff", "--cached", "--check"])
    run(["git", "commit", "-m", "Run EPRK-HGA atomic formal comparison"])
    run(["git", "push", "-u", "origin", BRANCH])

    env = gh_environment()
    existing_text = run([
        "gh", "pr", "list", "--repo", REPOSITORY, "--head", BRANCH,
        "--state", "open", "--json", "number,url",
    ], env=env).stdout
    existing = json.loads(existing_text)
    if existing:
        pr_number = existing[0]["number"]
        pr_url = existing[0]["url"]
    else:
        pr_url = run([
            "gh", "pr", "create", "--repo", REPOSITORY, "--draft",
            "--base", BASE, "--head", BRANCH,
            "--title", "EPRK-MA vs HGA atomic formal comparison",
            "--body-file", str(PR_BODY),
        ], env=env).stdout.strip()
        created = json.loads(run([
            "gh", "pr", "view", pr_url, "--repo", REPOSITORY, "--json", "number,url",
        ], env=env).stdout)
        pr_number = created["number"]
        pr_url = created["url"]

    publish_payload = {
        "status": "passed", "published_at": utc_now(), "repository": REPOSITORY,
        "branch": BRANCH, "base": BASE, "draft": True,
        "pr_number": pr_number, "pr_url": pr_url,
        "first_commit_sha": run(["git", "rev-parse", "HEAD"]).stdout.strip(),
        "staged_file_count": len(staged),
        "raw_results_uploaded": False,
        "raw_results_local_archive_sha256": archive["archive_sha256"],
    }
    atomic_json(PUBLISH, publish_payload)
    run(["git", "add", "--", str(PUBLISH.relative_to(ROOT))])
    validate_staged_scope()
    run(["git", "commit", "-m", "Record formal comparison publication"])
    run(["git", "push", "origin", BRANCH])
    result = {**publish_payload, "final_commit_sha": run(["git", "rev-parse", "HEAD"]).stdout.strip()}
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        atomic_json(PUBLISH, {
            "status": "failed", "failed_at": utc_now(), "error_type": type(exc).__name__, "error": str(exc),
        })
        raise
