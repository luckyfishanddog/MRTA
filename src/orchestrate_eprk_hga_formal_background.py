"""Run the resumable formal matrix in one persistent background process."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "formal_atomic" / "formal_execution"
STATUS = OUT / "BACKGROUND_RUNNER_STATUS.json"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_status(payload: dict[str, object]) -> None:
    STATUS.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> None:
    command = [
        sys.executable,
        "src/run_eprk_atomic_formal.py",
        "run-manifest",
        "--protocol", "formal_atomic/EPRK_ATOMIC_FORMAL_PROTOCOL.json",
        "--authorization", "formal_atomic/formal_execution/FORMAL_EXECUTION_AUTHORIZATION.json",
        "--manifest", "formal_atomic/formal_manifest.json",
        "--resume",
    ]
    started = utc_now()
    write_status({"status": "running", "started_at": started, "command": command})
    with (OUT / "formal_runner_stdout.log").open("a", encoding="utf-8") as stdout, \
            (OUT / "formal_runner_stderr.log").open("a", encoding="utf-8") as stderr:
        completed = subprocess.run(command, cwd=ROOT, stdout=stdout, stderr=stderr, text=True)
    payload = {
        "status": "passed" if completed.returncode == 0 else "failed",
        "started_at": started,
        "completed_at": utc_now(),
        "returncode": completed.returncode,
        "command": command,
    }
    write_status(payload)
    if completed.returncode:
        raise SystemExit(completed.returncode)


if __name__ == "__main__":
    main()
