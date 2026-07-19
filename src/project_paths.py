"""Central project paths for the source-layout repository."""

from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = PROJECT_ROOT / "src"
TEST_DIR = PROJECT_ROOT / "tests"
DOCS_DIR = PROJECT_ROOT / "docs"
CONFIG_DIR = PROJECT_ROOT / "config"
RESULTS_DIR = PROJECT_ROOT / "results"


def resolve_project_path(relative: str | Path) -> Path:
    """Resolve current files while accepting paths recorded before reorganization."""
    path = Path(relative)
    if path.is_absolute():
        return path
    direct = PROJECT_ROOT / path
    if direct.exists():
        return direct
    if len(path.parts) == 1 and path.suffix == ".py":
        for directory in (SOURCE_DIR, TEST_DIR):
            candidate = directory / path.name
            if candidate.exists():
                return candidate
    if len(path.parts) == 1 and path.suffix == ".md":
        matches = list(DOCS_DIR.rglob(path.name))
        if len(matches) == 1:
            return matches[0]
    if len(path.parts) == 1 and path.suffix == ".docx":
        candidate = DOCS_DIR / "thesis" / path.name
        if candidate.exists():
            return candidate
    if len(path.parts) == 1:
        for directory in (CONFIG_DIR, RESULTS_DIR):
            if directory.exists():
                matches = list(directory.rglob(path.name))
                if len(matches) == 1:
                    return matches[0]
    return direct
