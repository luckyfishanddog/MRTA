"""Create a local compressed archive, split parts and SHA-256 index for raw results."""

from __future__ import annotations

import csv
import hashlib
import json
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNS = ROOT / "formal_atomic" / "runs"
OUT = ROOT / "formal_atomic" / "formal_execution"
ARCHIVE = OUT / "formal_raw_results.zip"
PART_SIZE = 50 * 1024 * 1024


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> None:
    files = sorted(path for path in RUNS.rglob("*") if path.is_file())
    if not files:
        raise RuntimeError("no formal raw results found")
    total_size = sum(path.stat().st_size for path in files)
    largest = max(files, key=lambda path: path.stat().st_size)
    with zipfile.ZipFile(ARCHIVE, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in files:
            archive.write(path, path.relative_to(ROOT).as_posix())
    parts = []
    with ARCHIVE.open("rb") as source:
        index = 1
        while True:
            block = source.read(PART_SIZE)
            if not block:
                break
            part = OUT / f"formal_raw_results.zip.part{index:03d}"
            part.write_bytes(block)
            parts.append({"part": part.name, "size_bytes": part.stat().st_size, "sha256": sha256_file(part)})
            index += 1
    rows = [{"path": path.relative_to(ROOT).as_posix(), "size_bytes": path.stat().st_size,
             "sha256": sha256_file(path)} for path in files]
    with (OUT / "formal_raw_file_hash_index.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)
    payload = {
        "raw_file_count": len(files), "raw_total_size_bytes": total_size,
        "largest_file": largest.relative_to(ROOT).as_posix(), "largest_file_size_bytes": largest.stat().st_size,
        "archive_path": ARCHIVE.relative_to(ROOT).as_posix(), "archive_size_bytes": ARCHIVE.stat().st_size,
        "archive_sha256": sha256_file(ARCHIVE), "part_size_bytes": PART_SIZE, "parts": parts,
        "git_lfs_configured": (ROOT / ".gitattributes").is_file() and "filter=lfs" in (ROOT / ".gitattributes").read_text(encoding="utf-8", errors="ignore"),
        "raw_results_uploaded": False,
    }
    (OUT / "formal_raw_archive_index.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
