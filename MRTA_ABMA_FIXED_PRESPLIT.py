"""Development-only ABMA with immutable pre-split welds and exact caches.

The frozen :mod:`MRTA_ABMA` search implementation remains untouched.  This
module adapts only its task representation, boundary decoding, assignment and
route evaluator for the independently versioned fixed-pre-split model.
"""

from __future__ import annotations

import argparse
import contextlib
import copy
import hashlib
import json
import math
import os
import random
import time
from collections import Counter, OrderedDict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import MRTA_ABMA as legacy
import mrta_problem_core as core
from objective_normalization import BASELINE_ALGORITHM, OFFICIAL_MODE
from weld_fixed_presplit import (
    GEOMETRY_EPS,
    SCIENTIFIC_MODEL_VERSION,
    FixedWeld,
    assign_fixed_welds,
    file_sha256,
    load_fixed_instance,
    sha256_json,
)


ALGORITHM_VERSION = "ABMA-Fixed-PreSplit-Development-v1"
REFERENCE_VARIANT = "fixed_presplit_reference"
FAST_VARIANT = "fixed_presplit_exact_fast"
ZERO_POLICY = "connected_endpoint_zero_no_lift_descent_v1"
ROUTE_CACHE_MAX_ENTRIES = 1000000
TRAVEL_COST_CACHE_MAX_ENTRIES = 4000000
VARIANTS = (REFERENCE_VARIANT, FAST_VARIANT)


def _motion_hash(model: core.MotionModel) -> str:
    return sha256_json({
        "weld_speed": model.weld_speed,
        "travel_speed": model.travel_speed,
        "acceleration": model.acceleration,
        "safe_z": model.safe_z,
        "route_type": "open",
    })


def _endpoint(weld: Any, direction: int, start: bool) -> Tuple[float, float, float]:
    a, b = weld.start_point(), weld.end_point()
    value = (b if start else a) if direction else (a if start else b)
    return tuple(float(v) for v in value)


class FixedCacheContext:
    """Instance-wide exact caches and work counters for one solver run."""

    def __init__(self, variant: str, model: core.MotionModel,
                 stable_ids: Optional[Sequence[str]] = None):
        if variant not in VARIANTS:
            raise ValueError(f"unsupported fixed-pre-split variant: {variant}")
        self.variant = variant
        self.fast = variant == FAST_VARIANT
        self.model = model
        self.motion_hash = _motion_hash(model)
        self.pair_cache: Dict[Tuple[Any, ...], Tuple[float, float, bool]] = {}
        self.route_cache: OrderedDict[Any, Tuple[float, Tuple[Any, ...]]] = OrderedDict()
        self.travel_cost_cache: OrderedDict[Any, float] = OrderedDict()
        self.id_codes = {value: index for index, value in enumerate(sorted(set(stable_ids or ())))}
        self.counts: Counter[str] = Counter()
        self.times: Counter[str] = Counter()

    def snapshot(self) -> Dict[str, Any]:
        counts = dict(self.counts)
        route_requests = counts.get("route_evaluation_count", 0)
        pair_requests = counts.get("pair_request_count", 0)
        return {
            **counts,
            "route_cache_size": len(self.route_cache),
            "travel_cost_cache_size": len(self.travel_cost_cache),
            "pair_cache_size": len(self.pair_cache),
            "route_cache_hit_rate": counts.get("route_cache_hit_count", 0) / max(1, route_requests),
            "pair_cache_hit_rate": counts.get("pair_cache_hit_count", 0) / max(1, pair_requests),
            "timings_s": dict(self.times),
            "motion_model_hash": self.motion_hash,
            "zero_transition_policy": ZERO_POLICY,
        }


