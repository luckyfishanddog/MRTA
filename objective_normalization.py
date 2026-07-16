"""Shared objective normalization and deterministic baseline construction.

This module deliberately contains no solver imports.  It is the single public
implementation used by all four solvers and by the unified experiment runner.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple

import generate_welds
from mrta_problem_core import MotionModel, assign_welds_to_robots_split, evaluate_order


OFFICIAL_MODE = "ideal_baseline_range_v1"
LEGACY_MODE = "legacy_ratio_refs_v1"
BASELINE_ALGORITHM = "workload_balanced_cut_cheapest_insertion_2opt_dp_v1"
BASELINE_VERSION = "1.0.0"
DEFAULT_WEIGHTS = (0.7, 0.2, 0.1)
_ABS_TOL = 1e-10


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _finite(value: Any, name: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


def _metric_dict(value: Mapping[str, Any], name: str) -> Dict[str, float]:
    aliases = {
        "makespan": ("makespan", "C"),
        "load_imbalance": ("load_imbalance", "load", "B"),
        "idle_distance": ("idle_distance", "distance", "D"),
    }
    result: Dict[str, float] = {}
    for canonical, keys in aliases.items():
        found = next((value[key] for key in keys if key in value), None)
        if found is None:
            raise ValueError(f"{name}.{canonical} is required")
        result[canonical] = _finite(found, f"{name}.{canonical}")
    return result


@dataclass(frozen=True)
class DeterministicBaselineResult:
    algorithm: str
    algorithm_version: str
    instance_hash: str
    scientific_model_hash: str
    boundaries: Dict[str, float]
    metrics: Dict[str, float]
    robot_total_times: List[float]
    routes: List[List[str]]
    direction_flags: List[List[bool]]
    assignment_stats: Dict[str, Any]
    baseline_hash: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class NormalizationSpec:
    mode: str
    ideal: Dict[str, float]
    baseline: Dict[str, Any]
    scale: Dict[str, float]
    floors: Dict[str, float]
    weights: Tuple[float, float, float] = DEFAULT_WEIGHTS

    def to_dict(self) -> Dict[str, Any]:
        value = asdict(self)
        value["weights"] = list(self.weights)
        return value


def compute_theoretical_ideal_point(
    welds: Sequence[Any], model: MotionModel
) -> Dict[str, float]:
    """Return the relaxation ideal: equal pure weld work and no empty travel."""
    model.validate()
    total_original_weld_time = sum(float(weld.length) for weld in welds) / model.weld_speed
    return {
        "makespan": total_original_weld_time / 4.0,
        "load_imbalance": 0.0,
        "idle_distance": 0.0,
    }


def _weld_key(weld: Any) -> Tuple[Any, ...]:
    return (
        str(weld.id),
        *(round(float(v), 12) for v in (*weld.start_point(), *weld.end_point())),
    )


def _candidate_breakpoints(welds: Sequence[Any], upper: bool) -> List[float]:
    """Collect exact x breakpoints induced by endpoints and the fixed y cut."""
    y_cut = float(generate_welds.PLATFORM_H_M) / 2.0
    width = float(generate_welds.PLATFORM_W_M)
    values = {0.0, width / 2.0, width}
    for weld in welds:
        x1, y1, _ = weld.start_point()
        x2, y2, _ = weld.end_point()
        if (upper and max(y1, y2) >= y_cut - _ABS_TOL) or (
            not upper and min(y1, y2) <= y_cut + _ABS_TOL
        ):
            values.add(min(width, max(0.0, float(x1))))
            values.add(min(width, max(0.0, float(x2))))
        dy = float(y2) - float(y1)
        if abs(dy) > _ABS_TOL and min(y1, y2) < y_cut < max(y1, y2):
            t = (y_cut - float(y1)) / dy
            values.add(min(width, max(0.0, float(x1) + t * (float(x2) - float(x1)))))
    return sorted(values)


def _half_balance(
    welds: Sequence[Any], model: MotionModel, x: float, upper: bool
) -> Tuple[float, int]:
    x_up, x_low = (x, float(generate_welds.PLATFORM_W_M) / 2.0) if upper else (
        float(generate_welds.PLATFORM_W_M) / 2.0,
        x,
    )
    robots, stats = assign_welds_to_robots_split(welds, x_up, x_low)
    left, right = (0, 1) if upper else (2, 3)
    left_work = sum(float(w.length) for w in robots[left]) / model.weld_speed
    right_work = sum(float(w.length) for w in robots[right]) / model.weld_speed
    task_diff = abs(int(stats[f"robot_{left}_task_count"]) - int(stats[f"robot_{right}_task_count"]))
    return left_work - right_work, task_diff


def _choose_balanced_cut(
    welds: Sequence[Any], model: MotionModel, upper: bool
) -> float:
    breakpoints = _candidate_breakpoints(welds, upper)
    candidates = set(breakpoints)
    for left, right in zip(breakpoints[:-1], breakpoints[1:]):
        if right - left <= _ABS_TOL:
            continue
        candidates.add((left + right) / 2.0)
        f_left = _half_balance(welds, model, left, upper)[0]
        f_right = _half_balance(welds, model, right, upper)[0]
        if f_left == 0.0 or f_right == 0.0 or f_left * f_right > 0.0:
            continue
        lo, hi = left, right
        flo = f_left
        for _ in range(80):
            mid = (lo + hi) / 2.0
            fmid = _half_balance(welds, model, mid, upper)[0]
            if abs(fmid) <= _ABS_TOL:
                lo = hi = mid
                break
            if flo * fmid <= 0.0:
                hi = mid
            else:
                lo, flo = mid, fmid
        candidates.add((lo + hi) / 2.0)

    best_x = 0.0
    best_score: Tuple[float, int, float, float] | None = None
    for x in sorted(candidates):
        difference, task_difference = _half_balance(welds, model, x, upper)
        score = (abs(difference), task_difference, abs(x - 10.0), x)
        if best_score is None or score < best_score:
            best_score, best_x = score, x
    return float(best_x)


def _route_time(route: Sequence[Any], model: MotionModel) -> Tuple[float, Dict[str, Any]]:
    return evaluate_order(route, list(range(len(route))), model)


def _build_route(tasks: Sequence[Any], model: MotionModel) -> Tuple[List[Any], Dict[str, Any]]:
    remaining = sorted(tasks, key=_weld_key)
    route: List[Any] = []
    while remaining:
        best: Tuple[float, Tuple[Any, ...], int, Any, List[Any]] | None = None
        for task in remaining:
            for position in range(len(route) + 1):
                candidate = route[:position] + [task] + route[position:]
                cost, _ = _route_time(candidate, model)
                item = (cost, _weld_key(task), position, task, candidate)
                if best is None or item[:3] < best[:3]:
                    best = item
        assert best is not None
        route = best[4]
        remaining.remove(best[3])

    current_cost, current_stats = _route_time(route, model)
    improved = True
    while improved:
        improved = False
        for i in range(len(route)):
            for j in range(i + 1, len(route)):
                candidate = route[:i] + list(reversed(route[i : j + 1])) + route[j + 1 :]
                cost, stats = _route_time(candidate, model)
                if cost < current_cost - _ABS_TOL:
                    route, current_cost, current_stats = candidate, cost, stats
                    improved = True
                    break
            if improved:
                break
    return route, current_stats


def _scientific_model_payload(model: MotionModel) -> Dict[str, Any]:
    return {
        "weld_speed": model.weld_speed,
        "travel_speed": model.travel_speed,
        "acceleration": model.acceleration,
        "safe_z": model.safe_z,
        "platform_width": float(generate_welds.PLATFORM_W_M),
        "platform_height": float(generate_welds.PLATFORM_H_M),
        "fixed_y_cut": float(generate_welds.PLATFORM_H_M) / 2.0,
        "time_model": "corrected",
        "assignment_mode": "split",
        "split_timing_mode": "parent_aware_zero_setup_post_equivalent",
        "direction_mode": "bidirectional",
        "route_type": "open",
    }


def build_deterministic_baseline(
    welds: Sequence[Any],
    model: MotionModel,
    instance_hash: str | None = None,
) -> DeterministicBaselineResult:
    """Construct the common, solver-independent deterministic baseline."""
    model.validate()
    ordered_welds = sorted(welds, key=_weld_key)
    if instance_hash is None:
        instance_hash = generate_welds.compute_weld_instance_hash(list(ordered_welds))
    x_up = _choose_balanced_cut(ordered_welds, model, True)
    x_low = _choose_balanced_cut(ordered_welds, model, False)
    robots, assignment_stats = assign_welds_to_robots_split(ordered_welds, x_up, x_low)
    if int(assignment_stats["unassigned_subweld_count"]) != 0:
        raise ValueError("deterministic baseline produced unassigned subwelds")

    route_ids: List[List[str]] = []
    directions: List[List[bool]] = []
    totals: List[float] = []
    distances: List[float] = []
    for tasks in robots:
        route, stats = _build_route(tasks, model)
        route_ids.append([str(task.id) for task in route])
        directions.append([bool(value) for value in stats["direction_flags"]])
        totals.append(float(stats["total_time"]))
        distances.append(float(stats["total_idle_distance"]))

    metrics = {
        "makespan": max(totals, default=0.0),
        "load_imbalance": max(totals, default=0.0) - min(totals, default=0.0),
        "idle_distance": sum(distances),
    }
    boundaries = {"fixed_y": float(generate_welds.PLATFORM_H_M) / 2.0, "x_up": x_up, "x_low": x_low}
    scientific_model_hash = _sha256_json(_scientific_model_payload(model))
    hash_payload = {
        "instance_hash": instance_hash,
        "scientific_model": _scientific_model_payload(model),
        "algorithm": BASELINE_ALGORITHM,
        "algorithm_version": BASELINE_VERSION,
        "boundaries": boundaries,
        "routes": route_ids,
        "direction_flags": directions,
        "metrics": metrics,
    }
    return DeterministicBaselineResult(
        algorithm=BASELINE_ALGORITHM,
        algorithm_version=BASELINE_VERSION,
        instance_hash=str(instance_hash),
        scientific_model_hash=scientific_model_hash,
        boundaries=boundaries,
        metrics=metrics,
        robot_total_times=totals,
        routes=route_ids,
        direction_flags=directions,
        assignment_stats=dict(assignment_stats),
        baseline_hash=_sha256_json(hash_payload),
    )


def compute_normalization_spec(
    welds: Sequence[Any],
    model: MotionModel,
    instance_hash: str | None = None,
    weights: Sequence[float] = DEFAULT_WEIGHTS,
) -> NormalizationSpec:
    ideal = compute_theoretical_ideal_point(welds, model)
    baseline_result = build_deterministic_baseline(welds, model, instance_hash)
    baseline = baseline_result.to_dict()
    diagonal = math.hypot(float(generate_welds.PLATFORM_W_M), float(generate_welds.PLATFORM_H_M))
    floors = {
        "epsilon_makespan": 1e-9 * max(ideal["makespan"], 1.0),
        "load_imbalance": 0.05 * ideal["makespan"],
        "epsilon_idle_distance": 1e-9 * max(diagonal, 1.0),
    }
    scale = {
        "makespan": max(baseline["metrics"]["makespan"] - ideal["makespan"], floors["epsilon_makespan"]),
        "load_imbalance": max(baseline["metrics"]["load_imbalance"], floors["load_imbalance"]),
        "idle_distance": max(baseline["metrics"]["idle_distance"], floors["epsilon_idle_distance"]),
    }
    spec = NormalizationSpec(
        mode=OFFICIAL_MODE,
        ideal=ideal,
        baseline=baseline,
        scale=scale,
        floors=floors,
        weights=tuple(float(value) for value in weights),  # type: ignore[arg-type]
    )
    validate_normalization_spec(spec)
    return spec


def validate_normalization_spec(spec: NormalizationSpec | Mapping[str, Any]) -> None:
    value = spec.to_dict() if isinstance(spec, NormalizationSpec) else dict(spec)
    if value.get("mode") != OFFICIAL_MODE:
        raise ValueError(f"normalization mode must be {OFFICIAL_MODE}")
    ideal = _metric_dict(value.get("ideal", {}), "ideal")
    scale = _metric_dict(value.get("scale", {}), "scale")
    baseline = value.get("baseline", {})
    if not isinstance(baseline, Mapping):
        raise ValueError("baseline must be an object")
    _metric_dict(baseline.get("metrics", {}), "baseline.metrics")
    if baseline.get("algorithm") != BASELINE_ALGORITHM:
        raise ValueError("unexpected deterministic baseline algorithm")
    if any(number < 0.0 for number in ideal.values()):
        raise ValueError("ideal values must be nonnegative")
    if any(number <= 0.0 for number in scale.values()):
        raise ValueError("all normalization scales must be positive and finite")
    weights = tuple(float(item) for item in value.get("weights", DEFAULT_WEIGHTS))
    if len(weights) != 3 or any(not math.isfinite(item) or item < 0.0 for item in weights):
        raise ValueError("three finite nonnegative weights are required")
    if not math.isclose(sum(weights), 1.0, rel_tol=0.0, abs_tol=1e-12):
        raise ValueError("objective weights must sum to one")


def normalized_components(
    metrics: Mapping[str, Any], spec: NormalizationSpec | Mapping[str, Any]
) -> Dict[str, float]:
    validate_normalization_spec(spec)
    value = spec.to_dict() if isinstance(spec, NormalizationSpec) else dict(spec)
    raw = _metric_dict(metrics, "metrics")
    ideal = _metric_dict(value["ideal"], "ideal")
    scale = _metric_dict(value["scale"], "scale")
    result: Dict[str, float] = {}
    for key in ("makespan", "load_imbalance", "idle_distance"):
        delta = raw[key] - ideal[key]
        tolerance = max(1e-10, 1e-10 * max(abs(raw[key]), abs(ideal[key]), 1.0))
        if delta < -tolerance:
            raise ValueError(f"{key}={raw[key]} is below theoretical ideal {ideal[key]}")
        result[key] = 0.0 if delta < 0.0 else delta / scale[key]
    return result


def normalized_objective(
    metrics: Mapping[str, Any], spec: NormalizationSpec | Mapping[str, Any]
) -> float:
    value = spec.to_dict() if isinstance(spec, NormalizationSpec) else dict(spec)
    components = normalized_components(metrics, value)
    weights = tuple(float(item) for item in value.get("weights", DEFAULT_WEIGHTS))
    return sum(weight * components[key] for weight, key in zip(weights, ("makespan", "load_imbalance", "idle_distance")))


def normalization_spec_hash(spec: NormalizationSpec | Mapping[str, Any]) -> str:
    validate_normalization_spec(spec)
    value = spec.to_dict() if isinstance(spec, NormalizationSpec) else dict(spec)
    return _sha256_json(value)

