"""Build deterministic ideal/baseline/range normalization for atomic instances."""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

from atomic_problem_core import event_pair_routes, load_atomic_instance, scientific_model_payload, sha256_json
from atomic_route_evaluator import AtomicRouteEvaluator, normalization_hash
from mrta_problem_core import DEFAULT_MOTION_MODEL


BASELINE_ALGORITHM = "atomic_event_workload_balance_cheapest_insertion_v1"


def _balanced_event(events, weld_by_id):
    def score(event):
        left = sum(weld_by_id[x].weld_time_s for x in event.left_atomic_ids)
        right = sum(weld_by_id[x].weld_time_s for x in event.right_atomic_ids)
        return abs(left - right), abs(len(event.left_atomic_ids) - len(event.right_atomic_ids)), abs(event.representative_x - 10.0), event.event_index
    return min(events, key=score)


def _cheapest_route(ids: Sequence[str], evaluator: AtomicRouteEvaluator) -> List[str]:
    route: List[str] = []
    for wid in sorted(ids):
        best = None
        for pos in range(len(route) + 1):
            candidate = route[:pos] + [wid] + route[pos:]
            value = evaluator.evaluate_route(candidate).total_time
            item = (value, pos, candidate)
            if best is None or item[:2] < best[:2]:
                best = item
        route = best[2]  # type: ignore[index]
    return route


def build_spec(instance_path: Path) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    instance = load_atomic_instance(instance_path)
    evaluator = AtomicRouteEvaluator(instance)
    upper = _balanced_event(instance.upper_events, instance.by_id)
    lower = _balanced_event(instance.lower_events, instance.by_id)
    assigned = event_pair_routes(upper, lower)
    routes = [_cheapest_route(ids, evaluator) for ids in assigned]
    result = evaluator.evaluate_system(routes)
    total_weld = sum(w.weld_time_s for w in instance.atomic_welds)
    ideal = {"makespan": total_weld / 4.0, "load_imbalance": 0.0, "idle_distance": 0.0}
    floors = {"epsilon_makespan": 1e-9 * max(ideal["makespan"], 1.0), "load_imbalance": 0.05 * ideal["makespan"], "epsilon_idle_distance": 1e-9 * math.hypot(20.0, 12.0)}
    raw = {"makespan": result.makespan, "load_imbalance": result.load_imbalance, "idle_distance": result.idle_distance}
    scale = {"makespan": max(raw["makespan"] - ideal["makespan"], floors["epsilon_makespan"]),
             "load_imbalance": max(raw["load_imbalance"], floors["load_imbalance"]),
             "idle_distance": max(raw["idle_distance"], floors["epsilon_idle_distance"])}
    baseline = {"algorithm": BASELINE_ALGORITHM, "algorithm_version": "1.0.0",
                "boundaries": {"upper_event_index": upper.event_index, "lower_event_index": lower.event_index,
                               "x_up": upper.representative_x, "x_low": lower.representative_x},
                "routes": routes, "direction_flags": [list(x) for x in result.direction_flags],
                "robot_total_times": list(result.robot_total_times), "metrics": raw,
                "baseline_hash": sha256_json({"events": [upper.assignment_hash, lower.assignment_hash], "routes": routes, "metrics": raw})}
    spec = {"mode": "ideal_baseline_range_v1", "instance": instance.name,
            "atomic_instance_hash": instance.atomic_instance_hash,
            "scientific_model": scientific_model_payload(DEFAULT_MOTION_MODEL),
            "scientific_model_hash": sha256_json(scientific_model_payload(DEFAULT_MOTION_MODEL)),
            "ideal": ideal, "baseline": baseline, "scale": scale, "floors": floors, "weights": [0.7, 0.2, 0.1]}
    return spec, {"instance": instance.name, "atomic_instance_hash": instance.atomic_instance_hash,
                  **{f"ideal_{k}": v for k, v in ideal.items()}, **{f"baseline_{k}": v for k, v in raw.items()},
                  **{f"scale_{k}": v for k, v in scale.items()}, "upper_event_index": upper.event_index,
                  "lower_event_index": lower.event_index, "normalization_hash": normalization_hash(spec)}


def run(output_path: Path = Path("config/normalization/atomic_normalization_spec.json")) -> Dict[str, Any]:
    root = Path("data/instances/atomic_l5_l1")
    specs: Dict[str, Any] = {}
    rows = []
    for name in ("w30", "w45", "w60"):
        spec, row = build_spec(root / f"instance_{name}_atomic.json")
        specs[name] = spec; rows.append(row)
    payload = {"version": "1.0.0", "mode": "ideal_baseline_range_v1", "instances": specs}
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    digest = normalization_hash(payload)
    output_path.with_suffix(output_path.suffix + ".sha256").write_text(digest + "\n", encoding="utf-8")
    with Path("atomic_normalization_baselines.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)
    return payload


if __name__ == "__main__":
    run()
