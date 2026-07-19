"""ABMA-Outer5-v1: a single-factor five-generation wrapper for MRTA_ABMA.

The search implementation is imported from :mod:`MRTA_ABMA`.  This module
does not copy or modify the scientific solver.  It fixes only the outer SHADE
generation count to five for the ``legacy_exact_fast`` profile.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import sys
import tempfile
from contextlib import redirect_stdout
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

import MRTA_ABMA as source_abma


ROOT = Path(__file__).resolve().parents[1]
ALGORITHM_NAME = "ABMA-Outer5-v1"
SOLVER_NAME = "ABMA1"
VARIANT = "outer5_legacy_exact_fast"
SOURCE_VARIANT = "legacy_exact_fast"
OUTER_GENERATIONS = 5


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


SOURCE_ABMA_PATH = Path(source_abma.__file__).resolve()
SOURCE_ABMA_SHA256 = sha256_file(SOURCE_ABMA_PATH)


@dataclass
class ABMA1Config(source_abma.ABMAConfig):
    """The source configuration with only the outer-generation default fixed."""

    generations: int = OUTER_GENERATIONS
    max_objective_evaluations: int = 0
    early_stop_patience: int = 0
    abma_variant: str = SOURCE_VARIANT

    def validate(self) -> None:
        if self.generations != OUTER_GENERATIONS:
            raise ValueError("ABMA-Outer5-v1 requires generations=5")
        if self.max_objective_evaluations != 0:
            raise ValueError("ABMA-Outer5-v1 requires max_objective_evaluations=0")
        if self.early_stop_patience != 0:
            raise ValueError("ABMA-Outer5-v1 requires early_stop_patience=0")
        if self.abma_variant != SOURCE_VARIANT:
            raise ValueError("ABMA-Outer5-v1 requires abma_variant=legacy_exact_fast")
        super().validate()


class ABMA1Solver(source_abma.ABMASolver):
    """Identity-preserving solver subclass; all search code remains upstream."""


ABMAConfig = ABMA1Config
ABMASolver = ABMA1Solver


def wrapper_sha256() -> str:
    return sha256_file(Path(__file__).resolve())


def profile_payload(metrics: dict[str, Any]) -> dict[str, Any]:
    return {
        "algorithm_name": ALGORITHM_NAME,
        "solver_name": SOLVER_NAME,
        "variant": VARIANT,
        "source_variant": SOURCE_VARIANT,
        "single_changed_factor": {"outer_generations": {"source": 20, "variant": 5}},
        "population_size": int(metrics["population_size"]),
        "outer_generations": OUTER_GENERATIONS,
        "max_objective_evaluations": 0,
        "early_stop_patience": 0,
        "alns_iterations": int(metrics["alns_iterations"]),
        "vnd_iterations": int(metrics.get("config_vnd_iterations", 3)),
        "source_abma_file": SOURCE_ABMA_PATH.name,
        "source_abma_sha256": SOURCE_ABMA_SHA256,
        "abma1_wrapper_sha256": wrapper_sha256(),
    }


def profile_hash(metrics: dict[str, Any]) -> str:
    encoded = json.dumps(
        profile_payload(metrics), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _option_value(argv: Sequence[str], name: str) -> str | None:
    try:
        index = list(argv).index(name)
    except ValueError:
        return None
    if index + 1 >= len(argv):
        raise ValueError(f"{name} requires a value")
    return str(argv[index + 1])


def _replace_option(argv: list[str], name: str, value: str) -> None:
    while name in argv:
        index = argv.index(name)
        if index + 1 >= len(argv):
            raise ValueError(f"{name} requires a value")
        del argv[index:index + 2]
    argv.extend([name, value])


def _append_metrics_csv(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    old_rows: list[dict[str, Any]] = []
    fields: list[str] = []
    if path.is_file():
        with path.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            fields.extend(reader.fieldnames or [])
            old_rows.extend(reader)
    for key in row:
        if key not in fields:
            fields.append(key)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(old_rows)
        writer.writerow({key: json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value
                         for key, value in row.items()})


def _identity_fields(metrics: dict[str, Any]) -> dict[str, Any]:
    expected = int(metrics["population_size"]) * (1 + OUTER_GENERATIONS)
    realized = int(metrics["objective_evaluation_count"])
    fields = {
        "algorithm_name": ALGORITHM_NAME,
        "solver_name": SOLVER_NAME,
        "variant": VARIANT,
        "source_abma_variant": SOURCE_VARIANT,
        "outer_generations_requested": OUTER_GENERATIONS,
        "outer_generations_completed": int(metrics["generations_completed"]),
        "population_size": int(metrics["population_size"]),
        "expected_primary_evaluations": expected,
        "realized_primary_evaluations": realized,
        "source_abma_file": SOURCE_ABMA_PATH.name,
        "source_abma_sha256": SOURCE_ABMA_SHA256,
        "abma1_wrapper_sha256": wrapper_sha256(),
    }
    merged = {**metrics, **fields, "config_vnd_iterations": int(metrics.get("config_vnd_iterations", 3))}
    fields["abma1_profile_hash"] = profile_hash(merged)
    return fields


def _validate_controlled_arguments(argv: Sequence[str]) -> None:
    checks = {
        "--generations": (OUTER_GENERATIONS, "generations must equal 5"),
        "--max-objective-evaluations": (0, "max_objective_evaluations must equal 0"),
        "--early-stop-patience": (0, "early_stop_patience must equal 0"),
    }
    for option, (expected, message) in checks.items():
        value = _option_value(argv, option)
        if value is not None and int(value) != expected:
            raise ValueError(f"ABMA-Outer5-v1 controlled mode: {message}")
    variant = _option_value(argv, "--abma-variant")
    if variant is not None and variant != SOURCE_VARIANT:
        raise ValueError("ABMA-Outer5-v1 controlled mode requires legacy_exact_fast")


def _self_check() -> None:
    config = ABMA1Config()
    config.validate()
    assert config.population_size == 12
    assert config.generations == 5
    assert config.alns_iterations == 80
    assert config.vnd_iterations == 3
    assert config.max_objective_evaluations == 0
    assert config.early_stop_patience == 0
    assert config.population_size * (1 + config.generations) == 72
    try:
        ABMA1Config(generations=20).validate()
    except ValueError:
        pass
    else:  # pragma: no cover
        raise AssertionError("generations=20 was not rejected")
    source_abma._self_check()


def main(argv: Sequence[str] | None = None) -> None:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if "--self-check" in arguments:
        _self_check()
        print("ABMA-Outer5-v1 self-check passed")
        return
    try:
        _validate_controlled_arguments(arguments)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    requested_result = _option_value(arguments, "--result-json")
    requested_metrics = _option_value(arguments, "--metrics-csv")
    forwarded = list(arguments)
    _replace_option(forwarded, "--generations", str(OUTER_GENERATIONS))
    _replace_option(forwarded, "--max-objective-evaluations", "0")
    _replace_option(forwarded, "--early-stop-patience", "0")
    _replace_option(forwarded, "--abma-variant", SOURCE_VARIANT)

    with tempfile.TemporaryDirectory(prefix="abma1_") as folder:
        temporary = Path(folder)
        result_path = temporary / "result.json"
        metrics_path = temporary / "metrics.csv"
        _replace_option(forwarded, "--result-json", str(result_path))
        _replace_option(forwarded, "--metrics-csv", str(metrics_path))
        old_argv = sys.argv
        old_profiler = source_abma._ACTIVE_PROFILER
        captured = io.StringIO()
        try:
            sys.argv = [str(SOURCE_ABMA_PATH), *forwarded]
            with redirect_stdout(captured):
                source_abma.main()
        finally:
            sys.argv = old_argv
            source_abma._ACTIVE_PROFILER = old_profiler

        payload = json.loads(result_path.read_text(encoding="utf-8"))
        metrics = dict(payload["metrics"])
        metrics["config_vnd_iterations"] = int(payload.get("config", {}).get("vnd_iterations", 3))
        identity = _identity_fields(metrics)
        metrics.update(identity)
        payload.update(identity)
        payload["metrics"] = metrics
        payload["source_solver_identity"] = {
            "solver_name": source_abma.SOLVER_NAME,
            "variant": SOURCE_VARIANT,
            "source_abma_profile_hash": metrics.get("profile_hash"),
        }

        if requested_result:
            target = Path(requested_result)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
        if requested_metrics:
            _append_metrics_csv(Path(requested_metrics), metrics)
        print(json.dumps({key: payload[key] for key in (
            "algorithm_name", "solver_name", "variant", "outer_generations_requested",
            "outer_generations_completed", "population_size", "expected_primary_evaluations",
            "realized_primary_evaluations", "source_abma_file", "source_abma_sha256",
            "abma1_wrapper_sha256", "abma1_profile_hash",
        )}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
