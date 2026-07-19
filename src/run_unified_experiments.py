#!/usr/bin/env python3
"""Auditable three-solver experiment runner for the frozen MRTA instances.

The JSON protocol is the only source of scientific settings.  Solver adapters
translate those settings to backward-compatible CLIs; raw solver fields are
preserved and a validated unified field layer is written beside every run.
Formal execution is deliberately fail-closed.
"""

from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import importlib.metadata
import itertools
import json
import math
import os
import platform
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from generate_welds import load_frozen_weld_instance
from mrta_problem_core import (
    DirectedWeld,
    MotionModel,
    assign_welds_to_robots_split,
    compute_directed_route_stats,
)
from solver_output_metrics import REGION_NAMES
from objective_normalization import (
    LEGACY_MODE, OFFICIAL_MODE, compute_normalization_spec,
    normalized_components, normalized_objective, normalization_spec_hash,
    validate_normalization_spec,
)
from project_paths import resolve_project_path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROTOCOL = resolve_project_path("UNIFIED_EXPERIMENT_PROTOCOL.json")
UNIFIED_REQUIRED = (
    "protocol_version", "protocol_hash", "run_id", "run_class", "solver_name",
    "solver_seed", "instance_path", "instance_seed", "instance_hash",
    "actual_weld_count", "fitness", "makespan", "load_imbalance",
    "total_idle_distance", "total_weld_time", "total_travel_time", "x_up",
    "x_low", "weight_makespan", "weight_load", "weight_distance",
    "normalization_mode", "normalized_makespan", "normalized_load_imbalance",
    "normalized_idle_distance", "weld_speed",
    "travel_speed", "acceleration", "safe_z", "time_model",
    "assignment_mode", "split_timing_mode", "direction_mode",
    "objective_evaluation_count", "primary_budget_type",
    "primary_budget_value", "algorithm_time", "wall_clock_time", "return_code",
    "solver_wall_clock_time", "postprocess_time", "total_run_wall_clock_time",
    "robot_metrics", "sum_robot_total_time", "requested_objective_evaluations",
    "realized_objective_evaluations", "stop_reason", "run_status",
    "unassigned_subweld_count",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(text, encoding="utf-8")
    os.replace(temp, path)


def atomic_write_json(path: Path, value: Any) -> None:
    atomic_write_text(path, json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n")


def read_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def relative_to_root(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(path.resolve())


def to_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def to_int(value: Any) -> Optional[int]:
    number = to_float(value)
    return None if number is None else int(number)


def to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def close(a: Any, b: Any, tolerance: float = 1e-9) -> bool:
    left, right = to_float(a), to_float(b)
    if left is None or right is None:
        return False
    return math.isclose(left, right, rel_tol=1e-10, abs_tol=tolerance)


def read_last_csv_row(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"missing metrics CSV: {path}")
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    if len(rows) != 1:
        raise ValueError(f"per-run metrics CSV must contain exactly one row, got {len(rows)}: {path}")
    return dict(rows[0])


def protocol_hash(protocol_path: Path) -> str:
    return sha256_file(protocol_path)


def environment_evidence(python_executable: str) -> Dict[str, Any]:
    packages: Dict[str, Optional[str]] = {}
    for name in ("numpy", "pandas", "matplotlib", "openpyxl"):
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = None
    return {
        "recorded_at": utc_now(),
        "python_executable": str(Path(python_executable).resolve()),
        "runner_python_executable": sys.executable,
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "working_directory": str(ROOT),
        "packages": packages,
    }


def source_hashes(protocol: Mapping[str, Any]) -> Dict[str, str]:
    result: Dict[str, str] = {}
    for relative in protocol["source_evidence_files"]:
        path = resolve_project_path(relative)
        if not path.is_file():
            raise FileNotFoundError(f"source evidence file missing: {relative}")
        result[relative] = sha256_file(path)
    return result


def validate_frozen_abma_profile(protocol: Mapping[str, Any]) -> List[str]:
    """Validate the immutable scientific ABMA profile and its active registration.

    The profile's runner/test hashes are provenance for the development freeze.
    Protocol 2.6 is allowed to evolve orchestration and tests, but the three
    scientific implementation hashes remain strict.
    """
    errors: List[str] = []
    freeze = protocol.get("abma_candidate_freeze", {})
    official = protocol.get("official_abma_profile", {})
    expected_hash = "631b2d675e7531d04a3c019cc8013158223944074da3e42d489a5745addee1d3"
    profile_path = resolve_project_path(str(freeze.get("profile_path", "abma_final_profile.json")))
    checksum_path = resolve_project_path("abma_final_profile.sha256")
    if not profile_path.is_file():
        return [f"frozen ABMA profile missing: {relative_to_root(profile_path)}"]
    actual_hash = sha256_file(profile_path)
    if actual_hash != expected_hash:
        errors.append(f"frozen ABMA profile hash mismatch: {actual_hash}")
    if freeze.get("profile_hash") != expected_hash or official.get("profile_hash") != expected_hash:
        errors.append("protocol frozen ABMA profile hash must equal the fixed approved hash")
    if not checksum_path.is_file() or checksum_path.read_text(encoding="utf-8").split()[0] != expected_hash:
        errors.append("abma_final_profile.sha256 does not declare the fixed approved hash")
    try:
        profile = read_json(profile_path)
    except Exception as exc:
        return errors + [f"cannot parse frozen ABMA profile: {exc}"]
    expected_identity = {
        "algorithm_name": "ABMA-Legacy-Exact-Fast-v1",
        "variant": "legacy_exact_fast",
    }
    for key, value in expected_identity.items():
        if profile.get(key) != value or freeze.get(key) != value or official.get(key) != value:
            errors.append(f"frozen ABMA {key} must equal {value!r}")
    semantics = profile.get("search_semantics", {})
    if semantics.get("outer_algorithm") != "legacy_SHADE_exact":
        errors.append("frozen ABMA outer algorithm must remain legacy_SHADE_exact")
    if semantics.get("alns_iterations_per_partition") != 80 or semantics.get("vnd_iterations_per_partition") != 3:
        errors.append("frozen ABMA inner budget must remain ALNS=80 and VND=3")
    controls = protocol.get("budget", {}).get("formal_solver_controls", {}).get("abma", {})
    if (controls.get("abma_variant"), controls.get("alns_iterations"), controls.get("vnd_iterations")) != (
        "legacy_exact_fast", 80, 3
    ):
        errors.append("official ABMA controls must use legacy_exact_fast with ALNS=80 and VND=3")
    strict_sources = ("MRTA_ABMA.py", "mrta_problem_core.py", "objective_normalization.py")
    recorded_hashes = profile.get("source_hashes", {})
    for relative in strict_sources:
        path = resolve_project_path(relative)
        if not path.is_file() or recorded_hashes.get(relative) != sha256_file(path):
            errors.append(f"frozen scientific source hash mismatch: {relative}")
    if set(ADAPTERS) != {"ga_aco", "paper_aligned_hga", "abma"}:
        errors.append("active adapter registry must contain exactly the three official solvers")
    return errors


@dataclass(frozen=True)
class RunSpec:
    run_class: str
    solver: str
    instance_key: str
    instance: Mapping[str, Any]
    seed: int
    budget_value: int
    controls: Mapping[str, Any]
    variant: str = ""
    variant_modes: Sequence[str] = field(default_factory=tuple)

    @property
    def logical_id(self) -> str:
        parts = [self.run_class, self.instance_key, self.solver, f"seed_{self.seed}"]
        if self.variant:
            parts.insert(2, self.variant)
        return "__".join(parts)


class SolverAdapter:
    key = ""
    script = ""
    solver_name = ""

    def scientific_args(self, protocol: Mapping[str, Any], spec: RunSpec) -> List[str]:
        model = protocol["scientific_model"]
        normalization = spec.instance["normalization"]
        weights = model["weights"]
        ideal, baseline, scale = normalization["ideal"], normalization["baseline"]["metrics"], normalization["scale"]
        return [
            "--solver-seed", str(spec.seed),
            "--weight-makespan", str(weights[0]),
            "--weight-load", str(weights[1]),
            "--weight-distance", str(weights[2]),
            "--weld-speed", str(model["weld_speed"]),
            "--travel-speed", str(model["travel_speed"]),
            "--acceleration", str(model["acceleration"]),
            "--safe-z", str(model["safe_z"]),
            "--normalization-mode", OFFICIAL_MODE,
            "--ideal-makespan", repr(float(ideal["makespan"])),
            "--ideal-load-imbalance", repr(float(ideal["load_imbalance"])),
            "--ideal-distance", repr(float(ideal["idle_distance"])),
            "--baseline-makespan", repr(float(baseline["makespan"])),
            "--baseline-load-imbalance", repr(float(baseline["load_imbalance"])),
            "--baseline-distance", repr(float(baseline["idle_distance"])),
            "--scale-makespan", repr(float(scale["makespan"])),
            "--scale-load-imbalance", repr(float(scale["load_imbalance"])),
            "--scale-distance", repr(float(scale["idle_distance"])),
        ]

    def build(self, protocol: Mapping[str, Any], spec: RunSpec, python_executable: str, run_dir: Path) -> List[str]:
        raise NotImplementedError


class GAACOAdapter(SolverAdapter):
    key = "ga_aco"
    script = "MRTA_GA_ACO.py"
    solver_name = "GA+ACO"

    def build(self, protocol, spec, python_executable, run_dir):
        controls = spec.controls
        if spec.variant_modes:
            time_model, assignment_mode, split_mode, direction_mode = spec.variant_modes
        else:
            model = protocol["scientific_model"]
            time_model = model["time_model"]
            assignment_mode = model["assignment_mode"]
            split_mode = model["split_timing_mode"]
            direction_mode = model["direction_mode"]
        command = [
            python_executable, relative_to_root(resolve_project_path(self.script)),
            "--instance-path", spec.instance["path"],
            "--weld-count", str(spec.instance["weld_count"]),
            "--pop-size", str(controls["pop_size"]),
            "--max-gen", str(controls["max_gen"]),
            "--max-objective-evaluations", str(controls.get("max_objective_evaluations", spec.budget_value)),
            "--time-model", time_model,
            "--assignment-mode", assignment_mode,
            "--split-timing-mode", split_mode,
            "--direction-mode", direction_mode,
            "--metrics-csv", relative_to_root(run_dir / "metrics.csv"),
            "--result-json", relative_to_root(run_dir / "result.json"),
            "--output-image", relative_to_root(run_dir / "solution.png"),
        ]
        if spec.variant:
            command.extend(["--solver-seed", str(spec.seed)])
        else:
            command.extend(self.scientific_args(protocol, spec))
        return command


class HGAAdapter(SolverAdapter):
    key = "paper_aligned_hga"
    script = "MRTA_HGA_PAPER_ALIGNED_CONTROL.py"
    solver_name = "Paper-Aligned-HGA-XCut-Control"

    def build(self, protocol, spec, python_executable, run_dir):
        c = spec.controls
        command = [
            python_executable, relative_to_root(resolve_project_path(self.script)),
            "--config-path", "config/profiles/paper_aligned_hga_control_defaults.json",
            "--instance-path", spec.instance["path"],
            "--mu", str(c["mu"]), "--lambda", str(c["lambda"]),
            "--alpha-neighbors", str(c.get("alpha_neighbors", 20)),
            "--max-generations", str(c.get("max_generations", 0)),
            "--max-offspring", str(c["max_offspring"]),
            "--max-local-search-moves", str(c["max_local_search_moves"]),
            "--max-neighbor-checks", str(c["max_neighbor_checks"]),
            "--no-improve-limit", str(c.get("no_improve_limit", 0)),
            "--time-limit-s", str(c.get("time_limit_s", 0.0)),
            "--vnd-mode", c["vnd_mode"],
            "--max-objective-evaluations", str(c.get("max_objective_evaluations", spec.budget_value)),
            "--boundary-mutation-rate", "0.1", "--route-mutation-rate", "0",
            "--disable-project-fallback",
            "--metrics-json", relative_to_root(run_dir / "result.json"),
            "--metrics-csv", relative_to_root(run_dir / "metrics.csv"),
        ]
        command.extend(self.scientific_args(protocol, spec))
        return command


class ABMAAdapter(SolverAdapter):
    key = "abma"
    script = "MRTA_ABMA.py"
    solver_name = "ABMA"

    def build(self, protocol, spec, python_executable, run_dir):
        c = spec.controls
        model = protocol["scientific_model"]
        weights = model["weights"]
        command = [
            python_executable, relative_to_root(resolve_project_path(self.script)),
            "--instance-path", spec.instance["path"],
            "--weld-count", str(spec.instance["weld_count"]),
            "--population-size", str(c["population_size"]),
            "--generations", str(c["generations"]),
            "--alns-iterations", str(c["alns_iterations"]),
            "--vnd-iterations", str(c["vnd_iterations"]),
            "--early-stop-patience", str(c.get("early_stop_patience", 0)),
            "--route-time-limit-s", str(c.get("route_time_limit_s", 0.0)),
            "--max-objective-evaluations", str(c.get("max_objective_evaluations", spec.budget_value)),
            "--metrics-csv", relative_to_root(run_dir / "metrics.csv"),
            "--result-json", relative_to_root(run_dir / "result.json"),
            "--quiet",
        ]
        if c.get("abma_variant"):
            variant = str(c["abma_variant"])
            command.extend(["--abma-variant", variant])
        else:
            variant = ""
        if variant in protocol.get("historical_rejected_abma_variants", {}).get("variants", {}):
            historical = protocol["historical_rejected_abma_variants"]
            policy = historical["fidelity_policy"]
            screening = historical["screening_policy"]
            for level in ("low", "mid", "high"):
                command.extend([f"--{level}-alns-iterations", str(policy[level]["alns_iterations"])])
                command.extend([f"--{level}-vnd-iterations", str(policy[level]["vnd_iterations"])])
            command.extend([
                "--rejected-audit-interval", str(screening["rejected_audit_interval"]),
                "--audit-quantile", str(screening["quantile"]),
                "--minimum-audit-samples", str(screening["minimum_audit_samples"]),
                "--near-target-margin", str(screening["near_target_margin"]),
                "--small-boundary-delta", str(screening["small_boundary_delta"]),
                "--large-boundary-delta", str(screening["large_boundary_delta"]),
                "--high-preserved-task-ratio", str(screening["high_preserved_task_ratio"]),
            "--high-affected-task-ratio", str(screening["high_affected_task_ratio"]),
            "--low-population-diversity", str(screening["low_population_diversity"]),
            ])
        if spec.run_class == "abma_profile":
            command.extend([
                "--profile-json", relative_to_root(run_dir / "abma_internal_profile.json"),
                "--cprofile-output", relative_to_root(run_dir / "abma_profile.pstats"),
            ])
        command.extend(self.scientific_args(protocol, spec))
        return command


ADAPTERS: Dict[str, SolverAdapter] = {
    adapter.key: adapter for adapter in (GAACOAdapter(), HGAAdapter(), ABMAAdapter())
}


def validate_protocol(protocol: Mapping[str, Any], protocol_path: Path, output_root: Path) -> List[str]:
    errors: List[str] = []
    required = (
        "protocol_name", "protocol_version", "status", "formal_run_approved",
        "created_at", "project_model", "official_solvers", "rationale",
        "known_limitations", "instances", "scientific_model", "solver_seeds",
        "budget", "collision_audit", "outputs", "statistics",
        "source_evidence_files", "approval_gates",
    )
    for key in required:
        if key not in protocol:
            errors.append(f"missing protocol field: {key}")
    if errors:
        return errors
    if protocol["official_solvers"] != ["ga_aco", "paper_aligned_hga", "abma"]:
        errors.append("official_solvers must contain exactly ga_aco, paper_aligned_hga, and abma in protocol order")
    if protocol.get("protocol_version") != "2.6.0":
        errors.append("protocol_version must be 2.6.0")
    for key in (
        "abma_exact_fast_policy", "historical_stage_snapshots",
        "scale_calibration", "formal_experiment_design", "sensitivity_design",
    ):
        if key not in protocol:
            errors.append(f"missing protocol field: {key}")
    snapshots = protocol.get("historical_stage_snapshots", {})
    for stage in ("development", "independent_validation", "w30_budget_pilot"):
        if not isinstance(snapshots.get(stage), Mapping) or not snapshots[stage].get("recorded_at_stage_end"):
            errors.append(f"historical_stage_snapshots.{stage} must have recorded_at_stage_end")
    if any(key in protocol for key in (
        "abma_development_outcome", "abma_profile_profiling_evidence",
        "abma_development_gate_evidence", "abma_final_profile_status",
        "abma_independent_validation", "three_solver_w30_preflight",
    )):
        errors.append("historical stage state must not remain at protocol top level")
    previous = protocol.get("previous_protocol", {})
    if previous.get("sha256") != "f6a1d11957e19f4f55eb61f8e006313ce04bca0d64852b29b25417af1e7ed4f8":
        errors.append("previous protocol 2.5 hash is missing or incorrect")
    excluded = protocol.get("historical_excluded_solvers", {}).get("de_lkh", {})
    if excluded.get("eligible_for_official_summary") is not False:
        errors.append("historical_excluded_solvers.de_lkh must be ineligible for official summary")
    errors.extend(validate_frozen_abma_profile(protocol))
    model = protocol["scientific_model"]
    expected_model = {
        "weights": [0.7, 0.2, 0.1], "weld_speed": 0.0108,
        "travel_speed": 0.2, "acceleration": 0.5, "safe_z": 0.3,
        "weld_z": 0.1, "time_model": "corrected", "assignment_mode": "split",
        "split_timing_mode": "parent_aware", "direction_mode": "bidirectional",
        "route_type": "open", "include_initial_positioning": False,
        "include_final_return": False, "setup_time": 0.0, "post_time": 0.0,
    }
    for key, expected in expected_model.items():
        if model.get(key) != expected:
            errors.append(f"scientific_model.{key} must equal {expected!r}")
    try:
        motion = MotionModel(model["weld_speed"], model["travel_speed"], model["acceleration"], model["safe_z"])
        motion.validate()
    except Exception as exc:
        errors.append(f"invalid motion model: {exc}")
        motion = MotionModel()
    for key, entry in protocol["instances"].items():
        try:
            path = ROOT / entry["path"]
            welds, metadata = load_frozen_weld_instance(str(path))
            if str(metadata.get("instance_hash")) != entry["instance_hash"]:
                errors.append(f"{key}: instance hash mismatch")
            if int(metadata.get("instance_seed", -1)) != int(entry["instance_seed"]):
                errors.append(f"{key}: instance seed mismatch")
            if len(welds) != int(entry["weld_count"]) or len(welds) != int(entry["expected_original_weld_count"]):
                errors.append(f"{key}: weld count mismatch")
            if "refs" in entry:
                errors.append(f"{key}: official instance entry must not contain refs")
            stored = copy.deepcopy(entry["normalization"])
            stored_hash = stored.pop("normalization_spec_hash", None)
            validate_normalization_spec(stored)
            recomputed = compute_normalization_spec(welds, motion, entry["instance_hash"])
            expected_hash = normalization_spec_hash(recomputed)
            if stored_hash != expected_hash:
                errors.append(f"{key}: normalization spec hash mismatch")
            if normalization_spec_hash(stored) != expected_hash:
                errors.append(f"{key}: normalization values or baseline evidence mismatch")
        except Exception as exc:
            errors.append(f"{key}: instance validation failed: {exc}")
    budget = protocol["budget"]
    if budget.get("primary_budget_type") != "complete_system_objective_evaluations":
        errors.append("unsupported primary budget type")
    if to_int(budget.get("primary_budget_value")) is None or int(budget["primary_budget_value"]) <= 0:
        errors.append("primary budget must be positive")
    for solver in protocol["official_solvers"]:
        adapter = ADAPTERS.get(solver)
        if adapter is None:
            errors.append(f"adapter missing: {solver}")
        elif not resolve_project_path(adapter.script).is_file():
            errors.append(f"solver file missing: {adapter.script}")
        if solver not in budget.get("solver_cli_mapping", {}):
            errors.append(f"budget CLI mapping missing: {solver}")
    if not errors:
        try:
            first_instance_key = next(iter(protocol["instances"]))
            first_instance = protocol["instances"][first_instance_key]
            first_seed = int(protocol["solver_seeds"]["formal"][0])
            for solver in protocol["official_solvers"]:
                spec = RunSpec(
                    "formal", solver, first_instance_key, first_instance, first_seed,
                    int(budget["primary_budget_value"]), budget["formal_solver_controls"][solver],
                )
                command = ADAPTERS[solver].build(protocol, spec, sys.executable, output_root / "validation" / solver)
                for required_flag in ("--instance-path", "--solver-seed", "--max-objective-evaluations"):
                    if required_flag not in command:
                        errors.append(f"{solver}: adapter command missing {required_flag}")
                for required_flag in ("--normalization-mode", "--ideal-makespan", "--baseline-makespan", "--scale-makespan"):
                    if required_flag not in command:
                        errors.append(f"{solver}: official adapter command missing {required_flag}")
                if any(flag in command for flag in ("--ref-makespan", "--ref-load", "--ref-distance", "--reference-makespan", "--reference-load", "--reference-distance")):
                    errors.append(f"{solver}: official adapter command contains legacy reference flags")
                if solver == "abma":
                    if "--abma-variant" not in command or command[command.index("--abma-variant") + 1] != "legacy_exact_fast":
                        errors.append("abma: official adapter must select legacy_exact_fast")
                    historical_only_flags = ("--low-alns-iterations", "--mid-alns-iterations", "--high-alns-iterations")
                    if any(flag in command for flag in historical_only_flags):
                        errors.append("abma: official adapter must not inject rejected multi-fidelity controls")
        except Exception as exc:
            errors.append(f"adapter implementability check failed: {exc}")
    try:
        source_hashes(protocol)
    except Exception as exc:
        errors.append(str(exc))
    try:
        resolved = output_root.resolve()
        forbidden = {ROOT.resolve(), (ROOT / "data").resolve(), (ROOT / "data/instances/selected").resolve()}
        if resolved in forbidden:
            errors.append("output root may not be the workspace, data directory, or frozen-instance directory")
    except Exception as exc:
        errors.append(f"invalid output root: {exc}")
    if not protocol_path.is_file():
        errors.append(f"protocol file missing: {protocol_path}")
    return errors


def select_values(available: Iterable[str], requested: Optional[Sequence[str]], label: str) -> List[str]:
    values = list(available)
    if not requested:
        return values
    unknown = sorted(set(requested).difference(values))
    if unknown:
        raise ValueError(f"unknown {label}: {unknown}")
    return [value for value in values if value in requested]


def build_matrix(protocol: Mapping[str, Any], mode: str, args: argparse.Namespace) -> List[RunSpec]:
    if mode in ("abma_profile", "abma_exact_equivalence_benchmark", "abma_development_benchmark"):
        if args.instances and args.instances != ["w30"]:
            raise ValueError(f"{mode} permits only w30")
        if args.solvers and args.solvers != ["abma"]:
            raise ValueError(f"{mode} permits only ABMA")
        seeds = [int(item) for item in (args.seeds or ([42] if mode != "abma_development_benchmark" else [42, 43]))]
        budget_value = int(args.benchmark_budget)
        if mode == "abma_profile":
            if seeds != [42] or not 4 <= budget_value <= 200:
                raise ValueError("ABMA profile requires seed 42 and budget in [4, 200]")
            variants = args.abma_benchmark_variants or ["legacy"]
            if any(item not in ("legacy", "legacy_exact_fast") for item in variants):
                raise ValueError("ABMA profile permits only legacy/exact-fast variants")
        elif mode == "abma_exact_equivalence_benchmark":
            if seeds != [42] or budget_value != 100:
                raise ValueError("ABMA exact equivalence benchmark requires seed 42 and budget 100")
            variants = ["legacy", "legacy_exact_fast"]
        else:
            allowed = ((seeds == [42, 43] and budget_value == 500)
                       or (seeds == [42, 43, 44] and budget_value == 2000))
            if not allowed:
                raise ValueError("ABMA development requires seeds 42 43/budget 500 or seeds 42 43 44/budget 2000")
            variants = args.abma_benchmark_variants or ["legacy", "legacy_exact_fast"]
            if any(item not in ("legacy", "legacy_exact_fast", "exact_fast_incremental_multifidelity") for item in variants):
                raise ValueError("unsupported ABMA structural-development variant")
        controls_base = copy.deepcopy(protocol["budget"]["formal_solver_controls"]["abma"])
        controls_base.update({"max_objective_evaluations": budget_value, "early_stop_patience": 0})
        return [
            RunSpec(mode, "abma", "w30", protocol["instances"]["w30"], seed,
                    budget_value, {**controls_base, "abma_variant": variant}, variant=variant)
            for seed in seeds for variant in variants
        ]
    if mode == "abma_optimization_benchmark":
        if args.instances and args.instances != ["w30"]:
            raise ValueError("ABMA optimization benchmark permits only w30")
        if args.solvers and args.solvers != ["abma"]:
            raise ValueError("ABMA optimization benchmark permits only ABMA")
        seeds = [int(item) for item in (args.seeds or [42])]
        if any(seed not in (42, 43, 44) for seed in seeds):
            raise ValueError("ABMA optimization benchmark permits seeds 42, 43, and 44 only")
        budget_value = int(args.benchmark_budget)
        if budget_value < 4 or budget_value > 2000:
            raise ValueError("ABMA optimization benchmark budget must be in [4, 2000]")
        controls_base = copy.deepcopy(protocol["budget"]["formal_solver_controls"]["abma"])
        controls_base["max_objective_evaluations"] = budget_value
        controls_base["early_stop_patience"] = 0
        variants = args.abma_benchmark_variants or [
            "legacy", "incremental_only", "multifidelity_only", "incremental_multifidelity"
        ]
        return [
            RunSpec(mode, "abma", "w30", protocol["instances"]["w30"], seed,
                    budget_value, {**controls_base, "abma_variant": variant}, variant=variant)
            for seed in seeds
            for variant in variants
        ]
    if mode == "abma_validation_benchmark":
        if args.instances and args.instances != ["w30"]:
            raise ValueError("ABMA validation benchmark permits only w30")
        if args.solvers and args.solvers != ["abma"]:
            raise ValueError("ABMA validation benchmark permits only ABMA")
        seeds = [int(item) for item in (args.seeds or protocol["solver_seeds"]["abma_validation"])]
        if seeds != [45, 46, 47]:
            raise ValueError("ABMA validation benchmark requires frozen seeds 45 46 47 in order")
        budget_value = int(args.benchmark_budget)
        if budget_value != 2000:
            raise ValueError("ABMA validation benchmark requires exactly 2000 evaluations")
        base = copy.deepcopy(protocol["budget"]["formal_solver_controls"]["abma"])
        base["max_objective_evaluations"] = budget_value
        base["early_stop_patience"] = 0
        return [
            RunSpec(mode, "abma", "w30", protocol["instances"]["w30"], seed,
                    budget_value, {**base, "abma_variant": variant}, variant=variant)
            for seed in seeds for variant in ("legacy", "legacy_exact_fast")
        ]
    if mode in ("scale_preflight", "scale_pilot"):
        instances = list(args.instances or [])
        if len(instances) != 1 or instances[0] not in ("w45", "w60"):
            raise ValueError(f"{mode} requires exactly one instance: w45 or w60")
        if args.solvers and set(args.solvers) != set(protocol["official_solvers"]):
            raise ValueError(f"{mode} must execute all three official solvers")
        if args.seeds and [int(item) for item in args.seeds] != [42]:
            raise ValueError(f"{mode} permits only solver seed 42")
        if mode == "scale_preflight":
            budget_value = int(protocol["scale_calibration"]["preflight_budget"])
            if getattr(args, "scale_budget", None) not in (None, budget_value):
                raise ValueError("scale preflight budget is frozen at 2000")
        else:
            budget_value = int(getattr(args, "scale_budget", 0) or 0)
            floor = int(protocol["budget"]["scale_candidate_budget_floor"])
            ceiling = int(protocol["budget"]["scale_candidate_budget_ceiling"])
            rounding = int(protocol["budget"]["scale_candidate_budget_rounding"])
            if not floor <= budget_value <= ceiling or budget_value % rounding:
                raise ValueError(f"scale pilot budget must be a multiple of {rounding} in [{floor},{ceiling}]")
        instance_key = instances[0]
        specs: List[RunSpec] = []
        for solver in protocol["official_solvers"]:
            controls = copy.deepcopy(protocol["budget"]["formal_solver_controls"][solver])
            controls["max_objective_evaluations"] = budget_value
            if solver == "abma":
                controls["early_stop_patience"] = 0
            if solver == "paper_aligned_hga":
                controls["no_improve_limit"] = 0
                controls["time_limit_s"] = 0.0
            specs.append(RunSpec(
                mode, solver, instance_key, protocol["instances"][instance_key],
                42, budget_value, controls,
            ))
        return specs
    if mode in ("budget_pilot_preflight", "budget_pilot"):
        if args.instances and args.instances != ["w30"]:
            raise ValueError("budget pilot permits only w30")
        if args.solvers and set(args.solvers) != set(protocol["official_solvers"]):
            raise ValueError("budget pilot must execute all three official solvers")
        if args.seeds and [int(item) for item in args.seeds] != [42]:
            raise ValueError("budget pilot permits only solver seed 42")
        budget_value = 2000 if mode == "budget_pilot_preflight" else int(
            protocol["budget"].get("pilot_candidate_budget", protocol["budget"]["primary_budget_value"])
        )
        specs = []
        for solver in protocol["official_solvers"]:
            controls = copy.deepcopy(protocol["budget"]["formal_solver_controls"][solver])
            controls["max_objective_evaluations"] = budget_value
            if solver == "abma":
                controls["early_stop_patience"] = 0
            if solver == "paper_aligned_hga":
                controls["no_improve_limit"] = 0
                controls["time_limit_s"] = 0.0
            specs.append(RunSpec(mode, solver, "w30", protocol["instances"]["w30"],
                                 42, budget_value, controls))
        return specs
    if mode == "smoke":
        if args.instances and args.instances != ["w30"]:
            raise ValueError("smoke permits only w30")
        if args.solvers and set(args.solvers) != set(protocol["official_solvers"]):
            raise ValueError("smoke must execute all three official solvers")
        seed = int(protocol["solver_seeds"]["smoke"][0])
        if args.seeds and [int(item) for item in args.seeds] != [seed]:
            raise ValueError(f"smoke permits only solver seed {seed}")
        return [
            RunSpec(
                "smoke", solver, "w30", protocol["instances"]["w30"], seed,
                int(protocol["budget"]["smoke_solver_controls"][solver]["max_objective_evaluations"]),
                protocol["budget"]["smoke_solver_controls"][solver],
            )
            for solver in protocol["official_solvers"]
        ]
    if mode == "gaaco_ablation":
        instances = select_values(protocol["instances"], args.instances or ["w30"], "instance")
        seeds = [int(item) for item in (args.seeds or protocol["solver_seeds"]["smoke"])]
        controls = {
            "pop_size": args.ablation_pop_size or protocol["gaaco_ablation"]["default_pop_size"],
            "max_gen": args.ablation_max_gen or protocol["gaaco_ablation"]["default_max_gen"],
            "max_objective_evaluations": 0,
        }
        specs = []
        for method, modes in protocol["gaaco_ablation"]["methods"].items():
            for instance_key, seed in itertools.product(instances, seeds):
                specs.append(RunSpec(
                    "gaaco_ablation", "ga_aco", instance_key, protocol["instances"][instance_key],
                    seed, 0, controls, variant=method, variant_modes=tuple(modes),
                ))
        return specs
    solvers = select_values(protocol["official_solvers"], args.solvers, "solver")
    instances = select_values(protocol["instances"], args.instances, "instance")
    seeds = [int(item) for item in (args.seeds or protocol["solver_seeds"]["formal"])]
    controls_by_solver = protocol["budget"]["formal_solver_controls"]
    budget = int(getattr(args, "selected_evaluation_budget", None) or protocol["budget"]["primary_budget_value"])
    controls_by_solver = copy.deepcopy(controls_by_solver)
    for solver in controls_by_solver:
        controls_by_solver[solver]["max_objective_evaluations"] = budget
    return [
        RunSpec("formal", solver, instance_key, protocol["instances"][instance_key], seed,
                budget, controls_by_solver[solver])
        for solver, instance_key, seed in itertools.product(solvers, instances, seeds)
    ]


def run_base_dir(output_root: Path, spec: RunSpec) -> Path:
    parts = [output_root, spec.run_class, spec.instance_key]
    if spec.variant:
        parts.append(Path(spec.variant))
    parts.extend([Path(spec.solver), Path(f"seed_{spec.seed}")])
    result = Path(parts[0])
    for part in parts[1:]:
        result /= part
    return result


def command_identity(command: Sequence[str], run_dir: Path) -> List[str]:
    marker = relative_to_root(run_dir)
    return [str(item).replace(marker, "<RUN_DIR>") for item in command]


def next_attempt(base: Path) -> Path:
    numbers = []
    if base.exists():
        for child in base.glob("attempt_*"):
            try:
                numbers.append(int(child.name.split("_", 1)[1]))
            except (IndexError, ValueError):
                continue
    return base / f"attempt_{max(numbers, default=0) + 1:03d}"


def matching_success(base: Path, protocol_digest: str, source_digest: Mapping[str, str], command_digest: str) -> Optional[Path]:
    for attempt in sorted(base.glob("attempt_*")) if base.exists() else []:
        status_path = attempt / "run_status.json"
        if not status_path.exists():
            continue
        try:
            status = read_json(status_path)
        except Exception:
            continue
        if (
            status.get("run_status") == "success"
            and status.get("protocol_hash") == protocol_digest
            and status.get("source_hashes") == source_digest
            and status.get("command_hash") == command_digest
            and (attempt / "unified_metrics.json").is_file()
        ):
            return attempt
    return None


def classify_incomplete_attempts(base: Path) -> None:
    if not base.exists():
        return
    for attempt in base.glob("attempt_*"):
        status_path = attempt / "run_status.json"
        if status_path.exists():
            try:
                read_json(status_path)
                continue
            except Exception:
                state = "corrupt"
        else:
            state = "interrupted"
        atomic_write_json(status_path, {"run_status": state, "classified_at": utc_now(), "reason": "attempt lacked a readable terminal status"})


def recompute_solution_metrics(
    result_payload: Mapping[str, Any], spec: RunSpec, protocol: Mapping[str, Any]
) -> Dict[str, Any]:
    """Independently rebuild all four routes from IDs and direction flags."""
    solution = result_payload.get("solution")
    if not isinstance(solution, Mapping):
        metrics = result_payload.get("metrics", result_payload)
        solution = {
            "x_up": metrics.get("x_up"), "x_low": metrics.get("x_low"),
            "robots": [
                {"robot_id": index,
                 "route_task_ids": (result_payload.get("robot_order_ids") or metrics.get("robot_order_ids") or [[]] * 4)[index],
                 "direction_flags": (result_payload.get("robot_direction_flags") or metrics.get("robot_direction_flags") or [[]] * 4)[index]}
                for index in range(4)
            ],
        }
    x_up, x_low = float(solution["x_up"]), float(solution["x_low"])
    solver_robots = solution.get("robots")
    if not isinstance(solver_robots, list) or len(solver_robots) != 4:
        raise ValueError("result JSON must contain exactly four solution.robots")
    solver_robots = sorted(solver_robots, key=lambda item: int(item.get("robot_id", -1)))
    if [int(item.get("robot_id", -1)) for item in solver_robots] != [0, 1, 2, 3]:
        raise ValueError("solution.robots must have robot IDs 0,1,2,3")

    model_data = protocol["scientific_model"]
    model = MotionModel(model_data["weld_speed"], model_data["travel_speed"],
                        model_data["acceleration"], model_data["safe_z"])
    welds, metadata = load_frozen_weld_instance(str(ROOT / spec.instance["path"]))
    if metadata.get("instance_hash") != spec.instance["instance_hash"]:
        raise ValueError("independent reconstruction instance hash mismatch")
    assigned, assignment = assign_welds_to_robots_split(welds, x_up, x_low)
    if int(assignment.get("unassigned_subweld_count", -1)) != 0:
        raise ValueError("independent reconstruction produced unassigned tasks")

    robot_metrics: List[Dict[str, Any]] = []
    metric_names = ("total_weld_time", "total_travel_time", "total_time", "total_idle_distance")
    for robot_id in range(4):
        source = solver_robots[robot_id]
        route_ids = [str(value) for value in source.get("route_task_ids", [])]
        flags = [bool(value) for value in source.get("direction_flags", [])]
        if len(route_ids) != len(flags):
            raise ValueError(f"robot {robot_id}: route IDs and direction flags differ in length")
        by_id = {str(task.id): task for task in assigned[robot_id]}
        if len(by_id) != len(assigned[robot_id]):
            raise ValueError(f"robot {robot_id}: duplicate assigned task IDs")
        if len(route_ids) != len(set(route_ids)) or set(route_ids) != set(by_id):
            raise ValueError(f"robot {robot_id}: route IDs do not exactly cover assigned tasks")
        directed = [DirectedWeld(by_id[task_id], flags[position]) for position, task_id in enumerate(route_ids)]
        stats = compute_directed_route_stats(directed, model)
        item = {
            "robot_id": robot_id, "region_name": REGION_NAMES[robot_id],
            "task_count": len(route_ids), "route_task_ids": route_ids,
            "direction_flags": flags,
            **{name: float(stats[name]) for name in metric_names},
        }
        if not close(item["total_time"], item["total_weld_time"] + item["total_travel_time"], 1e-8):
            raise ValueError(f"robot {robot_id}: independent time identity failed")
        for name in metric_names:
            if name not in source or not close(source.get(name), item[name], 1e-8):
                raise ValueError(f"robot {robot_id}: solver {name} disagrees with independent reconstruction")
        if int(source.get("task_count", -1)) != item["task_count"]:
            raise ValueError(f"robot {robot_id}: solver task_count mismatch")
        robot_metrics.append(item)

    totals = [item["total_time"] for item in robot_metrics]
    system_metrics = {
        "makespan": max(totals, default=0.0),
        "load_imbalance": max(totals, default=0.0) - min(totals, default=0.0),
        "total_weld_time": sum(item["total_weld_time"] for item in robot_metrics),
        "total_travel_time": sum(item["total_travel_time"] for item in robot_metrics),
        "total_idle_distance": sum(item["total_idle_distance"] for item in robot_metrics),
        "sum_robot_total_time": sum(totals),
    }
    solver_system = result_payload.get("system_metrics")
    if not isinstance(solver_system, Mapping):
        raise ValueError("result JSON must contain system_metrics")
    for name, value in system_metrics.items():
        if name not in solver_system or not close(solver_system.get(name), value, 1e-8):
            raise ValueError(f"solver system_metrics.{name} disagrees with independent reconstruction")
    return {"robot_metrics": robot_metrics, "system_metrics": system_metrics,
            "x_up": x_up, "x_low": x_low, "assignment_stats": assignment}


def normalize_metrics(raw: Mapping[str, Any], adapter: SolverAdapter, protocol: Mapping[str, Any],
                      spec: RunSpec, run_id: str, protocol_digest: str, wall_clock: float,
                      return_code: int, recomputed: Mapping[str, Any]) -> Dict[str, Any]:
    model = protocol["scientific_model"]
    def first(*names: str) -> Any:
        for name in names:
            if name in raw and raw[name] not in (None, ""):
                return raw[name]
        return None
    normalized = {
        "protocol_version": protocol["protocol_version"], "protocol_hash": protocol_digest,
        "run_id": run_id, "run_class": spec.run_class, "solver_name": adapter.solver_name,
        "algorithm_name": (protocol["abma_candidate_freeze"]["algorithm_name"] if spec.solver == "abma" else adapter.solver_name),
        "frozen_profile_hash": (protocol["abma_candidate_freeze"]["profile_hash"] if spec.solver == "abma" else None),
        "frozen_profile_path": (protocol["abma_candidate_freeze"]["profile_path"] if spec.solver == "abma" else None),
        "solver_seed": to_int(first("solver_seed", "seed")), "instance_path": spec.instance["path"],
        "instance_seed": to_int(first("instance_seed")), "instance_hash": first("instance_hash"),
        "actual_weld_count": to_int(first("actual_weld_count", "weld_count")),
        "fitness": to_float(first("fitness")), "makespan": recomputed["system_metrics"]["makespan"],
        "load_imbalance": recomputed["system_metrics"]["load_imbalance"],
        "total_idle_distance": recomputed["system_metrics"]["total_idle_distance"],
        "total_weld_time": recomputed["system_metrics"]["total_weld_time"],
        "total_travel_time": recomputed["system_metrics"]["total_travel_time"],
        "sum_robot_total_time": recomputed["system_metrics"]["sum_robot_total_time"],
        "robot_metrics": recomputed["robot_metrics"],
        "x_up": recomputed["x_up"], "x_low": recomputed["x_low"],
        "weight_makespan": to_float(first("weight_makespan")),
        "weight_load": to_float(first("weight_load")), "weight_distance": to_float(first("weight_distance")),
        "normalization_mode": first("normalization_mode") or (LEGACY_MODE if spec.run_class == "gaaco_ablation" else OFFICIAL_MODE),
        "normalization_source": first("normalization_source"),
        "normalized_makespan": to_float(first("normalized_makespan")),
        "normalized_load_imbalance": to_float(first("normalized_load_imbalance")),
        "normalized_idle_distance": to_float(first("normalized_idle_distance")),
        "weld_speed": to_float(first("weld_speed")), "travel_speed": to_float(first("travel_speed")),
        "acceleration": to_float(first("acceleration", "acc")), "safe_z": to_float(first("safe_z")),
        "time_model": first("time_model") or "corrected",
        "assignment_mode": ("split" if first("assignment_mode") == "split_complete_segment" else first("assignment_mode")) or "split",
        "split_timing_mode": first("split_timing_mode") or "parent_aware",
        "direction_mode": first("direction_mode") or "bidirectional",
        "route_type": first("route_type") or "open",
        "objective_evaluation_count": to_int(first("objective_evaluation_count", "fitness_evaluation_count", "partition_evaluations")),
        "primary_budget_type": protocol["budget"]["primary_budget_type"],
        "primary_budget_value": spec.budget_value,
        "objective_budget_exhausted": to_bool(first("objective_budget_exhausted")),
        "algorithm_time": to_float(first("algorithm_time", "algorithm_time_s", "algo_time", "elapsed_time")),
        "solver_wall_clock_time": wall_clock, "wall_clock_time": wall_clock,
        "postprocess_time": 0.0, "total_run_wall_clock_time": wall_clock,
        "requested_objective_evaluations": spec.budget_value,
        "realized_objective_evaluations": to_int(first("objective_evaluation_count", "fitness_evaluation_count", "partition_evaluations")),
        "stop_reason": first("stop_reason"),
        "return_code": return_code, "run_status": "pending_validation",
        "unassigned_subweld_count": to_int(first("unassigned_subweld_count")),
        "raw_solver_name": first("solver_name"), "raw_metrics": dict(raw),
        "expected_scientific_model": dict(model),
        "normalization_spec_hash": spec.instance["normalization"].get("normalization_spec_hash"),
    }
    if spec.run_class == "gaaco_ablation":
        normalized.update({
            "ref_makespan": to_float(first("ref_makespan", "reference_makespan")),
            "ref_load": to_float(first("ref_load", "reference_load")),
            "ref_distance": to_float(first("ref_distance", "reference_distance")),
        })
    return normalized


def validate_unified(metrics: Mapping[str, Any], protocol: Mapping[str, Any], spec: RunSpec) -> List[str]:
    errors: List[str] = []
    model = protocol["scientific_model"]
    expected = {
        "solver_seed": spec.seed, "instance_seed": spec.instance["instance_seed"],
        "instance_hash": spec.instance["instance_hash"], "actual_weld_count": spec.instance["weld_count"],
        "time_model": "corrected", "assignment_mode": "split", "split_timing_mode": "parent_aware",
        "direction_mode": "bidirectional", "route_type": "open",
    }
    if spec.run_class == "gaaco_ablation":
        expected.update(dict(zip(("time_model", "assignment_mode", "split_timing_mode", "direction_mode"), spec.variant_modes)))
    for key, value in expected.items():
        if metrics.get(key) != value:
            errors.append(f"{key}: expected {value!r}, got {metrics.get(key)!r}")
    numeric_expected = {
        "weight_makespan": model["weights"][0], "weight_load": model["weights"][1],
        "weight_distance": model["weights"][2],
        "weld_speed": model["weld_speed"], "travel_speed": model["travel_speed"],
        "acceleration": model["acceleration"], "safe_z": model["safe_z"],
    }
    if spec.run_class != "gaaco_ablation":
        for key, value in numeric_expected.items():
            if not close(metrics.get(key), value):
                errors.append(f"{key}: expected {value!r}, got {metrics.get(key)!r}")
    for key in ("fitness", "makespan", "load_imbalance", "total_idle_distance", "x_up", "x_low"):
        value = to_float(metrics.get(key))
        if value is None or not math.isfinite(value):
            errors.append(f"{key} is missing or non-finite")
    if metrics.get("unassigned_subweld_count") != 0:
        errors.append("unassigned_subweld_count must be 0")
    if to_int(metrics.get("objective_evaluation_count")) is None or int(metrics["objective_evaluation_count"]) <= 0:
        errors.append("objective_evaluation_count must be positive")
    if metrics.get("realized_objective_evaluations") != metrics.get("objective_evaluation_count"):
        errors.append("realized_objective_evaluations must equal objective_evaluation_count")
    if spec.solver == "abma":
        freeze = protocol["abma_candidate_freeze"]
        if metrics.get("frozen_profile_hash") != freeze["profile_hash"]:
            errors.append("ABMA run does not carry the fixed frozen profile hash")
        if spec.run_class in (
            "formal", "smoke", "budget_pilot_preflight", "budget_pilot",
            "scale_preflight", "scale_pilot",
        ):
            if spec.controls.get("abma_variant") != "legacy_exact_fast":
                errors.append("official ABMA run must use legacy_exact_fast")
    if spec.run_class != "gaaco_ablation":
        if metrics.get("normalization_mode") != OFFICIAL_MODE:
            errors.append("official run must report ideal_baseline_range_v1")
        if metrics.get("normalization_source") != "externally_fixed_unified_protocol":
            errors.append("official normalization source must be externally_fixed_unified_protocol")
        if all(to_float(metrics.get(key)) is not None for key in ("makespan", "load_imbalance", "total_idle_distance")):
            raw = {"makespan": metrics["makespan"], "load_imbalance": metrics["load_imbalance"],
                   "idle_distance": metrics["total_idle_distance"]}
            normalization = spec.instance["normalization"]
            recalculated_components = normalized_components(raw, normalization)
            for field, component in (("normalized_makespan", "makespan"),
                                     ("normalized_load_imbalance", "load_imbalance"),
                                     ("normalized_idle_distance", "idle_distance")):
                if not close(metrics.get(field), recalculated_components[component], 1e-8):
                    errors.append(f"{field} recomputation mismatch")
            recalculated = normalized_objective(raw, normalization)
            if not close(metrics.get("fitness"), recalculated, 1e-8):
                errors.append(f"fitness recomputation mismatch: {metrics.get('fitness')!r} != {recalculated!r}")
    elif metrics.get("normalization_mode") != LEGACY_MODE:
        errors.append("GA+ACO ablation must remain in legacy_ratio_refs_v1")
    if spec.run_class == "formal":
        if int(metrics.get("objective_evaluation_count") or -1) != spec.budget_value:
            errors.append("formal run did not consume the exact primary budget")
        if not metrics.get("objective_budget_exhausted"):
            errors.append("formal run did not report objective budget exhaustion")
    if spec.run_class in ("budget_pilot_preflight", "budget_pilot", "scale_preflight", "scale_pilot"):
        if int(metrics.get("realized_objective_evaluations") or -1) != spec.budget_value:
            errors.append("pilot did not consume the exact requested evaluation budget")
        if not metrics.get("objective_budget_exhausted"):
            errors.append("pilot did not report objective budget exhaustion")
        if metrics.get("stop_reason") != "objective_budget":
            errors.append("pilot stop_reason must be objective_budget")
    algorithm_time = to_float(metrics.get("algorithm_time"))
    solver_time = to_float(metrics.get("solver_wall_clock_time"))
    postprocess_time = to_float(metrics.get("postprocess_time"))
    total_time = to_float(metrics.get("total_run_wall_clock_time"))
    if any(value is None or not math.isfinite(value) or value < 0.0
           for value in (algorithm_time, solver_time, postprocess_time, total_time)):
        errors.append("runtime metrics must be finite and nonnegative")
    elif algorithm_time > solver_time + 0.05:
        errors.append("algorithm_time exceeds solver_wall_clock_time")
    elif solver_time > total_time + 0.05:
        errors.append("solver_wall_clock_time exceeds total_run_wall_clock_time")
    required = UNIFIED_REQUIRED if spec.run_class != "gaaco_ablation" else tuple(
        key for key in UNIFIED_REQUIRED if not key.startswith("normalized_")
    )
    missing = [key for key in required if key not in metrics]
    if missing:
        errors.append(f"unified fields missing: {missing}")
    return errors


def collision_audit(result_path: Path, spec: RunSpec, protocol: Mapping[str, Any]) -> Dict[str, Any]:
    import collision_aware_schedule as collision
    result = read_json(result_path)
    metrics = result.get("metrics", result)
    solution = result.get("solution")
    if not isinstance(solution, Mapping):
        raise ValueError("result.json does not contain the frozen standard solution")
    solver_robots = solution.get("robots")
    if not isinstance(solver_robots, list) or len(solver_robots) != 4:
        raise ValueError("result.json does not contain four frozen solution robots")
    solver_robots = sorted(solver_robots, key=lambda item: int(item.get("robot_id", -1)))
    if [int(item.get("robot_id", -1)) for item in solver_robots] != [0, 1, 2, 3]:
        raise ValueError("frozen solution robot IDs must be 0,1,2,3")
    welds, _ = load_frozen_weld_instance(str(ROOT / spec.instance["path"]))
    robots, assignment = assign_welds_to_robots_split(welds, float(metrics["x_up"]), float(metrics["x_low"]))
    if int(assignment.get("unassigned_subweld_count", -1)) != 0:
        raise ValueError("collision reconstruction has unassigned subwelds")
    sequences: List[List[DirectedWeld]] = []
    for index, (robot_welds, frozen) in enumerate(zip(robots, solver_robots)):
        route_ids = [str(item) for item in frozen.get("route_task_ids", [])]
        route_flags = frozen.get("direction_flags", [])
        if not isinstance(route_flags, list) or len(route_flags) != len(route_ids):
            raise ValueError(f"robot {index}: frozen route and direction lengths differ")
        by_id = {str(task.id): task for task in robot_welds}
        if len(route_ids) != len(set(route_ids)) or set(route_ids) != set(by_id):
            raise ValueError(f"robot {index}: frozen route does not exactly cover assigned tasks")
        directed = [DirectedWeld(by_id[task_id], bool(route_flags[position]))
                    for position, task_id in enumerate(route_ids)]
        sequences.append(directed)
    model = protocol["scientific_model"]
    collision.gaaco.TIME_MODEL = model["time_model"]
    collision.gaaco.SPLIT_TIMING_MODE = model["split_timing_mode"]
    collision.gaaco.CORRECTED_WELD_SPEED = float(model["weld_speed"])
    collision.gaaco.CORRECTED_TRAVEL_SPEED = float(model["travel_speed"])
    collision.gaaco.CORRECTED_ACC = float(model["acceleration"])
    collision.gaaco.CORRECTED_SAFE_Z = float(model["safe_z"])
    config = protocol["collision_audit"]
    audited = collision.audit_boundary_collisions_for_four_robots(
        sequences, float(metrics["x_up"]), float(metrics["x_low"]),
        safety_distance=float(config["safety_distance"]),
        band_width=float(config["boundary_band_width"]),
        dt=float(config["sampling_interval"]),
    )
    audited["post_processing_only"] = True
    audited["original_makespan"] = float(metrics["makespan"])
    audited["raw_makespan"] = float(metrics["makespan"])
    audited["raw_fitness"] = float(metrics["fitness"])
    audited["original_metrics_unchanged"] = True
    if float(audited["collision_adjusted_makespan"]) + 1e-9 < float(metrics["makespan"]):
        raise ValueError("collision-adjusted makespan is smaller than the frozen raw makespan")
    return audited


def execute_spec(protocol: Mapping[str, Any], protocol_path: Path, spec: RunSpec,
                 python_executable: str, output_root: Path, resume: bool,
                 collision_enabled: bool, timeout_override: Optional[float] = None) -> Dict[str, Any]:
    adapter = ADAPTERS[spec.solver]
    protocol_digest = protocol_hash(protocol_path)
    hashes = source_hashes(protocol)
    base = run_base_dir(output_root, spec)
    classify_incomplete_attempts(base)
    provisional = base / "attempt_000"
    provisional_command = adapter.build(protocol, spec, python_executable, provisional)
    identity = command_identity(provisional_command, provisional)
    command_digest = sha256_bytes(json.dumps(identity, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    if resume:
        existing = matching_success(base, protocol_digest, hashes, command_digest)
        if existing is not None:
            metrics = read_json(existing / "unified_metrics.json")
            print(f"[resume] skip complete matching run: {spec.logical_id} -> {relative_to_root(existing)}")
            return metrics
    run_dir = next_attempt(base)
    run_dir.mkdir(parents=True, exist_ok=False)
    command = adapter.build(protocol, spec, python_executable, run_dir)
    actual_identity = command_identity(command, run_dir)
    actual_digest = sha256_bytes(json.dumps(actual_identity, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    if actual_digest != command_digest:
        raise AssertionError("command identity changed with attempt directory")
    run_id = f"{spec.logical_id}__{run_dir.name}"
    started_at = utc_now()
    attempt_started = time.perf_counter()
    atomic_write_json(run_dir / "command.json", {
        "argv": command, "identity_argv": actual_identity, "command_hash": command_digest,
        "cwd": str(ROOT), "run_id": run_id,
    })
    atomic_write_json(run_dir / "protocol_snapshot.json", protocol)
    atomic_write_json(run_dir / "source_hashes.json", hashes)
    atomic_write_json(run_dir / "environment.json", environment_evidence(python_executable))
    start = time.perf_counter()
    return_code = -1
    terminal = "failed"
    error = ""
    try:
        timeout_seconds = float(timeout_override or protocol["budget"]["wall_clock_safety_timeout"])
        completed = subprocess.run(
            command, cwd=str(ROOT), text=True, capture_output=True, shell=False,
            timeout=timeout_seconds, encoding="utf-8", errors="replace",
        )
        return_code = completed.returncode
        stdout, stderr = completed.stdout or "", completed.stderr or ""
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        if isinstance(stdout, bytes): stdout = stdout.decode("utf-8", "replace")
        if isinstance(stderr, bytes): stderr = stderr.decode("utf-8", "replace")
        terminal = "timeout"
        error = f"wall-clock safety timeout after {float(timeout_override or protocol['budget']['wall_clock_safety_timeout'])} s"
    wall_clock = time.perf_counter() - start
    postprocess_started = time.perf_counter()
    atomic_write_text(run_dir / "stdout.log", stdout)
    atomic_write_text(run_dir / "stderr.log", stderr)
    validation_errors: List[str] = []
    unified: Dict[str, Any] = {}
    if terminal != "timeout" and return_code == 0:
        try:
            raw = read_last_csv_row(run_dir / "metrics.csv")
            result_payload = read_json(run_dir / "result.json")
            result_metrics = result_payload.get("metrics", result_payload)
            if isinstance(result_metrics, Mapping):
                raw.update(result_metrics)
            recomputed = recompute_solution_metrics(result_payload, spec, protocol)
            unified = normalize_metrics(raw, adapter, protocol, spec, run_id, protocol_digest,
                                        wall_clock, return_code, recomputed)
            unified["checkpoint_trace"] = result_payload.get("checkpoint_trace", [])
            for evidence_key in (
                "history", "robot_order_ids", "robot_orders", "robot_direction_flags",
                "assignment_stats", "alns_stats",
            ):
                if evidence_key in result_payload:
                    unified[evidence_key] = result_payload[evidence_key]
            for evidence_key in (
                "candidate_sequence_hash", "candidate_acceptance_hash",
                "outer_rng_final_state_hash", "inner_rng_final_state_hash",
            ):
                if evidence_key in raw:
                    unified[evidence_key] = raw[evidence_key]
            unified["run_directory"] = relative_to_root(run_dir)
            if collision_enabled:
                audited = collision_audit(run_dir / "result.json", spec, protocol)
                atomic_write_json(run_dir / "collision_metrics.json", audited)
                if not close(audited.get("original_makespan"), unified.get("makespan"), 1e-8):
                    validation_errors.append("collision audit original makespan does not match main result")
            else:
                atomic_write_json(run_dir / "collision_metrics.json", {"enabled": False, "post_processing_only": True})
            postprocess_time = time.perf_counter() - postprocess_started
            total_run_time = time.perf_counter() - attempt_started
            unified.update({
                "postprocess_time": postprocess_time,
                "total_run_wall_clock_time": total_run_time,
                "solver_wall_clock_time": wall_clock,
                "wall_clock_time": wall_clock,
            })
            realized = int(unified.get("realized_objective_evaluations") or 0)
            algorithm_time = float(unified.get("algorithm_time") or 0.0)
            unified["evaluations_per_second"] = realized / algorithm_time if algorithm_time > 0 else None
            unified["seconds_per_evaluation"] = algorithm_time / realized if realized > 0 else None
            validation_errors.extend(validate_unified(unified, protocol, spec))
            terminal = "invalid" if validation_errors else "success"
        except Exception as exc:
            terminal = "invalid"
            validation_errors.append(str(exc))
    elif terminal != "timeout":
        error = f"solver returned nonzero exit status {return_code}"
    if unified:
        unified["variant"] = spec.variant
        unified["run_status"] = terminal
        atomic_write_json(run_dir / "unified_metrics.json", unified)
    status = {
        "run_id": run_id, "run_status": terminal, "started_at": started_at,
        "finished_at": utc_now(), "wall_clock_time": wall_clock,
        "solver_wall_clock_time": wall_clock,
        "postprocess_time": max(0.0, time.perf_counter() - postprocess_started),
        "total_run_wall_clock_time": max(wall_clock, time.perf_counter() - attempt_started),
        "return_code": return_code,
        "error": error, "validation_errors": validation_errors,
        "protocol_hash": protocol_digest, "command_hash": command_digest,
        "source_hashes": hashes, "run_class": spec.run_class,
    }
    atomic_write_json(run_dir / "run_status.json", status)
    print(f"[{terminal}] {spec.logical_id} -> {relative_to_root(run_dir)}")
    return unified or status


def dry_run(protocol: Mapping[str, Any], specs: Sequence[RunSpec], python_executable: str, output_root: Path) -> None:
    print(f"estimated_run_count={len(specs)}")
    for index, spec in enumerate(specs, 1):
        run_dir = run_base_dir(output_root, spec) / "attempt_NNN"
        command = ADAPTERS[spec.solver].build(protocol, spec, python_executable, run_dir)
        print(json.dumps({
            "index": index, "solver": spec.solver, "instance": spec.instance_key,
            "seed": spec.seed, "run_class": spec.run_class, "variant": spec.variant,
            "primary_budget_type": protocol["budget"]["primary_budget_type"],
            "primary_budget_value": spec.budget_value,
            "normalization": spec.instance["normalization"],
            "output_directory": relative_to_root(run_dir), "command": command,
        }, ensure_ascii=False, sort_keys=True))


def atomic_write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    fields = list(rows[0]) if rows else []
    with temp.open("w", newline="", encoding="utf-8-sig") as handle:
        if fields:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
    os.replace(temp, path)


def floor_to_multiple(value: float, multiple: int) -> int:
    return int(math.floor(float(value) / multiple) * multiple)


def ceil_to_multiple(value: float, multiple: int) -> int:
    return int(math.ceil(float(value) / multiple) * multiple)


def _project_artifact_path(filename: str) -> Path:
    lower = filename.lower()
    if lower.endswith(".md"):
        directory = ROOT / "docs" / ("plans" if "plan" in lower or "recommendation" in lower else "reports")
    elif filename in {"formal_experiment_manifest.json", "formal_experiment_manifest.csv"}:
        directory = ROOT / "config" / "protocols"
    elif filename == "cross_instance_budget_options.json":
        directory = ROOT / "config" / "budgets"
    elif lower.startswith("abma_"):
        directory = ROOT / "results" / "abma"
    elif lower.startswith("eprk_"):
        directory = ROOT / "results" / "eprk"
    elif lower.startswith("fixed_presplit_"):
        directory = ROOT / "results" / "fixed_presplit"
    elif lower.startswith(("w45_", "w60_", "three_solver_", "collision_")):
        directory = ROOT / "results" / "calibration"
    else:
        directory = ROOT / "results" / "misc"
    directory.mkdir(parents=True, exist_ok=True)
    return directory / filename


def _write_json_both(output_root: Path, filename: str, value: Any) -> None:
    atomic_write_json(output_root / filename, value)
    atomic_write_json(_project_artifact_path(filename), value)


def _write_text_both(output_root: Path, filename: str, value: str) -> None:
    atomic_write_text(output_root / filename, value)
    atomic_write_text(_project_artifact_path(filename), value)


def _write_csv_both(output_root: Path, filename: str, rows: Sequence[Mapping[str, Any]]) -> None:
    atomic_write_csv(output_root / filename, rows)
    atomic_write_csv(_project_artifact_path(filename), rows)


def summarize_scale_calibration(
    protocol: Mapping[str, Any], results: Sequence[Mapping[str, Any]],
    output_root: Path, mode: str,
) -> Dict[str, Any]:
    if mode not in ("scale_preflight", "scale_pilot"):
        raise ValueError(f"unsupported scale calibration mode: {mode}")
    if not results:
        raise ValueError("scale calibration has no result rows")
    instance_key = str(results[0].get("instance_path", "")).split("instance_")[-1].split(".")[0]
    if instance_key not in ("w45", "w60"):
        instance_hash = results[0].get("instance_hash")
        instance_key = next(key for key, item in protocol["instances"].items()
                            if item["instance_hash"] == instance_hash)
    rows: List[Dict[str, Any]] = []
    for item in results:
        requested = int(item.get("requested_objective_evaluations") or -1)
        realized = int(item.get("realized_objective_evaluations") or -1)
        rows.append({
            "instance": instance_key,
            "solver": item.get("solver_name"),
            "solver_seed": item.get("solver_seed"),
            "requested_objective_evaluations": requested,
            "realized_objective_evaluations": realized,
            "exact_budget": requested == realized,
            "stop_reason": item.get("stop_reason"),
            "run_status": item.get("run_status"),
            "fitness": item.get("fitness"),
            "makespan": item.get("makespan"),
            "load_imbalance": item.get("load_imbalance"),
            "total_idle_distance": item.get("total_idle_distance"),
            "algorithm_time": item.get("algorithm_time"),
            "solver_wall_clock_time": item.get("solver_wall_clock_time"),
            "postprocess_time": item.get("postprocess_time"),
            "total_run_wall_clock_time": item.get("total_run_wall_clock_time"),
            "evaluations_per_second": item.get("evaluations_per_second"),
            "seconds_per_evaluation": item.get("seconds_per_evaluation"),
            "unassigned_subweld_count": item.get("unassigned_subweld_count"),
            "checkpoint_count": len(item.get("checkpoint_trace") or []),
            "frozen_profile_hash": item.get("frozen_profile_hash"),
            "run_directory": item.get("run_directory"),
        })
    all_success = len(rows) == 3 and all(row["run_status"] == "success" for row in rows)
    all_exact = all(row["exact_budget"] and row["stop_reason"] == "objective_budget" for row in rows)
    max_wall = max((float(row["solver_wall_clock_time"] or 0.0) for row in rows), default=0.0)
    summary: Dict[str, Any] = {
        "generated_at": utc_now(), "formal": False, "ranking_performed": False,
        "run_class": mode, "instance": instance_key, "solver_seed": 42,
        "protocol_version": protocol["protocol_version"],
        "all_runs_successful": all_success, "all_budgets_exact": all_exact,
        "collision_audit_during_solver": False,
        "max_observed_solver_wall_clock_time": max_wall,
        "rows": rows,
    }
    prefix = instance_key.lower()
    if mode == "scale_preflight":
        target = int(protocol["scale_calibration"]["target_budget"])
        preflight = int(protocol["scale_calibration"]["preflight_budget"])
        timeout = float(protocol["scale_calibration"]["wall_clock_timeout_s"])
        projected_rows = []
        for row in rows:
            projected = float(row["solver_wall_clock_time"] or 0.0) * target / preflight
            projected_rows.append({
                "solver": row["solver"], "preflight_wall_seconds": row["solver_wall_clock_time"],
                "projected_target_wall_seconds": projected,
                "projected_evaluations_per_second": row["evaluations_per_second"],
            })
        max_projected = max((row["projected_target_wall_seconds"] for row in projected_rows), default=0.0)
        safety_ratio = timeout / max_projected if max_projected > 0 else 0.0
        threshold = float(protocol["budget"]["scale_safety_margin_threshold"])
        if safety_ratio >= threshold:
            candidate = target
            decision = "target_16000_safe"
        else:
            raw_candidate = target * timeout / (threshold * max_projected) if max_projected > 0 else 0.0
            candidate = floor_to_multiple(raw_candidate, int(protocol["budget"]["scale_candidate_budget_rounding"]))
            candidate = max(int(protocol["budget"]["scale_candidate_budget_floor"]),
                            min(int(protocol["budget"]["scale_candidate_budget_ceiling"]), candidate))
            decision = "reduced_by_frozen_safety_formula"
        summary.update({
            "preflight_budget": preflight, "target_budget": target,
            "projection_multiplier": target / preflight,
            "projected_rows": projected_rows,
            "max_projected_target_wall_seconds": max_projected,
            "safety_margin_ratio": safety_ratio,
            "safety_margin_threshold": threshold,
            "candidate_budget": candidate, "candidate_decision": decision,
        })
        _write_csv_both(output_root, f"{prefix}_scale_preflight_runs.csv", rows)
        _write_json_both(output_root, f"{prefix}_scale_preflight_summary.json", summary)
    else:
        budget = int(rows[0]["requested_objective_evaluations"])
        timeout_recommendation = ceil_to_multiple(max(21600.0, 1.5 * max_wall + 300.0), 300)
        summary.update({
            "validated_candidate_budget": budget if all_success and all_exact else None,
            "recommended_instance_timeout_seconds": timeout_recommendation,
            "pilot_valid": all_success and all_exact,
        })
        _write_csv_both(output_root, f"{prefix}_scale_pilot_runs.csv", rows)
        _write_json_both(output_root, f"{prefix}_scale_pilot_summary.json", summary)
        report = [
            f"# {instance_key.upper()} Scale Calibration Report", "",
            "This is a budget/timing Pilot only. It contains no algorithm-quality ranking.", "",
            "| Solver | Requested | Realized | Status | Stop | Algorithm s | Solver wall s | Fitness | Makespan |",
            "|---|---:|---:|---|---|---:|---:|---:|---:|",
        ]
        for row in rows:
            report.append(
                f"| {row['solver']} | {row['requested_objective_evaluations']} | "
                f"{row['realized_objective_evaluations']} | {row['run_status']} | {row['stop_reason']} | "
                f"{float(row['algorithm_time'] or 0):.6f} | {float(row['solver_wall_clock_time'] or 0):.6f} | "
                f"{float(row['fitness'] or 0):.12g} | {float(row['makespan'] or 0):.12g} |"
            )
        report.extend(["", f"- Validated common instance budget: `{summary['validated_candidate_budget']}`.",
                       f"- Recommended instance timeout: `{timeout_recommendation}` seconds.",
                       "- Collision audit was excluded from solver and algorithm timing.",
                       "- Formal execution remains unapproved.", ""])
        _write_text_both(output_root, f"{instance_key.upper()}_SCALE_CALIBRATION_REPORT.md", "\n".join(report))
    return summary


def _solver_key_from_metrics(metrics: Mapping[str, Any]) -> str:
    matches = [key for key, adapter in ADAPTERS.items() if adapter.solver_name == metrics.get("solver_name")]
    if len(matches) != 1:
        raise ValueError(f"cannot map solver_name to an official adapter: {metrics.get('solver_name')!r}")
    return matches[0]


def postprocess_collision_attempt(
    protocol: Mapping[str, Any], unified_path: Path,
) -> Dict[str, Any]:
    unified = read_json(unified_path)
    run_dir = unified_path.parent
    result_path = run_dir / "result.json"
    before_result_hash = sha256_file(result_path)
    before_unified_hash = sha256_file(unified_path)
    started = time.perf_counter()
    base_record = {
        "audit_version": "protocol_2.6_collision_postprocess_v1",
        "post_processing_only": True,
        "raw_run_directory": relative_to_root(run_dir),
        "raw_protocol_hash": unified.get("protocol_hash"),
        "solver": unified.get("solver_name"),
        "solver_seed": unified.get("solver_seed"),
        "instance_hash": unified.get("instance_hash"),
        "raw_result_sha256_before": before_result_hash,
        "raw_unified_metrics_sha256_before": before_unified_hash,
        "raw_makespan": unified.get("makespan"),
        "raw_fitness": unified.get("fitness"),
    }
    try:
        instance_key = next(key for key, item in protocol["instances"].items()
                            if item["instance_hash"] == unified.get("instance_hash"))
        solver_key = _solver_key_from_metrics(unified)
        spec = RunSpec(
            str(unified.get("run_class")), solver_key, instance_key,
            protocol["instances"][instance_key], int(unified["solver_seed"]),
            int(unified["realized_objective_evaluations"]), {},
        )
        audited = collision_audit(result_path, spec, protocol)
        elapsed = time.perf_counter() - started
        unresolved = int(audited.get("unresolved_conflict_count", -1))
        record = {
            **base_record, **audited,
            "instance": instance_key,
            "collision_audit_time": elapsed,
            "audit_status": "success" if unresolved == 0 else "completed_with_unresolved_conflicts",
            "audit_failure_reason": None,
            "collision_success_claimed": unresolved == 0,
        }
    except Exception as exc:
        record = {
            **base_record,
            "collision_audit_time": time.perf_counter() - started,
            "audit_status": "failed",
            "audit_failure_reason": f"{type(exc).__name__}: {exc}",
            "collision_success_claimed": False,
            "collision_adjusted_makespan": None,
            "added_waiting_time": None,
            "conflict_count": None,
            "unresolved_conflict_count": None,
        }
    after_result_hash = sha256_file(result_path)
    after_unified_hash = sha256_file(unified_path)
    record.update({
        "raw_result_sha256_after": after_result_hash,
        "raw_unified_metrics_sha256_after": after_unified_hash,
        "raw_files_unchanged": (
            before_result_hash == after_result_hash and before_unified_hash == after_unified_hash
        ),
    })
    if not record["raw_files_unchanged"]:
        record.update({
            "audit_status": "failed", "collision_success_claimed": False,
            "audit_failure_reason": "raw result or unified metrics changed during collision postprocess",
        })
    atomic_write_json(run_dir / "collision_postprocess.json", record)
    return record


def run_collision_postprocess(
    protocol: Mapping[str, Any], input_roots: Sequence[Path], output_root: Path,
) -> Dict[str, Any]:
    candidates: List[Path] = []
    for root in input_roots:
        candidates.extend(root.glob("**/unified_metrics.json"))
    eligible: List[Path] = []
    for path in sorted(set(item.resolve() for item in candidates)):
        item = read_json(path)
        run_class = item.get("run_class")
        budget = int(item.get("realized_objective_evaluations") or 0)
        if item.get("run_status") != "success":
            continue
        if (run_class == "budget_pilot" and item.get("instance_hash") == protocol["instances"]["w30"]["instance_hash"] and budget == 16000) or run_class == "scale_pilot":
            eligible.append(path)
    records = [postprocess_collision_attempt(protocol, path) for path in eligible]
    csv_rows = [{
        "instance": item.get("instance"), "instance_hash": item.get("instance_hash"),
        "solver": item.get("solver"), "solver_seed": item.get("solver_seed"),
        "audit_status": item.get("audit_status"), "raw_makespan": item.get("raw_makespan"),
        "collision_adjusted_makespan": item.get("collision_adjusted_makespan"),
        "added_waiting_time": item.get("added_waiting_time"),
        "conflict_count": item.get("conflict_count"),
        "unresolved_conflict_count": item.get("unresolved_conflict_count"),
        "collision_audit_time": item.get("collision_audit_time"),
        "raw_files_unchanged": item.get("raw_files_unchanged"),
        "collision_success_claimed": item.get("collision_success_claimed"),
        "audit_failure_reason": item.get("audit_failure_reason"),
        "raw_run_directory": item.get("raw_run_directory"),
    } for item in records]
    summary = {
        "generated_at": utc_now(), "formal": False, "ranking_performed": False,
        "post_processing_only": True, "eligible_run_count": len(eligible),
        "audit_record_count": len(records),
        "required_run_count": 9,
        "all_required_runs_present": len(records) == 9,
        "all_audits_completed_without_failure": all(item.get("audit_status") != "failed" for item in records),
        "all_raw_files_unchanged": all(item.get("raw_files_unchanged") for item in records),
        "successful_zero_unresolved_count": sum(item.get("audit_status") == "success" for item in records),
        "completed_with_unresolved_count": sum(item.get("audit_status") == "completed_with_unresolved_conflicts" for item in records),
        "failed_count": sum(item.get("audit_status") == "failed" for item in records),
        "records": records,
    }
    _write_csv_both(output_root, "collision_audit_pilot_runs.csv", csv_rows)
    _write_json_both(output_root, "collision_audit_pilot_summary.json", summary)
    report = [
        "# Collision Audit Pilot Policy Report", "",
        "The audit is a deterministic postprocess of frozen successful raw solutions. It is not part of optimization, evaluation budget, or algorithm ranking.", "",
        "| Instance | Solver | Status | Raw makespan | Adjusted makespan | Added wait | Conflicts | Unresolved | Audit s | Raw unchanged |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in csv_rows:
        report.append(
            f"| {row['instance']} | {row['solver']} | {row['audit_status']} | {row['raw_makespan']} | "
            f"{row['collision_adjusted_makespan']} | {row['added_waiting_time']} | {row['conflict_count']} | "
            f"{row['unresolved_conflict_count']} | {float(row['collision_audit_time'] or 0):.6f} | {row['raw_files_unchanged']} |"
        )
    report.extend(["", "- Raw fitness, makespan, boundaries, routes, and directions are not overwritten.",
                   "- Any failed audit or unresolved conflict is explicit and cannot be described as collision success.",
                   "- Raw and collision-adjusted metrics must be reported as separate families.",
                   "- No ranking conclusion is drawn from this Pilot.", ""])
    _write_text_both(output_root, "COLLISION_AUDIT_POLICY_REPORT.md", "\n".join(report))
    return summary


def generate_formal_manifest(
    protocol: Mapping[str, Any], protocol_path: Path, python_executable: str,
    output_root: Path,
) -> Dict[str, Any]:
    options_path = resolve_project_path("cross_instance_budget_options.json")
    if not options_path.is_file():
        raise FileNotFoundError("cross_instance_budget_options.json must be generated before the formal manifest")
    options = read_json(options_path)
    protocol_digest = protocol_hash(protocol_path)
    seeds = list(protocol["formal_experiment_design"]["paired_solver_seeds"])
    entries: List[Dict[str, Any]] = []
    order = 0
    for instance_key, solver, seed in itertools.product(
        protocol["formal_experiment_design"]["instances"],
        protocol["official_solvers"], seeds,
    ):
        order += 1
        instance = protocol["instances"][instance_key]
        budget_a = int(options["option_A_global_budget"])
        budget_b = int(options["option_B_per_instance_budgets"][instance_key])
        timeout_global = int(options["timeout_options"]["global_seconds"])
        timeout_instance = int(options["timeout_options"]["per_instance_seconds"][instance_key])
        run_id = f"formal__{instance_key}__{solver}__seed_{seed}"
        output_directory = f"unified_experiments/formal_protocol_{protocol_digest}/formal/{instance_key}/{solver}/seed_{seed}"
        identity = {
            "run_id": run_id, "instance_hash": instance["instance_hash"], "solver": solver,
            "seed": seed, "budget_options": {"A": budget_a, "B": budget_b},
            "timeout_options": {"global": timeout_global, "per_instance": timeout_instance},
            "protocol_hash": protocol_digest, "normalization_hash": instance["normalization"]["normalization_spec_hash"],
            "abma_profile_hash": protocol["abma_candidate_freeze"]["profile_hash"] if solver == "abma" else None,
            "collision_policy": "separate_postprocess_after_successful_raw_run",
            "output_directory": output_directory,
        }
        command_hash = sha256_bytes(json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))
        entries.append({
            **identity, "order": order,
            "evaluation_budget": budget_a if budget_a == budget_b else None,
            "evaluation_budget_selection_status": "unambiguous" if budget_a == budget_b else "pending_user_selection_A_or_B",
            "timeout_seconds": timeout_global if timeout_global == timeout_instance else None,
            "timeout_selection_status": "unambiguous" if timeout_global == timeout_instance else "pending_user_selection_global_or_per_instance",
            "resume_key": f"{protocol_digest}:{instance['instance_hash']}:{solver}:{seed}:{command_hash}",
            "command_hash": command_hash,
            "command_template": [
                python_executable, "src/run_unified_experiments.py", "--formal", "--resume",
                "--instances", instance_key, "--solvers", solver, "--seeds", str(seed),
                "--selected-evaluation-budget", str(budget_a) if budget_a == budget_b else "<USER_SELECTED_A_OR_B_BUDGET>",
                "--selected-timeout", str(timeout_global) if timeout_global == timeout_instance else "<USER_SELECTED_TIMEOUT>",
                "--output-root", output_directory,
            ],
        })
    if len(entries) != 270:
        raise AssertionError(f"formal manifest must contain 270 entries, got {len(entries)}")
    manifest = {
        "generated_at": utc_now(), "manifest_only": True, "formal_execution_authorized": False,
        "protocol_version": protocol["protocol_version"], "protocol_hash": protocol_digest,
        "run_count": len(entries), "budget_selection_status": options["selection_status"],
        "entries": entries,
    }
    csv_rows = [{
        "order": item["order"], "run_id": item["run_id"], "instance_hash": item["instance_hash"],
        "solver": item["solver"], "seed": item["seed"],
        "budget_A": item["budget_options"]["A"], "budget_B": item["budget_options"]["B"],
        "evaluation_budget": item["evaluation_budget"],
        "timeout_global": item["timeout_options"]["global"],
        "timeout_per_instance": item["timeout_options"]["per_instance"],
        "protocol_hash": item["protocol_hash"], "normalization_hash": item["normalization_hash"],
        "abma_profile_hash": item["abma_profile_hash"], "collision_policy": item["collision_policy"],
        "output_directory": item["output_directory"], "resume_key": item["resume_key"],
        "command_hash": item["command_hash"],
        "command_template": json.dumps(item["command_template"], ensure_ascii=False),
    } for item in entries]
    _write_json_both(output_root, "formal_experiment_manifest.json", manifest)
    _write_csv_both(output_root, "formal_experiment_manifest.csv", csv_rows)
    plan = [
        "# Formal Experiment Execution Plan", "",
        "This file freezes a 270-run manifest and dry command templates only. It does not authorize or launch formal experiments.", "",
        f"- Protocol SHA-256: `{protocol_digest}`",
        "- Instances: w30, w45, w60",
        "- Official solvers: GA+ACO, Paper-Aligned-HGA-XCut-Control, ABMA-Legacy-Exact-Fast-v1",
        "- Paired seeds: 42–71 (30 per solver/instance)",
        "- Total raw optimization runs: 270",
        f"- Budget selection status: `{options['selection_status']}`",
        "- Collision audit: separate postprocess after a successful frozen raw run",
        "- Resume: requires matching protocol, source, and command hashes",
        "- Failure: retain attempt and exclude it from successful paired statistics; never substitute a seed",
        "- Formal execution remains fail-closed until explicit user approval and budget/timeout selections.", "",
        "## Statistics", "",
        "Per instance: descriptive mean/std/median/min/max/IQR/success/time; Friedman raw-fitness omnibus; paired Wilcoxon for three pairs; Holm correction; rank-biserial effect size; W/T/L; deterministic 10,000-resample paired median-difference percentile bootstrap 95% CI.", "",
        "Raw and collision-adjusted metric families are reported separately.", "",
        "## Sensitivity design", "",
        "Weights, load-scale floor, deterministic baseline construction, and collision separation are isolated under a separate sensitivity output root and do not contaminate the main experiment.", "",
    ]
    _write_text_both(output_root, "FORMAL_EXPERIMENT_EXECUTION_PLAN.md", "\n".join(plan))
    return manifest


def summarize_abma_profile(results: Sequence[Mapping[str, Any]], output_root: Path) -> Dict[str, Any]:
    successful = [item for item in results if item.get("run_status") == "success"]
    profiles: List[Dict[str, Any]] = []
    for item in successful:
        run_dir = ROOT / str(item["run_directory"])
        profile = read_json(run_dir / "abma_internal_profile.json")
        profiles.append({"variant": item.get("variant"), "seed": item.get("solver_seed"),
                         "budget": item.get("realized_objective_evaluations"), **profile})
    aggregate_times: Dict[str, float] = {}
    aggregate_counts: Dict[str, int] = {}
    functions: List[Dict[str, Any]] = []
    for profile in profiles:
        for key, value in profile.get("stage_times", {}).items():
            aggregate_times[key] = aggregate_times.get(key, 0.0) + float(value)
        for key, value in profile.get("counts", {}).items():
            aggregate_counts[key] = aggregate_counts.get(key, 0) + int(value)
        functions.extend(profile.get("top_functions", []))
    functions.sort(key=lambda item: (-float(item.get("cumulative_time", 0.0)), str(item.get("function"))))
    summary = {
        "run_class": "abma_profile", "formal": False,
        "all_runs_successful": len(successful) == len(results),
        "profiles": profiles, "stage_times": aggregate_times,
        "counts": aggregate_counts, "top_functions": functions[:10],
    }
    atomic_write_json(output_root / "abma_profile_summary.json", summary)
    atomic_write_text(output_root / "abma_profile_functions.txt", "\n".join(
        f"{index:02d}\t{item.get('cumulative_time', 0):.9f}\t{item.get('own_time', 0):.9f}\t{item.get('total_calls', 0)}\t{item.get('function')}"
        for index, item in enumerate(functions[:10], 1)
    ) + "\n")
    atomic_write_csv(output_root / "abma_profile_stage_times.csv", [
        {"stage": key, "seconds": value, "count": aggregate_counts.get(key.replace("time_", "") + "_count", "")}
        for key, value in aggregate_times.items()
    ])
    return summary


def _trace_without_time(value: Any) -> Any:
    if isinstance(value, list):
        return [_trace_without_time(item) for item in value]
    if isinstance(value, dict):
        return {key: _trace_without_time(item) for key, item in value.items()
                if key not in ("elapsed_time", "elapsed_algorithm_time", "wall_clock_time")}
    return value


def abma_exact_equivalence(legacy: Mapping[str, Any], candidate: Mapping[str, Any]) -> List[str]:
    errors: List[str] = []
    exact_fields = (
        "realized_objective_evaluations", "stop_reason", "x_up", "x_low",
        "makespan", "load_imbalance", "total_idle_distance",
        "robot_order_ids", "robot_orders", "robot_direction_flags", "history",
        "candidate_sequence_hash", "candidate_acceptance_hash",
        "outer_rng_final_state_hash", "inner_rng_final_state_hash",
    )
    for key in exact_fields:
        if legacy.get(key) != candidate.get(key):
            errors.append(f"{key} mismatch")
    if not math.isclose(float(legacy.get("fitness", math.inf)), float(candidate.get("fitness", math.inf)), rel_tol=0.0, abs_tol=1e-12):
        errors.append("fitness mismatch beyond 1e-12")
    if _trace_without_time(legacy.get("checkpoint_trace")) != _trace_without_time(candidate.get("checkpoint_trace")):
        errors.append("checkpoint_trace mismatch")
    return errors


def summarize_abma_paired(results: Sequence[Mapping[str, Any]], output_root: Path,
                          run_class: str) -> Dict[str, Any]:
    by_seed: Dict[int, Dict[str, Mapping[str, Any]]] = {}
    for item in results:
        by_seed.setdefault(int(item.get("solver_seed", -1)), {})[str(item.get("variant"))] = item
    rows: List[Dict[str, Any]] = []
    for seed, variants in sorted(by_seed.items()):
        legacy = variants.get("legacy")
        for candidate_name, candidate in variants.items():
            if candidate_name == "legacy" or legacy is None:
                continue
            errors = abma_exact_equivalence(legacy, candidate)
            legacy_wall = float(legacy.get("solver_wall_clock_time") or 0.0)
            candidate_wall = float(candidate.get("solver_wall_clock_time") or 0.0)
            rows.append({
                "seed": seed, "legacy_variant": "legacy", "candidate_variant": candidate_name,
                "legacy_budget": legacy.get("realized_objective_evaluations"),
                "candidate_budget": candidate.get("realized_objective_evaluations"),
                "legacy_fitness": legacy.get("fitness"), "candidate_fitness": candidate.get("fitness"),
                "fitness_degradation": (float(candidate.get("fitness")) / max(1e-300, float(legacy.get("fitness"))) - 1.0),
                "legacy_solver_wall_clock_time": legacy_wall,
                "candidate_solver_wall_clock_time": candidate_wall,
                "speedup": legacy_wall / candidate_wall if candidate_wall > 0 else None,
                "exact_equivalence": not errors, "equivalence_errors": "; ".join(errors),
                "frozen_profile_hash": candidate.get("frozen_profile_hash"),
                "legacy_run_status": legacy.get("run_status"), "candidate_run_status": candidate.get("run_status"),
            })
    if run_class == "abma_exact_equivalence_benchmark":
        filename = "abma_exact_equivalence_results.csv"
    elif run_class == "abma_validation_benchmark":
        filename = "abma_validation_runs.csv"
    else:
        filename = "abma_exact_fast_development_results.csv"
    atomic_write_csv(output_root / filename, rows)
    speedups = [float(row["speedup"]) for row in rows if row.get("speedup") is not None]
    expected_hash = protocol_hash_value = "631b2d675e7531d04a3c019cc8013158223944074da3e42d489a5745addee1d3"
    all_budgets_exact = bool(rows) and all(
        row.get("legacy_budget") == 2000 and row.get("candidate_budget") == 2000 for row in rows
    ) if run_class == "abma_validation_benchmark" else True
    profile_hash_exact = bool(rows) and all(row.get("frozen_profile_hash") == expected_hash for row in rows)
    summary = {
        "run_class": run_class, "formal": False, "rows": rows,
        "all_runs_successful": all(item.get("run_status") == "success" for item in results),
        "all_exact_equivalent": bool(rows) and all(bool(row["exact_equivalence"]) for row in rows),
        "median_speedup": statistics.median(speedups) if speedups else None,
        "minimum_per_seed_speedup": min(speedups) if speedups else None,
        "all_budgets_exact": all_budgets_exact,
        "frozen_profile_hash": protocol_hash_value,
        "frozen_profile_hash_exact": profile_hash_exact,
        "validation_gate_passed": False,
    }
    if run_class == "abma_validation_benchmark":
        summary["validation_gate_passed"] = bool(
            summary["all_runs_successful"] and summary["all_exact_equivalent"]
            and all_budgets_exact and profile_hash_exact
            and summary["median_speedup"] is not None and summary["median_speedup"] >= 2.0
            and summary["minimum_per_seed_speedup"] is not None and summary["minimum_per_seed_speedup"] >= 1.5
        )
        atomic_write_json(output_root / "abma_validation_summary.json", summary)
        lines = [
            "# ABMA Independent Validation Report", "",
            "This is an independent frozen-candidate validation on seeds 45–47; no parameter tuning was performed.", "",
            f"- Frozen algorithm: `ABMA-Legacy-Exact-Fast-v1` / `legacy_exact_fast`",
            f"- Frozen profile SHA-256: `{expected_hash}`",
            f"- Exact scientific equivalence: `{summary['all_exact_equivalent']}`",
            f"- Exact 2000-evaluation budgets: `{all_budgets_exact}`",
            f"- Median speedup: `{summary['median_speedup']}` (gate ≥ 2.0)",
            f"- Minimum per-seed speedup: `{summary['minimum_per_seed_speedup']}` (gate ≥ 1.5)",
            f"- Validation gate passed: `{summary['validation_gate_passed']}`", "",
            "No three-solver quality ranking is made by this validation.",
        ]
        atomic_write_text(output_root / "ABMA_INDEPENDENT_VALIDATION_REPORT.md", "\n".join(lines) + "\n")
    return summary


def smoke_cross_validation(protocol: Mapping[str, Any], results: Sequence[Mapping[str, Any]], output_root: Path) -> Dict[str, Any]:
    errors: List[str] = []
    if len(results) != len(protocol["official_solvers"]):
        errors.append(f"expected {len(protocol['official_solvers'])} smoke results, got {len(results)}")
    for item in results:
        if item.get("run_status") != "success":
            errors.append(f"{item.get('solver_name', item.get('run_id'))}: not successful")
    fields = (
        "instance_hash", "instance_seed", "actual_weld_count", "solver_seed",
        "weight_makespan", "weight_load", "weight_distance", "normalization_mode",
        "normalization_spec_hash", "weld_speed", "travel_speed", "acceleration",
        "safe_z", "time_model", "assignment_mode", "split_timing_mode", "direction_mode",
    )
    if results:
        baseline = results[0]
        for item in results[1:]:
            for name in fields:
                if isinstance(baseline.get(name), float) or isinstance(item.get(name), float):
                    same = close(baseline.get(name), item.get(name))
                else:
                    same = baseline.get(name) == item.get(name)
                if not same:
                    errors.append(f"cross-solver mismatch {name}: {baseline.get(name)!r} != {item.get(name)!r}")
    report = {
        "checked_at": utc_now(), "run_class": "smoke", "solver_count": len(results),
        "cross_validation_passed": not errors, "errors": errors,
        "ranking_performed": False, "formal_summary_included": False,
    }
    atomic_write_json(output_root / "smoke_cross_validation.json", report)
    return report


def summarize_budget_pilot(
    protocol: Mapping[str, Any], results: Sequence[Mapping[str, Any]],
    output_root: Path, preflight: bool,
) -> Dict[str, Any]:
    run_class = "budget_pilot_preflight" if preflight else "budget_pilot"
    rows: List[Dict[str, Any]] = []
    for item in results:
        raw_realized = item.get("realized_objective_evaluations")
        realized = int(raw_realized) if raw_realized is not None else None
        algorithm_time = float(item.get("algorithm_time") or 0.0)
        rows.append({
            "solver": item.get("solver_name"),
            "fitness": item.get("fitness"),
            "makespan": item.get("makespan"),
            "load_imbalance": item.get("load_imbalance"),
            "total_idle_distance": item.get("total_idle_distance"),
            "requested_objective_evaluations": item.get("requested_objective_evaluations"),
            "realized_objective_evaluations": realized,
            "objective_budget_exhausted": item.get("objective_budget_exhausted"),
            "stop_reason": item.get("stop_reason"),
            "algorithm_time": algorithm_time,
            "solver_wall_clock_time": item.get("solver_wall_clock_time"),
            "postprocess_time": item.get("postprocess_time"),
            "total_run_wall_clock_time": item.get("total_run_wall_clock_time"),
            "evaluations_per_second": item.get("evaluations_per_second"),
            "seconds_per_evaluation": item.get("seconds_per_evaluation"),
            "output_cross_validation_status": item.get("run_status"),
            "frozen_profile_hash": item.get("frozen_profile_hash"),
            "run_directory": item.get("run_directory"),
        })
    solver_order = {ADAPTERS[key].solver_name: index for index, key in enumerate(protocol["official_solvers"])}
    rows.sort(key=lambda row: solver_order.get(str(row.get("solver")), len(solver_order)))
    successful = len(rows) == len(protocol["official_solvers"]) and all(
        item.get("run_status") == "success" for item in results
    )
    exact = successful and all(
        row["realized_objective_evaluations"] is not None
        and row["realized_objective_evaluations"] == row["requested_objective_evaluations"]
        and row["objective_budget_exhausted"] is True
        and row["stop_reason"] == "objective_budget"
        for row in rows
    )
    max_wall = max((float(row["solver_wall_clock_time"] or 0.0) for row in rows), default=0.0)
    if preflight:
        projected = {
            str(row["solver"]): float(row["solver_wall_clock_time"] or 0.0) * 25.0
            for row in rows
        }
        summary = {
            "run_class": run_class, "instance": "w30", "solver_seed": 42,
            "requested_objective_evaluations": 2000,
            "all_runs_successful": successful, "all_budgets_exact": exact,
            "projected_wall_clock_at_50000": projected,
            "projected_max_wall_clock_at_50000": max(projected.values(), default=0.0),
            "preflight_deterministic_blocker": (not exact),
            "runs": rows, "ranking_performed": False,
        }
        atomic_write_csv(output_root / "three_solver_w30_preflight_runs.csv", rows)
        atomic_write_json(output_root / "three_solver_w30_preflight_summary.json", summary)
        return summary

    requested_values = {
        int(row["requested_objective_evaluations"])
        for row in rows if row.get("requested_objective_evaluations") is not None
    }
    uniform_common_budget = len(rows) == len(protocol["official_solvers"]) and len(requested_values) == 1
    requested_budget = next(iter(requested_values)) if uniform_common_budget else None
    safety_margin = 21600.0 / max_wall if max_wall > 0 else math.inf
    recommended_budget = requested_budget if uniform_common_budget else None
    if uniform_common_budget and (not exact or safety_margin < 1.5):
        projected_at_50000 = max_wall * 50000.0 / max(int(requested_budget), 1)
        candidate = math.floor((50000.0 * 21600.0 / (1.5 * max(projected_at_50000, 1e-12))) / 1000.0) * 1000
        recommended_budget = min(50000, max(5000, int(candidate)))
    calibrated_timeout = int(math.ceil(max(21600.0, 1.5 * max_wall + 300.0) / 300.0) * 300)
    completed = bool(uniform_common_budget and exact and recommended_budget == requested_budget)
    summary = {
        "run_class": run_class, "instance": "w30", "solver_seed": 42,
        "common_requested_budget": requested_budget,
        "observed_requested_budgets": sorted(requested_values),
        "uniform_common_budget": uniform_common_budget,
        "all_runs_successful": successful, "all_budgets_exact": exact,
        "max_observed_solver_wall_clock_time": max_wall,
        "safety_margin_ratio_21600_over_max_wall": safety_margin,
        "recommended_primary_budget": recommended_budget,
        "recommended_wall_clock_safety_timeout": calibrated_timeout,
        "w30_budget_pilot_completed": completed,
        "remaining_scale_risk_for_w45_w60": "unresolved; independent scale pilots are required",
        "runs": rows, "ranking_performed": False,
    }
    atomic_write_json(output_root / "three_solver_w30_pilot_summary.json", summary)
    csv_path = output_root / "three_solver_w30_pilot_runs.csv"
    with csv_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]) if rows else ["solver"])
        writer.writeheader(); writer.writerows(rows)
    trace_rows: List[Dict[str, Any]] = []
    for item in results:
        for record in item.get("checkpoint_trace", []) or []:
            trace_rows.append({"solver": item.get("solver_name"), **record})
    trace_path = output_root / "three_solver_w30_checkpoint_trace.csv"
    fields = ["solver", "objective_evaluation_count", "best_fitness", "best_makespan",
              "best_load_imbalance", "best_total_idle_distance", "elapsed_algorithm_time"]
    with trace_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields); writer.writeheader(); writer.writerows(trace_rows)
    report_lines = [
        "# Three-Solver W30 Evaluation-Budget Pilot Report", "",
        "This report calibrates execution budget only; it contains no algorithm-quality ranking.", "",
        "## Output audit", "",
        "| Solver | Previously available | Added in this stage | Runner reconstruction |",
        "|---|---|---|---|",
    ]
    for row in rows:
        report_lines.append(
            f"| {row['solver']} | system metrics, route IDs/directions, algorithm timer | "
            "standard solution.robots, system/runtime blocks, flattened robot CSV fields | "
            f"{row['output_cross_validation_status']} |"
        )
    report_lines.extend(["", "## Pilot runs", "",
        "| Solver | Requested | Realized | Stop reason | Algorithm s | Solver wall s | Postprocess s | Total wall s | Eval/s |",
        "|---|---:|---:|---|---:|---:|---:|---:|---:|",
    ])
    for row in rows:
        report_lines.append(
            f"| {row['solver']} | {row['requested_objective_evaluations']} | {row['realized_objective_evaluations']} | "
            f"{row['stop_reason']} | {float(row['algorithm_time'] or 0):.6f} | "
            f"{float(row['solver_wall_clock_time'] or 0):.6f} | {float(row['postprocess_time'] or 0):.6f} | "
            f"{float(row['total_run_wall_clock_time'] or 0):.6f} | {float(row['evaluations_per_second'] or 0):.6f} |"
        )
    report_lines.extend(["", "## Raw scientific metrics (no ranking)", "",
        "| Solver | Fitness | Makespan | Load imbalance | Total idle distance |",
        "|---|---:|---:|---:|---:|",
    ])
    for row in rows:
        report_lines.append(
            f"| {row['solver']} | {float(row['fitness']):.12g} | {float(row['makespan']):.12g} | "
            f"{float(row['load_imbalance']):.12g} | {float(row['total_idle_distance']):.12g} |"
        )
    report_lines.extend(["", "## Common conclusion", "",
        f"- Recommended common primary budget: `{recommended_budget}` evaluations.",
        f"- Uniform common budget was executed: `{str(uniform_common_budget).lower()}`.",
        f"- Recommended wall-clock safety timeout: `{calibrated_timeout}` seconds.",
        f"- W30 budget pilot completed: `{str(completed).lower()}`.",
        "- W45/W60 timing remains uncalibrated and must not be inferred from this W30 run.",
        "- `formal_run_approved` remains false.", "",
    ])
    atomic_write_text(output_root / "THREE_SOLVER_W30_BUDGET_PILOT_REPORT.md", "\n".join(report_lines) + "\n")
    return summary


def exact_sign_test(differences: Sequence[float]) -> float:
    signs = [value for value in differences if abs(value) > 1e-12]
    n = len(signs)
    if n == 0:
        return 1.0
    positives = sum(value > 0 for value in signs)
    tail = min(positives, n - positives)
    probability = 2.0 * sum(math.comb(n, k) for k in range(tail + 1)) / (2 ** n)
    return min(1.0, probability)


def holm_adjust(pairs: List[Dict[str, Any]]) -> None:
    ordered = sorted(enumerate(pairs), key=lambda item: item[1]["p_value"])
    running = 0.0
    total = len(ordered)
    for rank, (original_index, item) in enumerate(ordered):
        adjusted = min(1.0, (total - rank) * item["p_value"])
        running = max(running, adjusted)
        pairs[original_index]["holm_adjusted_p"] = running


def summarize_formal(output_root: Path, expected_protocol_hash: str, protocol: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    rows = []
    for path in (output_root / "formal").glob("**/unified_metrics.json") if (output_root / "formal").exists() else []:
        item = read_json(path)
        official_names = ({ADAPTERS[key].solver_name for key in protocol["official_solvers"]}
                          if protocol is not None else None)
        if (
            item.get("run_status") == "success"
            and item.get("run_class") == "formal"
            and item.get("normalization_mode") == OFFICIAL_MODE
            and item.get("protocol_hash") == expected_protocol_hash
            and (official_names is None or item.get("solver_name") in official_names)
        ):
            rows.append(item)
    grouped: Dict[tuple, List[Dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault((row["instance_hash"], row["solver_name"]), []).append(row)
    descriptive = []
    for (instance_hash, solver), items in sorted(grouped.items()):
        record: Dict[str, Any] = {"instance_hash": instance_hash, "solver_name": solver, "count": len(items)}
        for field_name in ("fitness", "makespan", "load_imbalance", "total_idle_distance", "algorithm_time"):
            values = [float(item[field_name]) for item in items if to_float(item.get(field_name)) is not None]
            if values:
                record[field_name] = {
                    "count": len(values), "mean": statistics.mean(values),
                    "sample_standard_deviation": statistics.stdev(values) if len(values) > 1 else 0.0,
                    "median": statistics.median(values), "minimum": min(values), "maximum": max(values),
                }
        descriptive.append(record)
    comparisons = []
    for instance_hash in sorted({row["instance_hash"] for row in rows}):
        subset = [row for row in rows if row["instance_hash"] == instance_hash]
        by_solver_seed = {(row["solver_name"], row["solver_seed"]): row for row in subset}
        solvers = sorted({row["solver_name"] for row in subset})
        for left, right in itertools.combinations(solvers, 2):
            common = sorted({seed for solver, seed in by_solver_seed if solver == left}.intersection(
                {seed for solver, seed in by_solver_seed if solver == right}))
            differences = [by_solver_seed[(left, seed)]["fitness"] - by_solver_seed[(right, seed)]["fitness"] for seed in common]
            if not differences:
                continue
            std = statistics.stdev(differences) if len(differences) > 1 else 0.0
            comparisons.append({
                "instance_hash": instance_hash, "left": left, "right": right, "paired_count": len(differences),
                "mean_difference": statistics.mean(differences), "median_difference": statistics.median(differences),
                "paired_standardized_mean_difference": statistics.mean(differences) / std if std > 0 else None,
                "p_value": exact_sign_test(differences), "test": "exact_two_sided_sign_test",
            })
    holm_adjust(comparisons)
    report = {
        "generated_at": utc_now(), "formal_valid_run_count": len(rows),
        "protocol_hash": expected_protocol_hash,
        "normalization_mode": OFFICIAL_MODE,
        "descriptive": descriptive, "paired_comparisons": comparisons,
        "scientific_ranking_conclusion": None,
    }
    atomic_write_json(output_root / "formal_summary.json", report)
    return report


def lightweight_self_check(protocol: Mapping[str, Any], protocol_path: Path, python_executable: str) -> None:
    with tempfile.TemporaryDirectory(prefix="mrta_unified_runner_") as temporary:
        output_root = Path(temporary) / "outputs"
        errors = validate_protocol(protocol, protocol_path, output_root)
        if errors:
            raise AssertionError(errors)
        namespace = argparse.Namespace(instances=["w30"], solvers=None, seeds=None,
                                      ablation_pop_size=None, ablation_max_gen=None,
                                      scale_budget=None, selected_evaluation_budget=None)
        smoke_specs = build_matrix(protocol, "smoke", namespace)
        assert len(smoke_specs) == 3
        for spec in smoke_specs:
            command = ADAPTERS[spec.solver].build(protocol, spec, python_executable, output_root / spec.solver)
            assert command[0] == python_executable and "--instance-path" in command
            assert "--max-objective-evaluations" in command
            assert "--normalization-mode" in command and OFFICIAL_MODE in command
            assert not any(flag in command for flag in ("--ref-makespan", "--ref-load", "--ref-distance", "--reference-makespan"))
        ablation = build_matrix(protocol, "gaaco_ablation", namespace)
        assert len(ablation) == 6
        assert {spec.variant for spec in ablation} == set(protocol["gaaco_ablation"]["methods"])
        atomic_write_json(output_root / "atomic.json", {"ok": True})
        assert read_json(output_root / "atomic.json") == {"ok": True}
        tampered = copy.deepcopy(protocol)
        tampered["instances"]["w30"]["instance_hash"] = "0" * 64
        tampered_path = Path(temporary) / "tampered_protocol.json"
        atomic_write_json(tampered_path, tampered)
        tampered_errors = validate_protocol(tampered, tampered_path, output_root)
        assert any("w30: instance hash mismatch" in item for item in tampered_errors), tampered_errors
        tampered_scale = copy.deepcopy(protocol)
        tampered_scale["instances"]["w30"]["normalization"]["scale"]["makespan"] *= 1.01
        atomic_write_json(tampered_path, tampered_scale)
        scale_errors = validate_protocol(tampered_scale, tampered_path, output_root)
        assert any("normalization" in item and "mismatch" in item for item in scale_errors), scale_errors
        old_protocol = copy.deepcopy(protocol); old_protocol["protocol_version"] = "1.0.0"
        atomic_write_json(tampered_path, old_protocol)
        old_errors = validate_protocol(old_protocol, tampered_path, output_root)
        assert "protocol_version must be 2.6.0" in old_errors
        namespace.instances = ["w45"]
        namespace.seeds = [42]
        scale_specs = build_matrix(protocol, "scale_preflight", namespace)
        assert len(scale_specs) == 3
        assert all(spec.budget_value == 2000 and spec.seed == 42 for spec in scale_specs)
        assert [spec.solver for spec in scale_specs] == protocol["official_solvers"]
    print("Unified runner lightweight self-check passed")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Unified auditable MRTA three-solver experiment runner")
    mode = parser.add_mutually_exclusive_group(required=False)
    mode.add_argument("--validate-protocol", action="store_true")
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--smoke", action="store_true")
    mode.add_argument("--formal", action="store_true")
    mode.add_argument("--budget-pilot", action="store_true")
    mode.add_argument("--scale-preflight", action="store_true")
    mode.add_argument("--scale-pilot", action="store_true")
    mode.add_argument("--postprocess-collision-audit", action="store_true")
    mode.add_argument("--generate-formal-manifest", action="store_true")
    mode.add_argument("--abma-optimization-benchmark", action="store_true")
    mode.add_argument("--abma-validation-benchmark", action="store_true")
    mode.add_argument("--abma-profile", action="store_true")
    mode.add_argument("--abma-exact-equivalence-benchmark", action="store_true")
    mode.add_argument("--abma-development-benchmark", action="store_true")
    mode.add_argument("--self-check", action="store_true")
    parser.add_argument("--gaaco-ablation", action="store_true", help="Use the isolated migrated six-method GA+ACO ablation matrix")
    parser.add_argument("--preflight", action="store_true", help="With --budget-pilot, use the fixed 2,000-evaluation preflight budget")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--protocol", default=str(DEFAULT_PROTOCOL))
    parser.add_argument("--python-executable", default=sys.executable)
    parser.add_argument("--output-root")
    parser.add_argument("--solvers", nargs="+", choices=sorted(ADAPTERS))
    parser.add_argument("--instances", nargs="+", choices=("w30", "w45", "w60"))
    parser.add_argument("--seeds", nargs="+", type=int)
    parser.add_argument("--with-collision-audit", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument("--ablation-pop-size", type=int)
    parser.add_argument("--ablation-max-gen", type=int)
    parser.add_argument("--benchmark-budget", type=int, default=2000)
    parser.add_argument("--scale-budget", type=int)
    parser.add_argument("--collision-input-root", nargs="+")
    parser.add_argument("--selected-evaluation-budget", type=int)
    parser.add_argument("--selected-timeout", type=float)
    parser.add_argument("--abma-benchmark-variants", nargs="+", choices=(
        "legacy", "legacy_exact_fast", "exact_fast_incremental_multifidelity",
        "incremental_only", "multifidelity_only", "incremental_multifidelity"
    ), help="Development recheck subset; omitted runs the required four-variant matrix")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    protocol_path = Path(args.protocol)
    if not protocol_path.is_absolute():
        protocol_path = ROOT / protocol_path
    protocol = read_json(protocol_path)
    output_root = Path(args.output_root or protocol["outputs"]["default_root"])
    if not output_root.is_absolute():
        output_root = ROOT / output_root
    if args.budget_pilot and args.output_root is None:
        output_root = output_root / f"protocol_{protocol_hash(protocol_path)}"
    if (args.scale_preflight or args.scale_pilot or args.postprocess_collision_audit or args.generate_formal_manifest) and args.output_root is None:
        output_root = output_root / f"protocol_{protocol_hash(protocol_path)}"
    if args.abma_optimization_benchmark and args.output_root is None:
        output_root = output_root / f"protocol_{protocol_hash(protocol_path)}"
    if args.abma_validation_benchmark and args.output_root is None:
        output_root = output_root / f"protocol_{protocol_hash(protocol_path)}"
    if (args.abma_profile or args.abma_exact_equivalence_benchmark or args.abma_development_benchmark) and args.output_root is None:
        output_root = output_root / f"protocol_{protocol_hash(protocol_path)}"
    errors = validate_protocol(protocol, protocol_path, output_root)
    if args.validate_protocol:
        print(json.dumps({"valid": not errors, "errors": errors, "protocol_hash": protocol_hash(protocol_path)}, ensure_ascii=False, indent=2))
        return 0 if not errors else 2
    if errors:
        print(json.dumps({"run_status": "protocol_validation_failed", "errors": errors}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 2
    if args.self_check:
        lightweight_self_check(protocol, protocol_path, args.python_executable)
        return 0
    if args.postprocess_collision_audit:
        if not args.collision_input_root:
            raise SystemExit("--postprocess-collision-audit requires one or more --collision-input-root paths")
        roots = [Path(item) if Path(item).is_absolute() else ROOT / item for item in args.collision_input_root]
        output_root.mkdir(parents=True, exist_ok=True)
        report = run_collision_postprocess(protocol, roots, output_root)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["all_required_runs_present"] and report["all_audits_completed_without_failure"] and report["all_raw_files_unchanged"] else 4
    if args.generate_formal_manifest:
        output_root.mkdir(parents=True, exist_ok=True)
        report = generate_formal_manifest(protocol, protocol_path, args.python_executable, output_root)
        print(json.dumps({"manifest_only": True, "run_count": report["run_count"],
                          "protocol_hash": report["protocol_hash"]}, ensure_ascii=False, indent=2))
        return 0
    if args.gaaco_ablation and args.smoke:
        raise SystemExit("--gaaco-ablation cannot be combined with --smoke")
    if args.formal and args.gaaco_ablation:
        raise SystemExit("--formal and --gaaco-ablation are separate run classes")
    if args.formal:
        gates = protocol["approval_gates"]
        unresolved = [name for name, value in gates.items() if not value]
        if not protocol.get("formal_run_approved") or unresolved:
            print(json.dumps({
                "run_status": "protocol_validation_failed",
                "reason": "formal execution is not approved",
                "formal_run_approved": protocol.get("formal_run_approved"),
                "unresolved_approval_gates": unresolved,
            }, ensure_ascii=False, indent=2), file=sys.stderr)
            return 3
    if args.preflight and not args.budget_pilot:
        raise SystemExit("--preflight requires --budget-pilot")
    if args.scale_budget is not None and not args.scale_pilot:
        raise SystemExit("--scale-budget requires --scale-pilot")
    if args.gaaco_ablation:
        matrix_mode = "gaaco_ablation"
    elif args.budget_pilot:
        matrix_mode = "budget_pilot_preflight" if args.preflight else "budget_pilot"
    elif args.scale_preflight:
        matrix_mode = "scale_preflight"
    elif args.scale_pilot:
        matrix_mode = "scale_pilot"
    elif args.abma_optimization_benchmark:
        matrix_mode = "abma_optimization_benchmark"
    elif args.abma_validation_benchmark:
        matrix_mode = "abma_validation_benchmark"
    elif args.abma_profile:
        matrix_mode = "abma_profile"
    elif args.abma_exact_equivalence_benchmark:
        matrix_mode = "abma_exact_equivalence_benchmark"
    elif args.abma_development_benchmark:
        matrix_mode = "abma_development_benchmark"
    elif args.smoke:
        matrix_mode = "smoke"
    else:
        matrix_mode = "formal"
    specs = build_matrix(protocol, matrix_mode, args)
    if args.dry_run:
        dry_run(protocol, specs, args.python_executable, output_root)
        return 0
    if not (args.smoke or args.formal or args.gaaco_ablation or args.budget_pilot or args.scale_preflight or args.scale_pilot or args.abma_optimization_benchmark or args.abma_validation_benchmark or args.abma_profile or args.abma_exact_equivalence_benchmark or args.abma_development_benchmark):
        print("choose a validation, dry-run, smoke, pilot, manifest, formal, ablation, or self-check mode", file=sys.stderr)
        return 2
    collision_enabled = args.with_collision_audit
    if collision_enabled is None:
        collision_enabled = bool(protocol["collision_audit"].get(
            "enabled_for_smoke" if matrix_mode == "smoke" else "enabled_for_formal", False
        )) and matrix_mode not in ("gaaco_ablation", "budget_pilot_preflight", "budget_pilot", "scale_preflight", "scale_pilot", "abma_optimization_benchmark", "abma_validation_benchmark", "abma_profile", "abma_exact_equivalence_benchmark", "abma_development_benchmark")
    output_root.mkdir(parents=True, exist_ok=True)
    results = [
        execute_spec(protocol, protocol_path, spec, args.python_executable, output_root, args.resume, collision_enabled, args.selected_timeout)
        for spec in specs
    ]
    if matrix_mode == "smoke":
        report = smoke_cross_validation(protocol, results, output_root)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["cross_validation_passed"] else 4
    if matrix_mode in ("budget_pilot_preflight", "budget_pilot"):
        report = summarize_budget_pilot(
            protocol, results, output_root, matrix_mode == "budget_pilot_preflight"
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report.get("all_runs_successful") and report.get("all_budgets_exact") else 4
    if matrix_mode in ("scale_preflight", "scale_pilot"):
        report = summarize_scale_calibration(protocol, results, output_root, matrix_mode)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report.get("all_runs_successful") and report.get("all_budgets_exact") else 4
    if matrix_mode == "abma_profile":
        report = summarize_abma_profile(results, output_root)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["all_runs_successful"] else 4
    if matrix_mode in ("abma_exact_equivalence_benchmark", "abma_development_benchmark", "abma_validation_benchmark"):
        report = summarize_abma_paired(results, output_root, matrix_mode)
        if matrix_mode != "abma_validation_benchmark":
            atomic_write_json(output_root / f"{matrix_mode.upper()}_SUMMARY.json", report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        passed = (report.get("validation_gate_passed") if matrix_mode == "abma_validation_benchmark"
                  else report["all_runs_successful"] and report["all_exact_equivalent"])
        return 0 if passed else 4
    if matrix_mode == "abma_optimization_benchmark":
        successful = [item for item in results if item.get("run_status") == "success"]
        rows = [{
            "variant": item.get("variant"), "seed": item.get("solver_seed"),
            "fitness": item.get("fitness"), "algorithm_time": item.get("algorithm_time"),
            "wall_time": item.get("wall_time"),
            "objective_evaluation_count": item.get("objective_evaluation_count"),
        } for item in successful]
        report = {"run_class": matrix_mode, "formal": False, "rows": rows,
                  "all_runs_successful": len(successful) == len(results)}
        name = ("ABMA_OPTIMIZATION_BENCHMARK_SUMMARY.json" if matrix_mode == "abma_optimization_benchmark"
                else "ABMA_VALIDATION_BENCHMARK_SUMMARY.json")
        atomic_write_json(output_root / name, report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["all_runs_successful"] else 4
    if matrix_mode == "formal":
        summarize_formal(output_root, protocol_hash(protocol_path), protocol)
    return 0 if all(item.get("run_status") == "success" for item in results) else 4


if __name__ == "__main__":
    raise SystemExit(main())
