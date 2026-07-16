"""Global transition table and exact route/system evaluator for atomic welds."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Sequence, Tuple

from atomic_problem_core import AtomicInstance, AtomicWeld, ZERO_ENDPOINT_TOL_M, canonical_json
from mrta_problem_core import DEFAULT_MOTION_MODEL, MotionModel, travel_distance, travel_time


@dataclass(frozen=True)
class RouteResult:
    order: Tuple[str, ...]
    total_time: float
    weld_time: float
    travel_time: float
    idle_distance: float
    direction_flags: Tuple[bool, ...]


@dataclass(frozen=True)
class SystemResult:
    fitness: float
    makespan: float
    load_imbalance: float
    idle_distance: float
    robot_total_times: Tuple[float, ...]
    routes: Tuple[Tuple[str, ...], ...]
    direction_flags: Tuple[Tuple[bool, ...], ...]
    components: Dict[str, float]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "fitness": self.fitness, "makespan": self.makespan,
            "load_imbalance": self.load_imbalance, "idle_distance": self.idle_distance,
            "robot_total_times": list(self.robot_total_times),
            "routes": [list(route) for route in self.routes],
            "direction_flags": [list(flags) for flags in self.direction_flags],
            "normalized_components": dict(self.components),
        }


class AtomicRouteEvaluator:
    """Precompute exactly one 4N² directed endpoint transition table."""

    def __init__(self, instance: AtomicInstance, normalization_spec: Mapping[str, Any] | None = None,
                 model: MotionModel = DEFAULT_MOTION_MODEL):
        self.instance = instance
        self.model = model
        self.welds = tuple(instance.atomic_welds)
        self.ids = tuple(w.id for w in self.welds)
        self.index = {wid: i for i, wid in enumerate(self.ids)}
        self.normalization_spec = dict(normalization_spec) if normalization_spec is not None else None
        n = len(self.welds)
        self.transition_time = [[[[0.0 for _ in range(2)] for _ in range(n)] for _ in range(2)] for _ in range(n)]
        self.transition_distance = [[[[0.0 for _ in range(2)] for _ in range(n)] for _ in range(2)] for _ in range(n)]
        self.zero_successors: Dict[Tuple[str, bool], List[Tuple[str, bool]]] = {}
        for i, previous in enumerate(self.welds):
            for di in (0, 1):
                pend = previous.start if di else previous.end
                zero: List[Tuple[str, bool]] = []
                for j, current in enumerate(self.welds):
                    for dj in (0, 1):
                        cstart = current.end if dj else current.start
                        euclid = math.dist(pend, cstart)
                        if euclid <= ZERO_ENDPOINT_TOL_M:
                            t = d = 0.0
                            if i != j:
                                zero.append((current.id, bool(dj)))
                        else:
                            t = travel_time(pend, cstart, model)
                            d = travel_distance(pend, cstart, model)
                        self.transition_time[i][di][j][dj] = t
                        self.transition_distance[i][di][j][dj] = d
                self.zero_successors[(previous.id, bool(di))] = zero
        self.same_parent_adjacent = {
            weld.id: tuple(other.id for other in self.welds
                           if other.parent_weld_id == weld.parent_weld_id
                           and abs(other.segment_index - weld.segment_index) == 1)
            for weld in self.welds
        }
        self.route_cache: Dict[Tuple[str, ...], RouteResult] = {}
        self.route_evaluation_requests = 0
        self.route_cache_hits = 0
        self.direction_dp_calls = 0
        self.table_build_count = 1

    def evaluate_route(self, order: Sequence[str]) -> RouteResult:
        key = tuple(order)
        self.route_evaluation_requests += 1
        if key in self.route_cache:
            self.route_cache_hits += 1
            return self.route_cache[key]
        if len(set(key)) != len(key) or any(wid not in self.index for wid in key):
            raise ValueError("route contains duplicate or unknown atomic weld")
        if not key:
            result = RouteResult(key, 0.0, 0.0, 0.0, 0.0, ())
            self.route_cache[key] = result
            return result
        self.direction_dp_calls += 1
        n = len(key)
        dp = [[math.inf, math.inf] for _ in range(n)]
        parent = [[0, 0] for _ in range(n)]
        first = self.welds[self.index[key[0]]]
        dp[0] = [first.weld_time_s, first.weld_time_s]
        for pos in range(1, n):
            current_index = self.index[key[pos]]
            current = self.welds[current_index]
            previous_index = self.index[key[pos - 1]]
            for dc in (0, 1):
                for dpred in (0, 1):
                    candidate = dp[pos - 1][dpred] + self.transition_time[previous_index][dpred][current_index][dc] + current.weld_time_s
                    if candidate < dp[pos][dc] - 1e-12:
                        dp[pos][dc] = candidate
                        parent[pos][dc] = dpred
        direction = 0 if dp[-1][0] <= dp[-1][1] else 1
        directions = [False] * n
        for pos in range(n - 1, -1, -1):
            directions[pos] = bool(direction)
            if pos:
                direction = parent[pos][direction]
        weld_total = sum(self.welds[self.index[wid]].weld_time_s for wid in key)
        move_time = move_distance = 0.0
        for pos in range(1, n):
            i, j = self.index[key[pos - 1]], self.index[key[pos]]
            di, dj = int(directions[pos - 1]), int(directions[pos])
            move_time += self.transition_time[i][di][j][dj]
            move_distance += self.transition_distance[i][di][j][dj]
        result = RouteResult(key, weld_total + move_time, weld_total, move_time, move_distance, tuple(directions))
        self.route_cache[key] = result
        return result

    def evaluate_system(self, routes: Sequence[Sequence[str]]) -> SystemResult:
        if len(routes) != 4:
            raise ValueError("exactly four robot routes are required")
        flattened = [wid for route in routes for wid in route]
        if len(flattened) != len(self.ids) or set(flattened) != set(self.ids):
            raise ValueError("system routes must contain every atomic weld exactly once")
        route_results = tuple(self.evaluate_route(route) for route in routes)
        totals = tuple(r.total_time for r in route_results)
        metrics = {
            "makespan": max(totals, default=0.0),
            "load_imbalance": max(totals, default=0.0) - min(totals, default=0.0),
            "idle_distance": sum(r.idle_distance for r in route_results),
        }
        if self.normalization_spec is None:
            components = dict(metrics)
            fitness = 0.7 * metrics["makespan"] + 0.2 * metrics["load_imbalance"] + 0.1 * metrics["idle_distance"]
        else:
            ideal = self.normalization_spec["ideal"]
            scale = self.normalization_spec["scale"]
            components = {key: max(0.0, (metrics[key] - float(ideal[key])) / float(scale[key])) for key in metrics}
            weights = self.normalization_spec.get("weights", [0.7, 0.2, 0.1])
            fitness = sum(float(w) * components[k] for w, k in zip(weights, ("makespan", "load_imbalance", "idle_distance")))
        return SystemResult(fitness, metrics["makespan"], metrics["load_imbalance"], metrics["idle_distance"], totals,
                            tuple(tuple(route) for route in routes), tuple(r.direction_flags for r in route_results), components)

    def counters(self) -> Dict[str, int]:
        return {
            "global_transition_table_builds": self.table_build_count,
            "route_evaluation_requests": self.route_evaluation_requests,
            "route_cache_hits": self.route_cache_hits,
            "direction_dp_calls": self.direction_dp_calls,
        }


def normalization_hash(spec: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(spec).encode("utf-8")).hexdigest()

