"""Shared, solver-neutral result instrumentation.

This module only formats metrics that a solver has already computed.  It does
not participate in search, candidate generation, acceptance, or routing.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Sequence


REGION_NAMES = ("upper_left", "upper_right", "lower_left", "lower_right")
TRACE_CHECKPOINTS = (1000, 2000, 5000, 10000, 20000, 30000, 40000, 50000)
_TOL = 1e-8


def build_robot_metrics(
    route_task_ids: Sequence[Sequence[Any]],
    direction_flags: Sequence[Sequence[Any]],
    robot_stats: Sequence[Mapping[str, Any]],
) -> List[Dict[str, Any]]:
    if not (len(route_task_ids) == len(direction_flags) == len(robot_stats) == 4):
        raise ValueError("exactly four robot routes, direction lists, and stats are required")
    result: List[Dict[str, Any]] = []
    for robot_id in range(4):
        ids = [str(value) for value in route_task_ids[robot_id]]
        flags = [bool(value) for value in direction_flags[robot_id]]
        if len(ids) != len(flags):
            raise ValueError(f"robot {robot_id}: route_task_ids/direction_flags length mismatch")
        stats = robot_stats[robot_id]
        weld = float(stats.get("total_weld_time", 0.0))
        travel = float(stats.get("total_travel_time", 0.0))
        total = float(stats.get("total_time", 0.0))
        distance = float(stats.get("total_idle_distance", 0.0))
        values = (weld, travel, total, distance)
        if any(not math.isfinite(value) or value < -_TOL for value in values):
            raise ValueError(f"robot {robot_id}: invalid time or distance metric")
        if not math.isclose(total, weld + travel, rel_tol=1e-10, abs_tol=_TOL):
            raise ValueError(f"robot {robot_id}: total_time != total_weld_time + total_travel_time")
        if not ids and any(abs(value) > _TOL for value in values):
            raise ValueError(f"robot {robot_id}: empty route must have all-zero metrics")
        result.append({
            "robot_id": robot_id,
            "region_name": REGION_NAMES[robot_id],
            "task_count": len(ids),
            "route_task_ids": ids,
            "direction_flags": flags,
            "total_weld_time": weld,
            "total_travel_time": travel,
            "total_time": total,
            "total_idle_distance": distance,
        })
    return result


def build_system_metrics(robot_metrics: Sequence[Mapping[str, Any]], fitness: float) -> Dict[str, float]:
    if len(robot_metrics) != 4:
        raise ValueError("exactly four robot metrics are required")
    totals = [float(item["total_time"]) for item in robot_metrics]
    return {
        "makespan": max(totals, default=0.0),
        "load_imbalance": max(totals, default=0.0) - min(totals, default=0.0),
        "total_weld_time": sum(float(item["total_weld_time"]) for item in robot_metrics),
        "total_travel_time": sum(float(item["total_travel_time"]) for item in robot_metrics),
        "total_idle_distance": sum(float(item["total_idle_distance"]) for item in robot_metrics),
        "sum_robot_total_time": sum(totals),
        "fitness": float(fitness),
    }


def flatten_robot_metrics(robot_metrics: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for item in robot_metrics:
        robot_id = int(item["robot_id"])
        for field_name in (
            "total_weld_time", "total_travel_time", "total_time",
            "total_idle_distance", "task_count",
        ):
            result[f"robot_{robot_id}_{field_name}"] = item[field_name]
    result["sum_robot_total_time"] = sum(float(item["total_time"]) for item in robot_metrics)
    return result


def build_standard_result(
    solver_name: str,
    instance_hash: str,
    solver_seed: int,
    x_up: float,
    x_low: float,
    robot_metrics: Sequence[Mapping[str, Any]],
    system_metrics: Mapping[str, Any],
    algorithm_time: float,
) -> Dict[str, Any]:
    return {
        "solver_name": str(solver_name),
        "instance_hash": str(instance_hash),
        "solver_seed": int(solver_seed),
        "solution": {
            "x_up": float(x_up),
            "x_low": float(x_low),
            "robots": [dict(item) for item in robot_metrics],
        },
        "system_metrics": dict(system_metrics),
        "runtime_metrics": {"algorithm_time": float(algorithm_time)},
    }


@dataclass
class IncumbentTrace:
    checkpoints: Sequence[int] = TRACE_CHECKPOINTS
    best: Dict[str, float] | None = None
    records: List[Dict[str, float]] = field(default_factory=list)
    _next_index: int = 0

    def observe(
        self,
        objective_evaluation_count: int,
        fitness: float,
        makespan: float,
        load_imbalance: float,
        total_idle_distance: float,
        elapsed_algorithm_time: float,
    ) -> None:
        candidate = {
            "best_fitness": float(fitness),
            "best_makespan": float(makespan),
            "best_load_imbalance": float(load_imbalance),
            "best_total_idle_distance": float(total_idle_distance),
        }
        if self.best is None or candidate["best_fitness"] < self.best["best_fitness"]:
            self.best = candidate
        while self._next_index < len(self.checkpoints) and objective_evaluation_count >= int(self.checkpoints[self._next_index]):
            if self.best is None:
                break
            checkpoint = int(self.checkpoints[self._next_index])
            self.records.append({
                "objective_evaluation_count": checkpoint,
                **self.best,
                "elapsed_algorithm_time": float(elapsed_algorithm_time),
            })
            self._next_index += 1