class FixedRouteEvaluator:
    """Exact two-state direction DP over immutable stable weld identities."""

    active_context: Optional[FixedCacheContext] = None

    @staticmethod
    def _pack_stats(stats: Mapping[str, Any]) -> Tuple[Any, ...]:
        return (
            float(stats.get("total_weld_time", 0.0)),
            float(stats.get("total_travel_time", 0.0)),
            float(stats.get("total_idle_distance", 0.0)),
            int(stats.get("reversed_weld_count", 0)),
            tuple(bool(value) for value in stats.get("direction_flags", ())),
            int(stats.get("selected_zero_connected_transition_count", 0)),
        )

    @staticmethod
    def _unpack_stats(cost: float, packed: Tuple[Any, ...]) -> Dict[str, Any]:
        weld, travel, distance, reversed_count, directions, zero_selected = packed
        return {
            "total_time": float(cost), "total_weld_time": float(weld),
            "total_travel_time": float(travel), "total_idle_distance": float(distance),
            "setup_post_saved_time": 0.0, "merged_setup_post_count": 0,
            "pure_weld_time": float(weld), "setup_post_time": 0.0,
            "reversed_weld_count": int(reversed_count), "direction_mode": "bidirectional",
            "direction_decoder_objective": "minimum_route_time",
            "direction_flags": list(directions),
            "selected_zero_connected_transition_count": int(zero_selected),
        }

    def __init__(self, welds: Sequence[Any], model: core.MotionModel, exact_fast: bool = False):
        del exact_fast
        if self.active_context is None:
            raise RuntimeError("fixed route evaluator used without an active cache context")
        self.context = self.active_context
        self.welds = list(welds)
        self.model = model
        self.exact_fast = True  # requests stable precomputed tie_keys in legacy heuristics
        self.cache: Dict[Tuple[int, ...], Tuple[float, Dict[str, Any]]] = {}
        self.local_travel_cost_cache: Dict[Tuple[int, ...], float] = {}
        self.local_pair_cache: Dict[Tuple[int, int, int, int], Tuple[float, float, bool]] = {}
        self.inherited_cache = None
        self.inherited_index_map: Dict[int, int] = {}
        self.calls = 0
        self.hits = 0
        self.centers = [legacy._weld_center(weld) for weld in self.welds]
        self.tie_keys = []
        self.weld_times = []
        self.stable_codes = []
        self.transition_times: Dict[Tuple[int, int, int, int], float] = {}
        self.transition_distances: Dict[Tuple[int, int, int, int], float] = {}
        self.transition_connected: Dict[Tuple[int, int, int, int], bool] = {}
        missing_ids = sorted({str(weld.id) for weld in self.welds} - set(self.context.id_codes))
        next_code = max(self.context.id_codes.values(), default=-1) + 1
        for offset, weld_id in enumerate(missing_ids):
            self.context.id_codes[weld_id] = next_code + offset
        for weld in self.welds:
            endpoints = tuple(sorted((tuple(round(float(v), 12) for v in weld.start_point()),
                                      tuple(round(float(v), 12) for v in weld.end_point()))))
            self.tie_keys.append((str(weld.id), endpoints))
            self.weld_times.append(core.weld_time(weld, model))
            weld_id = str(weld.id)
            self.stable_codes.append(self.context.id_codes[weld_id])
        # The fast variant pays stable-ID/global-cache lookup once when an
        # evaluator is constructed.  Its millions of subsequent DP edges use
        # compact local integer keys, avoiding repeated string/dict assembly.
        if self.context.fast:
            started = time.perf_counter()
            for previous in range(len(self.welds)):
                for current in range(len(self.welds)):
                    for previous_direction in (0, 1):
                        for current_direction in (0, 1):
                            key = (previous, previous_direction, current, current_direction)
                            travel, distance, connected = self._pair(
                                previous, previous_direction, current, current_direction
                            )
                            self.transition_times[key] = travel
                            self.transition_distances[key] = distance
                            self.transition_connected[key] = connected
            self.context.times["local_transition_table_build"] += time.perf_counter() - started

    def _pair(self, previous: int, previous_direction: int,
              current: int, current_direction: int) -> Tuple[float, float, bool]:
        ctx = self.context
        ctx.counts["pair_request_count"] += 1
        a = self.welds[previous]
        b = self.welds[current]
        key = (str(a.id), previous_direction, str(b.id), current_direction,
               ctx.motion_hash, ZERO_POLICY)
        if ctx.fast and key in ctx.pair_cache:
            ctx.counts["pair_cache_hit_count"] += 1
            return ctx.pair_cache[key]
        local_key = (previous, previous_direction, current, current_direction)
        if local_key in self.local_pair_cache:
            ctx.counts["pair_local_cache_hit_count"] += 1
            return self.local_pair_cache[local_key]
        ctx.counts["pair_cache_miss_count"] += 1
        started = time.perf_counter()
        p1 = _endpoint(a, previous_direction, start=False)
        p2 = _endpoint(b, current_direction, start=True)
        connected = math.dist(p1, p2) <= GEOMETRY_EPS
        if connected:
            value = (0.0, 0.0, True)
            ctx.counts["zero_connected_transition_count"] += 1
            # What the old lift/planar/descent model would have charged.
            ctx.counts["zero_connected_transition_old_nonzero_count"] += int(
                core.travel_time(p1, p2, self.model) > 0.0
            )
        else:
            value = (core.travel_time(p1, p2, self.model),
                     core.travel_distance(p1, p2, self.model), False)
        ctx.times["pair_geometry"] += time.perf_counter() - started
        if ctx.fast:
            ctx.pair_cache[key] = value
        self.local_pair_cache[local_key] = value
        return value

    def _evaluate_exact(self, order: Tuple[int, ...]) -> Tuple[float, Dict[str, Any]]:
        if not order:
            return 0.0, core.empty_route_stats()
        ctx = self.context
        ctx.counts["direction_dp_count"] += 1
        started = time.perf_counter()
        n = len(order)
        dp = [[math.inf, math.inf] for _ in range(n)]
        parent = [[0, 0] for _ in range(n)]
        first = self.weld_times[order[0]]
        dp[0] = [first, first]
        for pos in range(1, n):
            prev, cur = order[pos - 1], order[pos]
            weld_time = self.weld_times[cur]
            for cur_dir in (0, 1):
                for prev_dir in (0, 1):
                    if ctx.fast:
                        travel = self.transition_times[(prev, prev_dir, cur, cur_dir)]
                    else:
                        travel = self._pair(prev, prev_dir, cur, cur_dir)[0]
                    candidate = dp[pos - 1][prev_dir] + travel + weld_time
                    if candidate < dp[pos][cur_dir]:
                        dp[pos][cur_dir] = candidate
                        parent[pos][cur_dir] = prev_dir
        direction = 0 if dp[-1][0] <= dp[-1][1] else 1
        directions = [False] * n
        for pos in range(n - 1, -1, -1):
            directions[pos] = bool(direction)
            if pos:
                direction = parent[pos][direction]
        total_weld = sum(self.weld_times[item] for item in order)
        total_travel = total_distance = 0.0
        zero_selected = 0
        for pos in range(1, n):
            edge = (order[pos - 1], int(directions[pos - 1]), order[pos], int(directions[pos]))
            if ctx.fast:
                travel = self.transition_times[edge]
                distance = self.transition_distances[edge]
                connected = self.transition_connected[edge]
            else:
                travel, distance, connected = self._pair(*edge)
            total_travel += travel
            total_distance += distance
            zero_selected += int(connected)
        ctx.counts["selected_zero_connected_transition_count"] += zero_selected
        stats = {
            "total_time": total_weld + total_travel,
            "total_weld_time": total_weld,
            "total_travel_time": total_travel,
            "total_idle_distance": total_distance,
            "setup_post_saved_time": 0.0,
            "merged_setup_post_count": 0,
            "pure_weld_time": total_weld,
            "setup_post_time": 0.0,
            "reversed_weld_count": sum(directions),
            "direction_mode": "bidirectional",
            "direction_decoder_objective": "minimum_route_time",
            "direction_flags": directions,
            "selected_zero_connected_transition_count": zero_selected,
        }
        ctx.times["direction_dp"] += time.perf_counter() - started
        return float(stats["total_time"]), stats

    def evaluate(self, order: Sequence[int]) -> Tuple[float, Dict[str, Any]]:
        self.calls += 1
        self.context.counts["route_evaluation_count"] += 1
        local_key = tuple(order)
        cached = self.cache.get(local_key)
        if cached is not None:
            self.hits += 1
            self.context.counts["route_local_cache_hit_count"] += 1
            return cached[0], dict(cached[1])
        codes = tuple(self.stable_codes[index] for index in local_key)
        stable_key = bytes(codes) if all(0 <= value < 256 for value in codes) else codes
        if self.context.fast and stable_key in self.context.route_cache:
            self.hits += 1
            self.context.counts["route_cache_hit_count"] += 1
            cost, packed = self.context.route_cache[stable_key]
            stats = self._unpack_stats(cost, packed)
            self.context.route_cache.move_to_end(stable_key)
            self.cache[local_key] = (cost, dict(stats))
            return cost, dict(stats)
        self.context.counts["route_cache_miss_count"] += 1
        cost, stats = self._evaluate_exact(local_key)
        self.cache[local_key] = (cost, dict(stats))
        if self.context.fast:
            self.context.route_cache[stable_key] = (cost, self._pack_stats(stats))
            self.context.route_cache.move_to_end(stable_key)
            if len(self.context.route_cache) > ROUTE_CACHE_MAX_ENTRIES:
                self.context.route_cache.popitem(last=False)
                self.context.counts["route_cache_eviction_count"] += 1
        return cost, dict(stats)

    def travel_cost(self, order: Sequence[int]) -> float:
        if not self.context.fast:
            return float(self.evaluate(order)[1].get("total_travel_time", 0.0))
        self.calls += 1
        self.context.counts["route_evaluation_count"] += 1
        local_order = tuple(order)
        canonical_local = min(local_order, tuple(reversed(local_order)))
        cached_local = self.local_travel_cost_cache.get(canonical_local)
        if cached_local is not None:
            self.hits += 1
            self.context.counts["route_local_cache_hit_count"] += 1
            return cached_local
        coded_tuple = tuple(self.stable_codes[index] for index in local_order)
        coded = bytes(coded_tuple) if all(0 <= value < 256 for value in coded_tuple) else coded_tuple
        reversed_coded = coded[::-1]
        stable_key = min(coded, reversed_coded)
        cached = self.context.travel_cost_cache.get(stable_key)
        if cached is not None:
            self.hits += 1
            self.context.counts["route_cache_hit_count"] += 1
            self.context.counts["travel_cost_reverse_safe_hit_count"] += 1
            self.context.travel_cost_cache.move_to_end(stable_key)
            self.local_travel_cost_cache[canonical_local] = cached
            return cached
        self.context.counts["route_cache_miss_count"] += 1
        cost, stats = self._evaluate_exact(local_order)
        travel = float(stats.get("total_travel_time", 0.0))
        # This exact orientation is now also available for callers requesting
        # full stats.  The reverse is cached only as a scalar travel cost.
        self.cache[local_order] = (cost, dict(stats))
        exact_key = coded
        self.context.route_cache[exact_key] = (cost, self._pack_stats(stats))
        self.context.route_cache.move_to_end(exact_key)
        if len(self.context.route_cache) > ROUTE_CACHE_MAX_ENTRIES:
            self.context.route_cache.popitem(last=False)
            self.context.counts["route_cache_eviction_count"] += 1
        self.local_travel_cost_cache[canonical_local] = travel
        self.context.travel_cost_cache[stable_key] = travel
        self.context.travel_cost_cache.move_to_end(stable_key)
        if len(self.context.travel_cost_cache) > TRAVEL_COST_CACHE_MAX_ENTRIES:
            self.context.travel_cost_cache.popitem(last=False)
            self.context.counts["travel_cost_cache_eviction_count"] += 1
        return travel


