"""Test bootstrap for the source-layout project."""

from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = PROJECT_ROOT / "src"

for path in (str(SOURCE_DIR), str(PROJECT_ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)
