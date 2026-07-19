"""Build independent deterministic normalization for fixed-pre-split instances."""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

import mrta_problem_core as core
from objective_normalization import BASELINE_ALGORITHM, OFFICIAL_MODE
from MRTA_ABMA_FIXED_PRESPLIT import (
    FixedCacheContext,
    FixedRouteEvaluator,
    REFERENCE_VARIANT,
    SCIENTIFIC_MODEL_VERSION,
    _activate_fixed_evaluator,
)
from weld_fixed_presplit import assign_fixed_welds, load_fixed_instance, sha256_json


def _choose_boundary(fixed, candidates: Sequence[Dict[str, Any]], half: str,
                     other_x: float, weld_speed: float) -> float:
    best = None
    for item in candidates:
        x = float(item["x"])
        robots, _ = assign_fixed_welds(
            fixed, x if half == "upper" else other_x,
            x if half == "lower" else other_x,
        )
        left, right = (0, 1) if half == "upper" else (2, 3)
        difference = abs(
            sum(w.length for w in robots[left]) / weld_speed
            - sum(w.length for w in robots[right]) / weld_speed
        )
        score = (difference, abs(int(item["left_task_count"]) - int(item["right_task_count"])),
                 abs(x - 10.0), x)
        if best is None or score < best[0]:
            best = (score, x)
    if best is None:
        raise ValueError(f"no legal {half} normalization boundary")
    return float(best[1])


def _route(tasks, model: core.MotionModel) -> Tuple[List[int], Dict[str, Any]]:
    evaluator = FixedRouteEvaluator(tasks, model, True)
    route: List[int] = []
    remaining = list(range(len(tasks)))
    while remaining:
        best = None
        base = evaluator.evaluate(route)[0]
        for task in remaining:
            for position in range(len(route) + 1):
                candidate = route[:position] + [task] + route[position:]
                increment = evaluator.evaluate(candidate)[0] - base
                score = (increment, str(tasks[task].id), position)
                if best is None or score < best[0]:
                    best = (score, task, candidate)
        assert best is not None
        route = best[2]; remaining.remove(best[1])
    cost, stats = evaluator.evaluate(route)
    improved = True
    while improved:
        improved = False
        for i in range(len(route)):
            for j in range(i + 1, len(route)):
                candidate = route[:i] + list(reversed(route[i:j + 1])) + route[j + 1:]
                new_cost, new_stats = evaluator.evaluate(candidate)
                if new_cost < cost - 1e-10:
                    route, cost, stats = candidate, new_cost, new_stats
                    improved = True
                    break
            if improved:
                break
    return route, stats


def build_one(path: Path) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    fixed, metadata = load_fixed_instance(path)
    model = core.MotionModel()
    upper_candidates = metadata["boundary_candidates"]["upper"]["candidates"]
    lower_candidates = metadata["boundary_candidates"]["lower"]["candidates"]
    x_up = _choose_boundary(fixed, upper_candidates, "upper", float(lower_candidates[0]["x"]), model.weld_speed)
    x_low = _choose_boundary(fixed, lower_candidates, "lower", x_up, model.weld_speed)
    robots_fixed, assignment = assign_fixed_welds(fixed, x_up, x_low)
    robots = [[item.to_weld() for item in robot] for robot in robots_fixed]
    context = FixedCacheContext(REFERENCE_VARIANT, model)
    routes: List[List[str]] = []; directions: List[List[bool]] = []
    totals: List[float] = []; distances: List[float] = []
    with _activate_fixed_evaluator(context):
        for tasks in robots:
            order, stats = _route(tasks, model)
            routes.append([str(tasks[index].id) for index in order])
            directions.append([bool(value) for value in stats["direction_flags"]])
            totals.append(float(stats["total_time"])); distances.append(float(stats["total_idle_distance"]))
    total_weld_time = sum(w.weld_time_s for w in fixed)
    ideal = {"makespan": total_weld_time / 4.0, "load_imbalance": 0.0, "idle_distance": 0.0}
    metrics = {
        "makespan": max(totals, default=0.0),
        "load_imbalance": max(totals, default=0.0) - min(totals, default=0.0),
        "idle_distance": sum(distances),
    }
    model_payload = {
        "scientific_model_version": SCIENTIFIC_MODEL_VERSION,
        "preprocessing_hash": metadata["preprocessing_hash"],
        "motion": {"weld_speed": model.weld_speed, "travel_speed": model.travel_speed,
                   "acceleration": model.acceleration, "safe_z": model.safe_z},
        "assignment": "immutable_no_cross_fixed_presplit",
        "zero_transition_policy": "connected_endpoint_zero_no_lift_descent_v1",
        "direction_mode": "bidirectional", "route_type": "open",
    }
    baseline_payload = {
        "algorithm": BASELINE_ALGORITHM,
        "algorithm_version": "fixed-presplit-baseline-v1.0.0",
        "instance_hash": metadata["preprocessing_hash"],
        "scientific_model_hash": sha256_json(model_payload),
        "boundaries": {"fixed_y": 6.0, "x_up": x_up, "x_low": x_low},
        "metrics": metrics, "robot_total_times": totals,
        "routes": routes, "direction_flags": directions,
        "assignment_stats": assignment,
    }
    baseline_payload["baseline_hash"] = sha256_json(baseline_payload)
    diagonal = math.hypot(20.0, 12.0)
    floors = {
        "epsilon_makespan": 1e-9 * max(ideal["makespan"], 1.0),
        "load_imbalance": 0.05 * ideal["makespan"],
        "epsilon_idle_distance": 1e-9 * max(diagonal, 1.0),
    }
    scale = {
        "makespan": max(metrics["makespan"] - ideal["makespan"], floors["epsilon_makespan"]),
        "load_imbalance": max(metrics["load_imbalance"], floors["load_imbalance"]),
        "idle_distance": max(metrics["idle_distance"], floors["epsilon_idle_distance"]),
    }
    spec = {"mode": OFFICIAL_MODE, "ideal": ideal, "baseline": baseline_payload,
            "scale": scale, "floors": floors, "weights": [0.7, 0.2, 0.1],
            "normalization_scope": "fixed_presplit_independent_v1",
            "scientific_model_version": SCIENTIFIC_MODEL_VERSION,
            "preprocessing_hash": metadata["preprocessing_hash"]}
    row = {"instance": metadata["name"], "x_up": x_up, "x_low": x_low,
           **{f"ideal_{k}": v for k, v in ideal.items()},
           **{f"baseline_{k}": v for k, v in metrics.items()},
           **{f"scale_{k}": v for k, v in scale.items()},
           "baseline_hash": baseline_payload["baseline_hash"],
           "normalization_spec_hash": sha256_json(spec)}
    return spec, row


def main() -> None:
    result = {}; rows = []
    for name in ("w30", "w45", "w60"):
        spec, row = build_one(Path(f"data/instances/fixed_presplit/{name}_fixed_presplit.json"))
        result[name] = spec; rows.append(row)
    output = Path("config/normalization/fixed_presplit_normalization.json")
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    output.with_suffix(".sha256").write_text(sha256_json(result) + "\n", encoding="utf-8")
    with Path("config/normalization/fixed_presplit_normalization_baselines.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)
    print(json.dumps(rows, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