@contextlib.contextmanager
def _activate_fixed_evaluator(context: FixedCacheContext):
    previous_class = legacy.RouteEvaluator
    previous_context = FixedRouteEvaluator.active_context
    FixedRouteEvaluator.active_context = context
    legacy.RouteEvaluator = FixedRouteEvaluator
    try:
        yield
    finally:
        legacy.RouteEvaluator = previous_class
        FixedRouteEvaluator.active_context = previous_context


class FixedPreSplitABMASolver(legacy.ABMASolver):
    def __init__(self, fixed_welds: Sequence[FixedWeld], boundary_data: Mapping[str, Any],
                 config: legacy.ABMAConfig, variant: str):
        self.fixed_welds = list(fixed_welds)
        self.boundary_values = {
            half: [float(item["x"]) for item in boundary_data[half]["candidates"]]
            for half in ("upper", "lower")
        }
        if not self.boundary_values["upper"] or not self.boundary_values["lower"]:
            raise ValueError("fixed-pre-split instance has an empty legal boundary set")
        self.variant = variant
        weld_objects = [w.to_weld() for w in self.fixed_welds]
        self.fixed_by_id = {w.id: w for w in self.fixed_welds}
        self.cache_context = FixedCacheContext(
            variant, config.motion_model(), [w.id for w in self.fixed_welds]
        )
        self.boundary_pair_visits: Counter[Tuple[int, int]] = Counter()
        super().__init__(weld_objects, config)

    def _decode_indices(self, u_up: float, u_low: float) -> Tuple[int, int]:
        def decode(value: float, values: Sequence[float]) -> int:
            bounded = min(1.0, max(0.0, float(value)))
            return int(round(bounded * (len(values) - 1)))
        return decode(u_up, self.boundary_values["upper"]), decode(u_low, self.boundary_values["lower"])

    def _key(self, x_up: float, x_low: float) -> Tuple[int, int]:  # type: ignore[override]
        return self._decode_indices(x_up, x_low)

    def _repair_boundary(self, value: float, parent: float) -> float:
        if value < 0.0:
            return 0.5 * parent
        if value > 1.0:
            return 0.5 * (1.0 + parent)
        return value

    def _initial_partition_vectors(self, lo: float, hi: float) -> List[Tuple[float, float]]:
        del lo, hi
        vectors = [(0.5, 0.5)]
        if self.config.use_problem_informed_initialization:
            vectors.append((0.5, 0.5))
        vectors.extend(legacy._latin_hypercube_2d(
            self.rng, self.config.population_size - len(vectors), 0.0, 1.0
        ))
        return vectors[: self.config.population_size]

    def _assign_partition(self, u_up: float, u_low: float):
        key = self._key(u_up, u_low)
        cached = self.assignment_cache.get(key)
        if cached is not None:
            self.assignment_cache_hits += 1
            robots, stats = cached
            return robots, dict(stats)
        self.assignment_cache_misses += 1
        x_up = self.boundary_values["upper"][key[0]]
        x_low = self.boundary_values["lower"][key[1]]
        fixed_robots, stats = assign_fixed_welds(self.fixed_welds, x_up, x_low)
        robots = [[item.to_weld() for item in robot] for robot in fixed_robots]
        stats.update({"boundary_index_up": key[0], "boundary_index_low": key[1],
                      "boundary_x_up": x_up, "boundary_x_low": x_low,
                      "no_fixed_weld_crosses_boundary": True})
        self.assignment_cache[key] = (robots, dict(stats))
        return robots, stats

    def _legacy_evaluate(self, u_up: float, u_low: float) -> legacy.PartitionSolution:
        key = self._key(u_up, u_low)
        self.boundary_pair_visits[key] += 1
        self.objective_evaluations += 1
        if self.config.enable_partition_cache and key in self.partition_cache:
            self.cache_hits += 1
            solution = self.partition_cache[key].clone()
            self.trace.observe(self.objective_evaluations, solution.fitness,
                               solution.makespan, solution.load_imbalance,
                               solution.total_idle_distance,
                               time.perf_counter() - self.trace_started)
            return solution
        self.cache_misses += 1
        self.partition_evaluations += 1
        robots, assignment_stats = self._assign_partition(u_up, u_low)
        warm_solution = self._nearest_warm_solution(u_up, u_low)
        local_rng = random.Random(legacy._stable_partition_seed(self.config.seed, key))
        actual_up = float(assignment_stats["boundary_x_up"])
        actual_low = float(assignment_stats["boundary_x_low"])
        with _activate_fixed_evaluator(self.cache_context):
            orders, route_stats, alns_stats = legacy.optimize_partition_joint_alns(
                robots, self.config, local_rng, actual_up, actual_low,
                self.references, warm_solution=warm_solution,
            )
        self._digest_update(self.inner_rng_state_digest, [key, repr(local_rng.getstate())])
        makespan, load, distance = legacy._system_metrics(route_stats)
        solution = legacy.PartitionSolution(
            x_up=u_up, x_low=u_low, makespan=makespan, load_imbalance=load,
            total_idle_distance=distance,
            fitness=self._fitness_values(makespan, load, distance),
            robots_stats=[dict(item) for item in route_stats],
            robot_orders=[list(order) for order in orders],
            robot_order_ids=[[str(robots[i][task].id) for task in orders[i]] for i in range(4)],
            robot_direction_flags=[[bool(flag) for flag in stats.get("direction_flags", [])]
                                   for stats in route_stats],
            assignment_stats=dict(assignment_stats), alns_stats=alns_stats,
        )
        self.warm_archive.append(solution.clone())
        max_archive = max(20, 4 * self.config.population_size)
        if len(self.warm_archive) > max_archive:
            self.warm_archive = sorted(self.warm_archive, key=lambda item: item.fitness)[:max_archive]
        if self.config.enable_partition_cache:
            self.partition_cache[key] = solution.clone()
        self.trace.observe(self.objective_evaluations, solution.fitness, solution.makespan,
                           solution.load_imbalance, solution.total_idle_distance,
                           time.perf_counter() - self.trace_started)
        return solution

    def solve(self) -> legacy.ABMAResult:
        result = super().solve()
        best_key = self._key(result.best.x_up, result.best.x_low)
        result.best.assignment_stats.update({
            "boundary_index_up": best_key[0], "boundary_index_low": best_key[1],
            "boundary_x_up": self.boundary_values["upper"][best_key[0]],
            "boundary_x_low": self.boundary_values["lower"][best_key[1]],
        })
        visits = sum(self.boundary_pair_visits.values())
        result.diagnostics.update({
            "algorithm_version": ALGORITHM_VERSION,
            "development_variant": self.variant,
            "scientific_model_version": SCIENTIFIC_MODEL_VERSION,
            "boundary_search_space": "continuous_u_decoded_to_discrete_index_round",
            "upper_boundary_candidate_count": len(self.boundary_values["upper"]),
            "lower_boundary_candidate_count": len(self.boundary_values["lower"]),
            "boundary_pair_visit_count": visits,
            "unique_boundary_pair_count": len(self.boundary_pair_visits),
            "duplicate_boundary_pair_visit_count": visits - len(self.boundary_pair_visits),
            "boundary_pair_visit_histogram": {
                f"{a},{b}": count for (a, b), count in sorted(self.boundary_pair_visits.items())
            },
            **self.cache_context.snapshot(),
        })
        return result


def _rss_peak_mb() -> float:
    if os.name == "nt":
        try:
            import psutil
            memory = psutil.Process().memory_info()
            return float(getattr(memory, "peak_wset", memory.rss)) / (1024.0 * 1024.0)
        except Exception:
            return 0.0
    import resource
    return float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss) / 1024.0


def run_solver(fixed_path: str | Path, normalization_path: str | Path,
               variant: str, seed: int, budget: int, verbose: bool = False) -> Dict[str, Any]:
    fixed, metadata = load_fixed_instance(fixed_path)
    normalization_all = json.loads(Path(normalization_path).read_text(encoding="utf-8"))
    normalization = normalization_all[metadata["name"]]
    population = 12
    generations = max(0, math.ceil(max(0, budget - population) / population))
    config = legacy.ABMAConfig(
        seed=seed, population_size=population, generations=generations,
        max_objective_evaluations=budget, alns_iterations=80, vnd_iterations=3,
        max_move_checks_per_operator=80, early_stop_patience=0,
        normalization_mode=OFFICIAL_MODE, normalization_spec=normalization,
        abma_variant="legacy_exact_fast", verbose=verbose,
    )
    solver = FixedPreSplitABMASolver(fixed, metadata["boundary_candidates"], config, variant)
    started = time.perf_counter(); result = solver.solve(); wall = time.perf_counter() - started
    best = result.best
    payload = {
        "algorithm_version": ALGORITHM_VERSION, "development_variant": variant,
        "scientific_model_version": SCIENTIFIC_MODEL_VERSION,
        "instance": metadata["name"], "instance_file": str(fixed_path),
        "instance_file_sha256": file_sha256(fixed_path),
        "preprocessing_hash": metadata["preprocessing_hash"],
        "normalization_file": str(normalization_path),
        "normalization_spec_hash": sha256_json(normalization),
        "seed": seed, "budget": budget, "population_size": population,
        "alns_iterations": 80, "vnd_iterations": 3,
        "elapsed_time_s": wall, "solver_elapsed_time_s": result.elapsed_time,
        "peak_rss_mb": _rss_peak_mb(), "fitness": best.fitness,
        "makespan": best.makespan, "load_imbalance": best.load_imbalance,
        "total_idle_distance": best.total_idle_distance,
        "best_boundary_u": {"upper": best.x_up, "lower": best.x_low},
        "best_boundary": {
            "upper_index": best.assignment_stats["boundary_index_up"],
            "lower_index": best.assignment_stats["boundary_index_low"],
            "x_up": best.assignment_stats["boundary_x_up"],
            "x_low": best.assignment_stats["boundary_x_low"],
        },
        "robot_order_ids": best.robot_order_ids,
        "robot_direction_flags": best.robot_direction_flags,
        "robot_stats": best.robots_stats,
        "assignment_stats": best.assignment_stats,
        "diagnostics": result.diagnostics,
        "formal_run": False, "development_only": True,
    }
    return payload


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--instance", required=True)
    parser.add_argument("--normalization", default="fixed_presplit_normalization.json")
    parser.add_argument("--variant", choices=VARIANTS, required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--budget", type=int, required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    payload = run_solver(args.instance, args.normalization, args.variant,
                         args.seed, args.budget, args.verbose)
    path = Path(args.output); path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({key: payload[key] for key in
                      ("instance", "development_variant", "seed", "budget", "elapsed_time_s", "fitness")},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
