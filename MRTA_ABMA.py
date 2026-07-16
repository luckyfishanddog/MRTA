"""Adaptive bilevel memetic algorithm (ABMA) for four-robot weld scheduling.

The implementation is aligned with the Chapter 2 model:

* outer level: a SHADE-like adaptive differential evolution searches the two
  continuous partition coordinates ``(x_up, x_low)``;
* decoder: boundary intersections split cross-region welds, and every complete
  sub-weld is assigned to exactly one robot region;
* inner level: a joint, direction-aware ALNS/VND modifies one robot route at a
  time but accepts moves using the complete four-robot weighted objective;
* orientation decoder: for each fixed route order, a two-state dynamic program
  obtains the minimum-time forward/reverse orientation combination;
* acceleration: partition caching and partition-similarity warm starts.

The motion model uses one empty-travel speed and one acceleration for lift,
planar movement on the travel plane, and descent, exactly as stated in the
paper. Initial positioning and final return are excluded.
"""

from __future__ import annotations

import argparse
import cProfile
import csv
import hashlib
import importlib.util
import json
import math
import os
import pstats
import random
import sys
import time
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from types import ModuleType
from typing import Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

from objective_normalization import (
    BASELINE_ALGORITHM, LEGACY_MODE, OFFICIAL_MODE,
    normalized_components, normalized_objective, validate_normalization_spec,
)
from solver_output_metrics import (
    IncumbentTrace, build_robot_metrics, build_standard_result,
    build_system_metrics, flatten_robot_metrics,
)


def _load_module_from_path(module_name: str, path: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {module_name} from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _load_generate_welds() -> ModuleType:
    try:
        import generate_welds as module  # type: ignore
        return module
    except ModuleNotFoundError:
        directory = os.path.dirname(os.path.abspath(__file__))
        for filename in ("generate_welds(25).py", "generate_welds(22).py"):
            path = os.path.join(directory, filename)
            if os.path.exists(path):
                return _load_module_from_path("generate_welds", path)
        raise


def _load_core() -> ModuleType:
    """Prefer the sibling file with the unchanged uploaded filename."""
    directory = os.path.dirname(os.path.abspath(__file__))
    sibling = os.path.join(directory, "mrta_problem_core(1).py")
    if os.path.exists(sibling):
        return _load_module_from_path("mrta_problem_core_abma_local", sibling)
    import mrta_problem_core as module  # type: ignore
    return module


_generate_welds = _load_generate_welds()
core = _load_core()

DEFAULT_MIN_WELD_LENGTH_M = float(_generate_welds.DEFAULT_MIN_WELD_LENGTH_M)
DEFAULT_WELD_Z_M = float(_generate_welds.DEFAULT_WELD_Z_M)
PLATFORM_W_M = float(_generate_welds.PLATFORM_W_M)
Weld = _generate_welds.Weld
get_welds_from_excel = _generate_welds.get_welds_from_excel
instance_metrics = _generate_welds.instance_metrics
load_solver_weld_input = _generate_welds.load_solver_weld_input
resolve_solver_seed = _generate_welds.resolve_solver_seed

SOLVER_NAME = "ABMA"
EPS = 1.0e-9


PROFILE_STAGE_NAMES = (
    "time_outer_de", "time_partition_assignment",
    "time_subweld_identity_mapping", "time_incremental_repair",
    "time_regret_insertion", "time_route_evaluation", "time_direction_dp",
    "time_alns", "time_vnd", "time_system_aggregation", "time_state_copy",
    "time_cache_lookup", "time_result_serialization",
)
PROFILE_COUNT_NAMES = (
    "partition_assignment_count", "route_evaluation_count",
    "direction_dp_count", "system_objective_count", "deepcopy_count",
    "inner_state_clone_count", "route_cache_hit_count",
    "route_cache_miss_count", "assignment_cache_hit_count",
    "assignment_cache_miss_count", "high_fidelity_promotion_count",
)


class ABMAProfiler:
    """Opt-in exact stage accounting; inactive during ordinary solver runs."""

    def __init__(self) -> None:
        self.stage_times = defaultdict(float)
        self.counts = defaultdict(int)

    def add_time(self, name: str, elapsed: float) -> None:
        self.stage_times[name] += max(0.0, float(elapsed))

    def count(self, name: str, amount: int = 1) -> None:
        self.counts[name] += int(amount)

    def payload(self) -> Dict[str, object]:
        return {
            "stage_times": {name: self.stage_times[name] for name in PROFILE_STAGE_NAMES},
            "counts": {name: self.counts[name] for name in PROFILE_COUNT_NAMES},
        }


_ACTIVE_PROFILER: Optional[ABMAProfiler] = None


@contextmanager
def _profile_stage(name: str):
    profiler = _ACTIVE_PROFILER
    if profiler is None:
        yield
        return
    started = time.perf_counter()
    try:
        yield
    finally:
        profiler.add_time(name, time.perf_counter() - started)


def _profile_count(name: str, amount: int = 1) -> None:
    if _ACTIVE_PROFILER is not None:
        _ACTIVE_PROFILER.count(name, amount)


@dataclass
class ABMAConfig:
    seed: int = 42
    population_size: int = 12
    generations: int = 20
    max_objective_evaluations: int = 0
    memory_size: int = 6
    p_best_rate: float = 0.25
    archive_rate: float = 1.5
    boundary_margin: float = 0.0

    # Use a fixed budget in formal comparisons. Set to 0 to disable early stop.
    early_stop_patience: int = 0
    early_stop_min_delta: float = 1.0e-6

    # Joint inner ALNS/VND.
    alns_iterations: int = 80
    alns_segment_length: int = 10
    destroy_fraction_min: float = 0.10
    destroy_fraction_max: float = 0.35
    reaction_factor: float = 0.20
    initial_temperature_ratio: float = 0.03
    cooling_rate: float = 0.97
    critical_robot_rate: float = 0.65
    vnd_iterations: int = 3
    max_move_checks_per_operator: int = 80
    route_time_limit_s: float = 0.0

    # System-level objective from Chapter 2.
    weight_makespan: float = 0.70
    weight_load: float = 0.20
    weight_distance: float = 0.10

    # Optional externally fixed references. Provide all three or none.
    reference_makespan: Optional[float] = None
    reference_load: Optional[float] = None
    reference_distance: Optional[float] = None
    normalization_mode: str = LEGACY_MODE
    normalization_spec: Optional[Dict[str, object]] = None

    # Evaluation and acceleration.
    partition_cache_decimals: int = 6
    enable_partition_cache: bool = True
    enable_warm_start: bool = True
    enable_shade_adaptation: bool = True
    enable_alns_adaptation: bool = True
    use_problem_informed_initialization: bool = True
    verbose: bool = True

    # ABMA performance profiles.  ``legacy`` preserves the historical 80/3
    # one-shot inner search.  The optimized profiles keep the same scientific
    # model and outer SHADE search while changing only route inheritance and/or
    # the amount of inner work spent before a candidate is competitive.
    abma_variant: str = "legacy"
    low_alns_iterations: int = 15
    mid_alns_iterations: int = 50
    high_alns_iterations: int = 100
    low_vnd_iterations: int = 0
    mid_vnd_iterations: int = 1
    high_vnd_iterations: int = 3
    low_promotion_margin: float = 0.03
    mid_promotion_margin: float = 0.03
    rejected_audit_interval: int = 20
    adaptive_promotion_enabled: bool = True
    audit_quantile: float = 0.95
    minimum_audit_samples: int = 50
    promotion_tolerance: float = 1.0e-12
    near_target_margin: float = 0.01
    small_boundary_delta: float = 0.10
    large_boundary_delta: float = 5.0
    high_preserved_task_ratio: float = 0.80
    high_affected_task_ratio: float = 0.40
    low_population_diversity: float = 0.01

    # Physical parameters: one speed and one acceleration for all empty travel.
    weld_speed: float = 0.0108
    travel_speed: float = 0.20
    acceleration: float = 0.50
    safe_z: float = 0.30

    # Geometric feasibility.
    split_epsilon: float = 1.0e-9
    geometry_tolerance: float = 1.0e-8
    min_subweld_length: float = 1.0e-6

    direction_mode: str = "bidirectional"

    def validate(self) -> None:
        if self.population_size < 4:
            raise ValueError("population_size must be at least 4")
        if self.generations < 0 or self.alns_iterations < 0:
            raise ValueError("iteration counts must be non-negative")
        if self.max_objective_evaluations < 0:
            raise ValueError("max_objective_evaluations must be non-negative")
        if self.memory_size < 1:
            raise ValueError("memory_size must be positive")
        if not 0.0 < self.p_best_rate <= 1.0:
            raise ValueError("p_best_rate must be in (0, 1]")
        if not 0.0 <= self.destroy_fraction_min <= self.destroy_fraction_max <= 1.0:
            raise ValueError("invalid destroy fraction interval")
        if not 0.0 <= self.critical_robot_rate <= 1.0:
            raise ValueError("critical_robot_rate must be in [0, 1]")
        if self.early_stop_patience < 0:
            raise ValueError("early_stop_patience must be non-negative")
        if self.split_epsilon <= 0.0 or self.geometry_tolerance <= 0.0:
            raise ValueError("geometric tolerances must be positive")
        if self.min_subweld_length < 0.0:
            raise ValueError("min_subweld_length must be non-negative")
        if self.abma_variant not in {
            "legacy", "legacy_exact_fast", "exact_fast_incremental_multifidelity",
            "incremental_only", "multifidelity_only", "incremental_multifidelity",
        }:
            raise ValueError("unsupported ABMA optimization variant")
        fidelity_counts = (
            self.low_alns_iterations, self.mid_alns_iterations,
            self.high_alns_iterations, self.low_vnd_iterations,
            self.mid_vnd_iterations, self.high_vnd_iterations,
        )
        if any(value < 0 for value in fidelity_counts):
            raise ValueError("fidelity iteration counts must be non-negative")
        if not (self.low_alns_iterations <= self.mid_alns_iterations <= self.high_alns_iterations):
            raise ValueError("ALNS fidelity budgets must be nondecreasing")
        if not (self.low_vnd_iterations <= self.mid_vnd_iterations <= self.high_vnd_iterations):
            raise ValueError("VND fidelity budgets must be nondecreasing")
        if self.low_promotion_margin < 0.0 or self.mid_promotion_margin < 0.0:
            raise ValueError("promotion margins must be non-negative")
        if self.rejected_audit_interval < 1:
            raise ValueError("rejected_audit_interval must be positive")
        if not 0.5 <= self.audit_quantile < 1.0:
            raise ValueError("audit_quantile must be in [0.5, 1.0)")
        if self.minimum_audit_samples < 1:
            raise ValueError("minimum_audit_samples must be positive")
        if min(self.promotion_tolerance, self.near_target_margin,
               self.small_boundary_delta, self.large_boundary_delta,
               self.high_preserved_task_ratio, self.high_affected_task_ratio,
               self.low_population_diversity) < 0.0:
            raise ValueError("adaptive promotion thresholds must be non-negative")
        if self.high_preserved_task_ratio > 1.0 or self.high_affected_task_ratio > 1.0:
            raise ValueError("task ratios must be at most one")

        total_weight = self.weight_makespan + self.weight_load + self.weight_distance
        if total_weight <= 0.0:
            raise ValueError("at least one objective weight must be positive")
        self.weight_makespan /= total_weight
        self.weight_load /= total_weight
        self.weight_distance /= total_weight

        references = (
            self.reference_makespan,
            self.reference_load,
            self.reference_distance,
        )
        supplied = [value is not None for value in references]
        if any(supplied) and not all(supplied):
            raise ValueError("provide all three reference values or none")
        if all(supplied) and any(float(value) <= 0.0 for value in references):
            raise ValueError("reference values must be positive")
        if self.normalization_mode == OFFICIAL_MODE:
            if any(supplied):
                raise ValueError("legacy references cannot be mixed with official normalization")
            if self.normalization_spec is None:
                raise ValueError("official normalization spec is required")
            validate_normalization_spec(self.normalization_spec)
        elif self.normalization_mode != LEGACY_MODE:
            raise ValueError("unsupported normalization mode")
        elif self.normalization_spec is not None:
            raise ValueError("normalization_spec is only valid in official mode")

        if self.direction_mode != "bidirectional":
            raise ValueError("ABMA requires bidirectional direction decoding")
        self.motion_model().validate()

    def motion_model(self):
        return core.MotionModel(
            weld_speed=self.weld_speed,
            travel_speed=self.travel_speed,
            acceleration=self.acceleration,
            safe_z=self.safe_z,
        )

    def explicit_references(self) -> Optional[Tuple[float, float, float]]:
        if self.reference_makespan is None:
            return None
        assert self.reference_load is not None and self.reference_distance is not None
        return (
            float(self.reference_makespan),
            float(self.reference_load),
            float(self.reference_distance),
        )

    @property
    def incremental_enabled(self) -> bool:
        return self.abma_variant in {
            "incremental_only", "incremental_multifidelity",
            "exact_fast_incremental_multifidelity",
        }

    @property
    def multifidelity_enabled(self) -> bool:
        return self.abma_variant in {
            "multifidelity_only", "incremental_multifidelity",
            "exact_fast_incremental_multifidelity",
        }

    @property
    def exact_fast_enabled(self) -> bool:
        return self.abma_variant in {
            "legacy_exact_fast", "exact_fast_incremental_multifidelity",
        }


def abma_profile_payload(config: ABMAConfig) -> Dict[str, object]:
    return {
        "algorithm_version": "ABMA-Incremental-MultiFidelity-v2",
        "variant": config.abma_variant,
        "fidelity_budgets": {
            "low": [config.low_alns_iterations, config.low_vnd_iterations],
            "mid": [config.mid_alns_iterations, config.mid_vnd_iterations],
            "high": [config.high_alns_iterations, config.high_vnd_iterations],
        },
        "promotion_policy": {
            "mode": "audited_conservative_quantile_v1",
            "enabled": config.adaptive_promotion_enabled,
            "quantile": config.audit_quantile,
            "minimum_audit_samples": config.minimum_audit_samples,
            "audit_interval": config.rejected_audit_interval,
            "tolerance": config.promotion_tolerance,
            "near_target_margin": config.near_target_margin,
            "small_boundary_delta": config.small_boundary_delta,
            "large_boundary_delta": config.large_boundary_delta,
            "high_preserved_task_ratio": config.high_preserved_task_ratio,
            "high_affected_task_ratio": config.high_affected_task_ratio,
            "low_population_diversity": config.low_population_diversity,
        },
        "incremental_policy": {
            "enabled": config.incremental_enabled,
            "identity": "parent_id_plus_canonical_geometry",
            "repair": "deterministic_regret2_direction_dp",
            "assignment_cache": True,
            "unchanged_robot_route_cache_inheritance": True,
        },
        "rng_policy": "separate_outer_and_partition_derived_inner_streams",
        "normalization_mode": config.normalization_mode,
    }


def abma_profile_hash(config: ABMAConfig) -> str:
    return hashlib.sha256(json.dumps(
        abma_profile_payload(config), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")).hexdigest()


@dataclass
class PartitionSolution:
    x_up: float
    x_low: float
    makespan: float = math.inf
    load_imbalance: float = math.inf
    total_idle_distance: float = math.inf
    fitness: float = math.inf
    robots_stats: List[Dict[str, object]] = field(default_factory=list)
    robot_orders: List[List[int]] = field(default_factory=list)
    robot_order_ids: List[List[str]] = field(default_factory=list)
    robot_direction_flags: List[List[bool]] = field(default_factory=list)
    assignment_stats: Dict[str, object] = field(default_factory=dict)
    alns_stats: List[Dict[str, object]] = field(default_factory=list)
    fidelity_level: str = "high"
    high_certified: bool = True
    repair_diagnostics: Dict[str, object] = field(default_factory=dict)
    inner_state: Optional["InnerSearchState"] = field(default=None, repr=False)

    def clone(self) -> "PartitionSolution":
        _profile_count("deepcopy_count")
        if self.inner_state is not None:
            _profile_count("inner_state_clone_count")
        with _profile_stage("time_state_copy"):
            return PartitionSolution(
                x_up=self.x_up,
                x_low=self.x_low,
                makespan=self.makespan,
                load_imbalance=self.load_imbalance,
                total_idle_distance=self.total_idle_distance,
                fitness=self.fitness,
                robots_stats=[dict(item) for item in self.robots_stats],
                robot_orders=[list(item) for item in self.robot_orders],
                robot_order_ids=[list(item) for item in self.robot_order_ids],
                robot_direction_flags=[list(item) for item in self.robot_direction_flags],
                assignment_stats=dict(self.assignment_stats),
                alns_stats=[dict(item) for item in self.alns_stats],
                fidelity_level=self.fidelity_level,
                high_certified=self.high_certified,
                repair_diagnostics=dict(self.repair_diagnostics),
                inner_state=self.inner_state,
            )


@dataclass
class ABMAResult:
    best: PartitionSolution
    history: List[float]
    references: Tuple[float, float, float]
    generations_completed: int
    early_stopped: bool
    elapsed_time: float
    diagnostics: Dict[str, object]


class RouteEvaluator:
    """Cached route-time evaluation with exact time-optimal direction decoding."""

    def __init__(self, welds: Sequence[Weld], model, exact_fast: bool = False):
        self.welds = list(welds)
        self.model = model
        self.exact_fast = exact_fast
        self.cache: Dict[Tuple[int, ...], Tuple[float, Dict[str, object]]] = {}
        self.inherited_cache: Optional[
            Dict[Tuple[int, ...], Tuple[float, Dict[str, object]]]
        ] = None
        self.inherited_index_map: Dict[int, int] = {}
        self.calls = 0
        self.hits = 0
        self.centers = [_weld_center(weld) for weld in self.welds] if exact_fast else []
        self.tie_keys = []
        self.weld_times: List[float] = []
        self.transition_times: Dict[Tuple[int, int, int, int], float] = {}
        self.transition_distances: Dict[Tuple[int, int, int, int], float] = {}
        if exact_fast:
            for weld in self.welds:
                endpoints = tuple(sorted((tuple(round(float(v), 10) for v in weld.start_point()),
                                          tuple(round(float(v), 10) for v in weld.end_point()))))
                self.tie_keys.append((str(getattr(weld, "parent_id", weld.id)), endpoints, str(weld.id)))
                self.weld_times.append(core.weld_time(weld, model))
            directed_endpoints = [
                ((weld.start_point(), weld.end_point()), (weld.end_point(), weld.start_point()))
                for weld in self.welds
            ]
            for previous in range(len(self.welds)):
                for current in range(len(self.welds)):
                    for previous_direction in (0, 1):
                        previous_end = directed_endpoints[previous][previous_direction][1]
                        for current_direction in (0, 1):
                            current_start = directed_endpoints[current][current_direction][0]
                            key = (previous, previous_direction, current, current_direction)
                            self.transition_times[key] = core.travel_time(previous_end, current_start, model)
                            self.transition_distances[key] = core.travel_distance(previous_end, current_start, model)

    def _evaluate_exact_fast(self, order: Tuple[int, ...]) -> Tuple[float, Dict[str, object]]:
        if not order:
            stats = core.empty_route_stats()
            return 0.0, stats
        n = len(order)
        dp = [[math.inf, math.inf] for _ in range(n)]
        parent = [[0, 0] for _ in range(n)]
        first_cost = self.weld_times[order[0]]
        dp[0] = [first_cost, first_cost]
        for position in range(1, n):
            current_task = order[position]
            current_weld_time = self.weld_times[current_task]
            previous_task = order[position - 1]
            for current_direction in (0, 1):
                for previous_direction in (0, 1):
                    candidate = (
                        dp[position - 1][previous_direction]
                        + self.transition_times[(previous_task, previous_direction, current_task, current_direction)]
                        + current_weld_time
                    )
                    if candidate < dp[position][current_direction]:
                        dp[position][current_direction] = candidate
                        parent[position][current_direction] = previous_direction
        direction = 0 if dp[-1][0] <= dp[-1][1] else 1
        directions = [False] * n
        for position in range(n - 1, -1, -1):
            directions[position] = bool(direction)
            if position > 0:
                direction = parent[position][direction]
        total_weld = sum(self.weld_times[task] for task in order)
        total_travel = 0.0
        total_distance = 0.0
        for position in range(1, n):
            key = (order[position - 1], int(directions[position - 1]),
                   order[position], int(directions[position]))
            total_travel += self.transition_times[key]
            total_distance += self.transition_distances[key]
        stats = {
            "total_time": total_weld + total_travel,
            "total_weld_time": total_weld,
            "total_travel_time": total_travel,
            "total_idle_distance": total_distance,
            "setup_post_saved_time": 0.0,
            "merged_setup_post_count": 0,
            "pure_weld_time": total_weld,
            "setup_post_time": 0.0,
            "reversed_weld_count": sum(1 for flag in directions if flag),
            "direction_mode": "bidirectional",
            "direction_decoder_objective": "minimum_route_time",
            "direction_flags": directions,
        }
        return float(stats["total_time"]), stats

    def evaluate(self, order: Sequence[int]) -> Tuple[float, Dict[str, object]]:
        self.calls += 1
        _profile_count("route_evaluation_count")
        key = tuple(order)
        with _profile_stage("time_cache_lookup"):
            cached = self.cache.get(key)
        if cached is not None:
            self.hits += 1
            _profile_count("route_cache_hit_count")
            return cached[0], dict(cached[1])
        if self.inherited_cache is not None and all(
            index in self.inherited_index_map for index in key
        ):
            inherited_key = tuple(self.inherited_index_map[index] for index in key)
            inherited = self.inherited_cache.get(inherited_key)
            if inherited is not None:
                self.hits += 1
                _profile_count("route_cache_hit_count")
                self.cache[key] = (inherited[0], dict(inherited[1]))
                return inherited[0], dict(inherited[1])
        _profile_count("route_cache_miss_count")
        _profile_count("direction_dp_count")
        with _profile_stage("time_route_evaluation"):
            with _profile_stage("time_direction_dp"):
                if self.exact_fast:
                    cost, stats = self._evaluate_exact_fast(key)
                else:
                    cost, stats = core.evaluate_order(self.welds, key, self.model)
        self.cache[key] = (float(cost), dict(stats))
        return float(cost), dict(stats)

    def travel_cost(self, order: Sequence[int]) -> float:
        """Order-dependent cost used by insertion/removal heuristics."""
        _, stats = self.evaluate(order)
        return float(stats.get("total_travel_time", 0.0))


def _weighted_choice(rng: random.Random, weights: Dict[str, float]) -> str:
    names = list(weights)
    total = sum(max(0.0, weights[name]) for name in names)
    if total <= 0.0:
        return rng.choice(names)
    threshold = rng.random() * total
    cumulative = 0.0
    for name in names:
        cumulative += max(0.0, weights[name])
        if cumulative >= threshold:
            return name
    return names[-1]


def _weld_center(weld: Weld) -> Tuple[float, float]:
    """Geometric descriptor used only by ALNS relatedness operators."""
    start = weld.start_point()
    end = weld.end_point()
    return (0.5 * (start[0] + end[0]), 0.5 * (start[1] + end[1]))


def _route_from_warm_ids(
    welds: Sequence[Weld],
    warm_ids: Optional[Sequence[str]],
) -> List[int]:
    if not warm_ids:
        return []
    by_id: Dict[str, List[int]] = {}
    for index, weld in enumerate(welds):
        by_id.setdefault(str(weld.id), []).append(index)
    route: List[int] = []
    for weld_id in warm_ids:
        candidates = by_id.get(str(weld_id), [])
        if candidates:
            route.append(candidates.pop(0))
    return route


def _repair_route(
    partial: Sequence[int],
    removed: Iterable[int],
    evaluator: RouteEvaluator,
    regret_k: int,
) -> List[int]:
    """Greedy or regret-k insertion based only on empty-travel time."""
    started = time.perf_counter() if _ACTIVE_PROFILER is not None else 0.0
    route = list(partial)
    pending = list(removed)
    def tie_key(task: int) -> Tuple[object, ...]:
        if evaluator.exact_fast:
            return evaluator.tie_keys[task]
        weld = evaluator.welds[task]
        endpoints = tuple(sorted((tuple(round(float(v), 10) for v in weld.start_point()),
                                  tuple(round(float(v), 10) for v in weld.end_point()))))
        return (str(getattr(weld, "parent_id", weld.id)), endpoints, str(weld.id))
    while pending:
        base_cost = evaluator.travel_cost(route)
        selected_task: Optional[int] = None
        selected_position = 0
        selected_regret = -math.inf
        selected_best_increment = math.inf

        for task in pending:
            insertion_costs: List[Tuple[float, int]] = []
            for position in range(len(route) + 1):
                candidate = list(route)
                candidate.insert(position, task)
                increment = evaluator.travel_cost(candidate) - base_cost
                insertion_costs.append((increment, position))
            insertion_costs.sort(key=lambda item: (item[0], item[1]))
            best_increment, best_position = insertion_costs[0]

            if regret_k <= 1:
                regret = 0.0
                choose = (
                    best_increment < selected_best_increment - EPS
                    or (
                        abs(best_increment - selected_best_increment) <= EPS
                        and (selected_task is None or tie_key(task) < tie_key(selected_task))
                    )
                )
            else:
                k = min(regret_k, len(insertion_costs))
                regret = sum(
                    insertion_costs[index][0] - best_increment
                    for index in range(1, k)
                )
                choose = (
                    regret > selected_regret + EPS
                    or (
                        abs(regret - selected_regret) <= EPS
                        and best_increment < selected_best_increment - EPS
                    )
                    or (
                        abs(regret - selected_regret) <= EPS
                        and abs(best_increment - selected_best_increment) <= EPS
                        and (selected_task is None or tie_key(task) < tie_key(selected_task))
                    )
                )

            if choose:
                selected_task = task
                selected_position = best_position
                selected_regret = regret
                selected_best_increment = best_increment

        assert selected_task is not None
        route.insert(selected_position, selected_task)
        pending.remove(selected_task)
    if _ACTIVE_PROFILER is not None:
        _ACTIVE_PROFILER.add_time("time_regret_insertion", time.perf_counter() - started)
    return route


def _destroy_route(
    order: Sequence[int],
    welds: Sequence[Weld],
    evaluator: RouteEvaluator,
    operator: str,
    remove_count: int,
    rng: random.Random,
    boundary_x: float,
) -> Tuple[List[int], List[int]]:
    route = list(order)
    if len(route) <= 1:
        return route, []
    remove_count = min(max(1, remove_count), len(route) - 1)

    if operator == "random":
        removed = rng.sample(route, remove_count)
    elif operator == "worst":
        base_cost = evaluator.travel_cost(route)
        savings: List[Tuple[float, int]] = []
        for task in route:
            candidate = list(route)
            candidate.remove(task)
            savings.append((base_cost - evaluator.travel_cost(candidate), task))
        removed = [
            task for _, task in sorted(savings, key=lambda item: (item[0], item[1]), reverse=True)[:remove_count]
        ]
    elif operator == "related":
        seed = rng.choice(route)
        seed_x, seed_y = evaluator.centers[seed] if evaluator.exact_fast else _weld_center(welds[seed])
        ranked = sorted(
            route,
            key=lambda task: math.hypot(
                (evaluator.centers[task][0] if evaluator.exact_fast else _weld_center(welds[task])[0]) - seed_x,
                (evaluator.centers[task][1] if evaluator.exact_fast else _weld_center(welds[task])[1]) - seed_y,
            ),
        )
        removed = ranked[:remove_count]
    elif operator == "boundary":
        removed = sorted(
            route,
            key=lambda task: abs((evaluator.centers[task][0] if evaluator.exact_fast else _weld_center(welds[task])[0]) - boundary_x),
        )[:remove_count]
    elif operator == "parent":
        seed = rng.choice(route)
        parent_id = getattr(welds[seed], "parent_id", None)
        same_parent = [
            task
            for task in route
            if getattr(welds[task], "parent_id", None) == parent_id
        ]
        remainder = [task for task in route if task not in same_parent]
        rng.shuffle(remainder)
        removed = (same_parent + remainder)[:remove_count]
    else:
        raise ValueError(f"unknown destroy operator: {operator}")

    removed_set = set(removed)
    return [task for task in route if task not in removed_set], removed


def _iter_vnd_candidates(order: Sequence[int], operator: str) -> Iterator[List[int]]:
    incumbent = list(order)
    n = len(incumbent)
    if operator == "two_opt":
        for i in range(n - 1):
            for j in range(i + 1, n):
                yield incumbent[:i] + list(reversed(incumbent[i : j + 1])) + incumbent[j + 1 :]
    elif operator == "relocate":
        for i in range(n):
            for j in range(n + 1):
                if j in (i, i + 1):
                    continue
                candidate = list(incumbent)
                task = candidate.pop(i)
                candidate.insert(j if j < i else j - 1, task)
                yield candidate
    elif operator == "swap":
        for i in range(n - 1):
            for j in range(i + 1, n):
                candidate = list(incumbent)
                candidate[i], candidate[j] = candidate[j], candidate[i]
                yield candidate
    elif operator == "or_opt2":
        if n < 3:
            return
        for i in range(n - 1):
            block = incumbent[i : i + 2]
            remainder = incumbent[:i] + incumbent[i + 2 :]
            for j in range(len(remainder) + 1):
                if j == i:
                    continue
                yield remainder[:j] + block + remainder[j:]
    else:
        raise ValueError(f"unknown VND operator: {operator}")


def _system_metrics(
    route_stats: Sequence[Dict[str, object]],
) -> Tuple[float, float, float]:
    started = time.perf_counter() if _ACTIVE_PROFILER is not None else 0.0
    times = [float(item.get("total_time", 0.0)) for item in route_stats]
    makespan = max(times) if times else 0.0
    load = makespan - min(times) if times else 0.0
    distance = sum(float(item.get("total_idle_distance", 0.0)) for item in route_stats)
    if _ACTIVE_PROFILER is not None:
        _ACTIVE_PROFILER.add_time("time_system_aggregation", time.perf_counter() - started)
    return makespan, load, distance


def _system_fitness(
    route_stats: Sequence[Dict[str, object]],
    config: ABMAConfig,
    references: Tuple[float, float, float],
) -> Tuple[float, float, float, float]:
    _profile_count("system_objective_count")
    makespan, load, distance = _system_metrics(route_stats)
    if config.normalization_mode == OFFICIAL_MODE:
        assert config.normalization_spec is not None
        metrics = {"makespan": makespan, "load_imbalance": load, "idle_distance": distance}
        if config.exact_fast_enabled:
            value = config.normalization_spec
            components: Dict[str, float] = {}
            for key in ("makespan", "load_imbalance", "idle_distance"):
                raw = float(metrics[key])
                ideal = float(value["ideal"][key])  # type: ignore[index]
                scale = float(value["scale"][key])  # type: ignore[index]
                delta = raw - ideal
                tolerance = max(1e-10, 1e-10 * max(abs(raw), abs(ideal), 1.0))
                if delta < -tolerance:
                    raise ValueError(f"{key}={raw} is below theoretical ideal {ideal}")
                components[key] = 0.0 if delta < 0.0 else delta / scale
            weights = tuple(float(item) for item in value.get("weights", (0.7, 0.2, 0.1)))
            fitness = sum(weight * components[key] for weight, key in zip(
                weights, ("makespan", "load_imbalance", "idle_distance")
            ))
        else:
            fitness = normalized_objective(metrics, config.normalization_spec)
        return fitness, makespan, load, distance
    ref_makespan, ref_load, ref_distance = references
    fitness = (
        config.weight_makespan * makespan / max(EPS, ref_makespan)
        + config.weight_load * load / max(EPS, ref_load)
        + config.weight_distance * distance / max(EPS, ref_distance)
    )
    return fitness, makespan, load, distance


def _initialize_joint_routes(
    robots: Sequence[Sequence[Weld]],
    evaluators: Sequence[RouteEvaluator],
    warm_solution: Optional[PartitionSolution],
) -> Tuple[List[List[int]], List[Dict[str, object]]]:
    orders: List[List[int]] = []
    stats: List[Dict[str, object]] = []
    for robot_index, robot_welds in enumerate(robots):
        warm_ids = None
        if warm_solution and robot_index < len(warm_solution.robot_order_ids):
            warm_ids = warm_solution.robot_order_ids[robot_index]
        warm_order = _route_from_warm_ids(robot_welds, warm_ids)
        present = set(warm_order)
        missing = [index for index in range(len(robot_welds)) if index not in present]
        order = _repair_route(warm_order, missing, evaluators[robot_index], regret_k=2)
        _, route_stats = evaluators[robot_index].evaluate(order)
        orders.append(order)
        stats.append(route_stats)
    return orders, stats


def optimize_partition_joint_alns(
    robots: Sequence[Sequence[Weld]],
    config: ABMAConfig,
    rng: random.Random,
    x_up: float,
    x_low: float,
    references: Tuple[float, float, float],
    warm_solution: Optional[PartitionSolution] = None,
) -> Tuple[List[List[int]], List[Dict[str, object]], List[Dict[str, object]]]:
    """Optimize four fixed robot routes with system-level ALNS acceptance.

    Task sets remain fixed by the outer partition. Each move changes one robot
    route, but acceptance and best-solution updates use the complete objective
    containing makespan, load imbalance, and total empty-travel distance.
    """
    model = config.motion_model()
    evaluators = [RouteEvaluator(welds, model, config.exact_fast_enabled) for welds in robots]
    current_orders, current_stats = _initialize_joint_routes(
        robots, evaluators, warm_solution
    )
    current_fitness, _, _, _ = _system_fitness(current_stats, config, references)
    best_orders = [list(order) for order in current_orders]
    best_stats = [dict(item) for item in current_stats]
    best_fitness = current_fitness

    destroy_weights = {
        name: 1.0 for name in ("random", "worst", "related", "boundary", "parent")
    }
    repair_weights = {name: 1.0 for name in ("greedy", "regret2", "regret3")}
    destroy_score = {name: 0.0 for name in destroy_weights}
    repair_score = {name: 0.0 for name in repair_weights}
    destroy_use = {name: 0 for name in destroy_weights}
    repair_use = {name: 0 for name in repair_weights}

    accepted_moves = 0
    improving_moves = 0
    global_improving_moves = 0
    iterations_completed = 0
    start_time = time.perf_counter()
    temperature = max(
        EPS, config.initial_temperature_ratio * max(EPS, current_fitness)
    )

    alns_started = time.perf_counter() if _ACTIVE_PROFILER is not None else 0.0
    for iteration in range(config.alns_iterations):
        if (
            config.route_time_limit_s > 0.0
            and time.perf_counter() - start_time >= config.route_time_limit_s
        ):
            break
        mutable_robots = [
            index for index, order in enumerate(current_orders) if len(order) > 1
        ]
        if not mutable_robots:
            break

        if rng.random() < config.critical_robot_rate:
            robot_index = max(
                mutable_robots,
                key=lambda index: float(current_stats[index].get("total_time", 0.0)),
            )
        else:
            robot_index = rng.choice(mutable_robots)

        destroy_name = _weighted_choice(rng, destroy_weights)
        repair_name = _weighted_choice(rng, repair_weights)
        fraction = rng.uniform(
            config.destroy_fraction_min, config.destroy_fraction_max
        )
        remove_count = max(1, int(round(len(current_orders[robot_index]) * fraction)))
        boundary_x = x_up if robot_index < 2 else x_low
        partial, removed = _destroy_route(
            current_orders[robot_index],
            robots[robot_index],
            evaluators[robot_index],
            destroy_name,
            remove_count,
            rng,
            boundary_x,
        )
        if not removed:
            continue
        regret_k = {"greedy": 1, "regret2": 2, "regret3": 3}[repair_name]
        candidate_order = _repair_route(
            partial, removed, evaluators[robot_index], regret_k
        )
        _, candidate_route_stats = evaluators[robot_index].evaluate(candidate_order)
        candidate_stats = (list(current_stats) if config.exact_fast_enabled
                           else [dict(item) for item in current_stats])
        candidate_stats[robot_index] = candidate_route_stats
        candidate_fitness, _, _, _ = _system_fitness(
            candidate_stats, config, references
        )

        delta = candidate_fitness - current_fitness
        accept = delta <= 0.0 or rng.random() < math.exp(
            -delta / max(EPS, temperature)
        )
        reward = 0.0
        if candidate_fitness < best_fitness - EPS:
            best_fitness = candidate_fitness
            best_orders = (list(current_orders) if config.exact_fast_enabled
                           else [list(order) for order in current_orders])
            best_orders[robot_index] = list(candidate_order)
            best_stats = (list(candidate_stats) if config.exact_fast_enabled
                          else [dict(item) for item in candidate_stats])
            global_improving_moves += 1
            reward = 8.0
        elif candidate_fitness < current_fitness - EPS:
            improving_moves += 1
            reward = 4.0
        elif accept:
            reward = 1.0

        if accept:
            current_orders[robot_index] = candidate_order
            current_stats = candidate_stats
            current_fitness = candidate_fitness
            accepted_moves += 1

        destroy_score[destroy_name] += reward
        repair_score[repair_name] += reward
        destroy_use[destroy_name] += 1
        repair_use[repair_name] += 1
        temperature *= config.cooling_rate
        iterations_completed = iteration + 1

        if (
            config.enable_alns_adaptation
            and iterations_completed % max(1, config.alns_segment_length) == 0
        ):
            rho = config.reaction_factor
            for name in destroy_weights:
                if destroy_use[name] > 0:
                    observed = destroy_score[name] / destroy_use[name]
                    destroy_weights[name] = (
                        (1.0 - rho) * destroy_weights[name]
                        + rho * max(0.1, observed)
                    )
                destroy_score[name] = 0.0
                destroy_use[name] = 0
            for name in repair_weights:
                if repair_use[name] > 0:
                    observed = repair_score[name] / repair_use[name]
                    repair_weights[name] = (
                        (1.0 - rho) * repair_weights[name]
                        + rho * max(0.1, observed)
                    )
                repair_score[name] = 0.0
                repair_use[name] = 0
    if _ACTIVE_PROFILER is not None:
        _ACTIVE_PROFILER.add_time("time_alns", time.perf_counter() - alns_started)

    # System-level VND intensification.
    vnd_started = time.perf_counter() if _ACTIVE_PROFILER is not None else 0.0
    operators = ("two_opt", "relocate", "swap", "or_opt2")
    for _ in range(max(0, config.vnd_iterations)):
        improved_round = False
        robot_sequence = sorted(
            range(len(best_orders)),
            key=lambda index: float(best_stats[index].get("total_time", 0.0)),
            reverse=True,
        )
        for robot_index in robot_sequence:
            if len(best_orders[robot_index]) <= 1:
                continue
            for operator in operators:
                checks = 0
                accepted_local = False
                for candidate_order in _iter_vnd_candidates(
                    best_orders[robot_index], operator
                ):
                    if checks >= config.max_move_checks_per_operator:
                        break
                    checks += 1
                    _, candidate_route_stats = evaluators[robot_index].evaluate(
                        candidate_order
                    )
                    candidate_stats = (list(best_stats) if config.exact_fast_enabled
                                       else [dict(item) for item in best_stats])
                    candidate_stats[robot_index] = candidate_route_stats
                    candidate_fitness, _, _, _ = _system_fitness(
                        candidate_stats, config, references
                    )
                    if candidate_fitness < best_fitness - EPS:
                        best_orders[robot_index] = list(candidate_order)
                        best_stats = candidate_stats
                        best_fitness = candidate_fitness
                        improved_round = True
                        accepted_local = True
                        break
                if accepted_local:
                    break
        if not improved_round:
            break
    if _ACTIVE_PROFILER is not None:
        _ACTIVE_PROFILER.add_time("time_vnd", time.perf_counter() - vnd_started)

    total_calls = sum(evaluator.calls for evaluator in evaluators)
    total_hits = sum(evaluator.hits for evaluator in evaluators)
    final_fitness, final_makespan, final_load, final_distance = _system_fitness(
        best_stats, config, references
    )
    joint_stats: Dict[str, object] = {
        "scope": "joint_four_robot",
        "acceptance_objective": "system_weighted_fitness",
        "direction_decoder_objective": "minimum_route_time",
        "iterations": iterations_completed,
        "accepted_moves": accepted_moves,
        "improving_moves": improving_moves,
        "global_improving_moves": global_improving_moves,
        "route_eval_count": total_calls,
        "route_cache_hit_count": total_hits,
        "route_cache_hit_rate": total_hits / total_calls if total_calls else 0.0,
        "destroy_weights": dict(destroy_weights),
        "repair_weights": dict(repair_weights),
        "final_inner_fitness": final_fitness,
        "final_makespan": final_makespan,
        "final_load_imbalance": final_load,
        "final_idle_distance": final_distance,
        "elapsed_time": time.perf_counter() - start_time,
    }
    return best_orders, best_stats, [joint_stats]


def _canonical_geometry(weld: Weld, tolerance: float) -> Tuple[object, ...]:
    """Stable sub-weld identity independent of generated ``_segN`` labels."""
    digits = max(0, min(12, int(round(-math.log10(tolerance)))))
    start = tuple(round(float(value), digits) for value in weld.start_point())
    end = tuple(round(float(value), digits) for value in weld.end_point())
    endpoints = tuple(sorted((start, end)))
    return (str(getattr(weld, "parent_id", weld.id)), endpoints)


def _stable_task_id(weld: Weld, tolerance: float) -> str:
    payload = json.dumps(_canonical_geometry(weld, tolerance), separators=(",", ":"))
    return hashlib.blake2b(payload.encode("utf-8"), digest_size=10).hexdigest()


@dataclass
class InnerSearchState:
    """Resumable route-search state shared by low/mid/high fidelity levels."""
    robots: List[List[Weld]]
    evaluators: List[RouteEvaluator]
    current_orders: List[List[int]]
    current_stats: List[Dict[str, object]]
    best_orders: List[List[int]]
    best_stats: List[Dict[str, object]]
    current_fitness: float
    best_fitness: float
    rng: random.Random
    destroy_weights: Dict[str, float]
    repair_weights: Dict[str, float]
    destroy_score: Dict[str, float]
    repair_score: Dict[str, float]
    destroy_use: Dict[str, int]
    repair_use: Dict[str, int]
    temperature: float
    alns_completed: int = 0
    vnd_completed: int = 0
    accepted_moves: int = 0
    improving_moves: int = 0
    global_improving_moves: int = 0
    affected_robots: List[int] = field(default_factory=list)
    repair_diagnostics: Dict[str, object] = field(default_factory=dict)
    elapsed_time: float = 0.0


@dataclass(frozen=True)
class PromotionFeatures:
    affected_task_ratio: float
    preserved_task_ratio: float
    boundary_delta: float
    generation_index: int
    population_boundary_diversity: float = math.inf


@dataclass(frozen=True)
class PromotionDecision:
    reject: bool
    adjusted_fitness: float
    uncertainty_quantile: float
    gray_zone_reasons: Tuple[str, ...]
    model_ready: bool


class AdaptivePromotionPolicy:
    """Deterministic audited upper-error bound for fidelity promotion.

    Every candidate that reaches high with both low and mid observations adds
    one error sample.  The one-sided 95% nearest-rank quantile is subtracted
    from a provisional fitness before it may be screened out.
    """

    def __init__(self, config: ABMAConfig):
        self.config = config
        self.records: List[Dict[str, object]] = []
        self.low_potentials: List[float] = []
        self.mid_potentials: List[float] = []

    @staticmethod
    def nearest_rank_quantile(values: Sequence[float], probability: float) -> float:
        if not values:
            return 0.0
        ordered = sorted(float(value) for value in values)
        rank = max(1, int(math.ceil(probability * len(ordered))))
        return ordered[min(len(ordered), rank) - 1]

    @property
    def q_low(self) -> float:
        return self.nearest_rank_quantile(self.low_potentials, self.config.audit_quantile)

    @property
    def q_mid(self) -> float:
        return self.nearest_rank_quantile(self.mid_potentials, self.config.audit_quantile)

    @property
    def ready(self) -> bool:
        required = self.config.minimum_audit_samples
        return len(self.low_potentials) >= required and len(self.mid_potentials) >= required

    def record(
        self, low_fitness: Optional[float], mid_fitness: Optional[float], high_fitness: float,
        features: PromotionFeatures,
    ) -> None:
        if low_fitness is None or mid_fitness is None:
            return
        low_potential = max(0.0, float(low_fitness) - float(high_fitness))
        mid_potential = max(0.0, float(mid_fitness) - float(high_fitness))
        self.low_potentials.append(low_potential)
        self.mid_potentials.append(mid_potential)
        self.records.append({
            "low_fitness": float(low_fitness), "mid_fitness": float(mid_fitness),
            "high_fitness": float(high_fitness),
            "low_to_high_improvement": low_potential,
            "mid_to_high_improvement": mid_potential,
            "affected_task_ratio": features.affected_task_ratio,
            "preserved_task_ratio": features.preserved_task_ratio,
            "boundary_delta": features.boundary_delta,
            "generation_index": features.generation_index,
        })

    def decide(
        self, level: str, observed_fitness: float, target_high_fitness: float,
        provisional_best: bool, features: PromotionFeatures,
    ) -> PromotionDecision:
        if level not in ("low", "mid"):
            raise ValueError("adaptive promotion level must be low or mid")
        quantile = self.q_low if level == "low" else self.q_mid
        reasons: List[str] = []
        if not self.ready:
            reasons.append("audit_model_warmup")
        if provisional_best:
            reasons.append("generation_provisional_best")
        if observed_fitness <= target_high_fitness * (1.0 + self.config.near_target_margin):
            reasons.append("near_target")
        if (features.boundary_delta <= self.config.small_boundary_delta
                and features.preserved_task_ratio >= self.config.high_preserved_task_ratio):
            reasons.append("small_boundary_high_preservation")
        if features.boundary_delta >= self.config.large_boundary_delta:
            reasons.append("large_boundary_uncertainty")
        if features.affected_task_ratio >= self.config.high_affected_task_ratio:
            reasons.append("high_affected_task_ratio")
        if features.population_boundary_diversity <= self.config.low_population_diversity:
            reasons.append("low_population_diversity")
        adjusted = max(0.0, observed_fitness - quantile)
        reject = bool(
            self.config.adaptive_promotion_enabled and self.ready and not reasons
            and adjusted > target_high_fitness + self.config.promotion_tolerance
        )
        return PromotionDecision(
            reject=reject, adjusted_fitness=adjusted,
            uncertainty_quantile=quantile, gray_zone_reasons=tuple(reasons),
            model_ready=self.ready,
        )

    def diagnostics(self) -> Dict[str, object]:
        return {
            "mode": "audited_conservative_quantile_v1",
            "quantile_probability": self.config.audit_quantile,
            "minimum_audit_samples": self.config.minimum_audit_samples,
            "valid_audit_sample_count": min(len(self.low_potentials), len(self.mid_potentials)),
            "low_audit_sample_count": len(self.low_potentials),
            "mid_audit_sample_count": len(self.mid_potentials),
            "q_low": self.q_low, "q_mid": self.q_mid,
            "model_ready": self.ready,
            "records": list(self.records),
        }


def _incremental_initial_routes(
    robots: Sequence[Sequence[Weld]],
    evaluators: Sequence[RouteEvaluator],
    config: ABMAConfig,
    parent: Optional[PartitionSolution],
    parent_robots: Optional[Sequence[Sequence[Weld]]],
    x_up: float,
    x_low: float,
) -> Tuple[List[List[int]], List[Dict[str, object]], Dict[str, object]]:
    """Inherit unchanged geometry and repair only boundary-affected robots."""
    if parent is None or parent_robots is None:
        orders, stats = _initialize_joint_routes(robots, evaluators, parent)
        return orders, stats, {
            "parent_available": False, "affected_robots": [0, 1, 2, 3],
            "preserved_task_count": 0, "inserted_task_count": sum(map(len, robots)),
            "removed_task_count": 0, "migrated_task_count": 0,
            "split_parent_count": 0, "merged_parent_count": 0,
            "unchanged_half_reuse_count": 0,
        }

    tolerance = config.geometry_tolerance
    old_locations: Dict[Tuple[object, ...], Tuple[int, int]] = {}
    old_id_to_key: Dict[str, Tuple[object, ...]] = {}
    parent_counts: Dict[str, int] = {}
    new_counts: Dict[str, int] = {}
    for robot_index, route in enumerate(parent_robots):
        for task_index, weld in enumerate(route):
            key = _canonical_geometry(weld, tolerance)
            old_locations[key] = (robot_index, task_index)
            old_id_to_key[str(weld.id)] = key
            pid = str(getattr(weld, "parent_id", weld.id))
            parent_counts[pid] = parent_counts.get(pid, 0) + 1
    new_by_key: Dict[Tuple[object, ...], Tuple[int, int]] = {}
    for robot_index, route in enumerate(robots):
        for task_index, weld in enumerate(route):
            new_by_key[_canonical_geometry(weld, tolerance)] = (robot_index, task_index)
            pid = str(getattr(weld, "parent_id", weld.id))
            new_counts[pid] = new_counts.get(pid, 0) + 1

    upper_changed = abs(parent.x_up - x_up) > tolerance
    lower_changed = abs(parent.x_low - x_low) > tolerance
    affected = ([0, 1] if upper_changed else []) + ([2, 3] if lower_changed else [])
    migrated = sum(
        1 for key, (old_robot, _) in old_locations.items()
        if key in new_by_key and new_by_key[key][0] != old_robot
    )
    preserved = 0
    inserted = 0
    orders: List[List[int]] = []
    stats: List[Dict[str, object]] = []
    unchanged_reuse = 0
    for robot_index, robot_welds in enumerate(robots):
        exact_same = (
            robot_index not in affected
            and robot_index < len(parent.robot_order_ids)
            and {str(w.id) for w in robot_welds} == set(parent.robot_order_ids[robot_index])
        )
        if exact_same:
            order = _route_from_warm_ids(robot_welds, parent.robot_order_ids[robot_index])
            route_stats = dict(parent.robots_stats[robot_index])
            unchanged_reuse += 1
        else:
            inherited: List[int] = []
            for old_id in parent.robot_order_ids[robot_index]:
                key = old_id_to_key.get(str(old_id))
                location = new_by_key.get(key) if key is not None else None
                if location is not None and location[0] == robot_index:
                    inherited.append(location[1])
            # Preserve relative order of every unchanged segment; only newly
            # created/migrated pieces are inserted by deterministic regret-2.
            inherited = list(dict.fromkeys(inherited))
            missing = [idx for idx in range(len(robot_welds)) if idx not in set(inherited)]
            missing.sort(key=lambda idx: _stable_task_id(robot_welds[idx], tolerance))
            order = _repair_route(inherited, missing, evaluators[robot_index], regret_k=2)
            _, route_stats = evaluators[robot_index].evaluate(order)
            preserved += len(inherited)
            inserted += len(missing)
        orders.append(order)
        stats.append(route_stats)
    old_keys, new_keys = set(old_locations), set(new_by_key)
    diagnostics = {
        "parent_available": True,
        "parent_boundary_distance": math.hypot(parent.x_up - x_up, parent.x_low - x_low),
        "upper_boundary_changed": upper_changed,
        "lower_boundary_changed": lower_changed,
        "affected_robots": affected,
        "preserved_task_count": preserved,
        "inserted_task_count": inserted,
        "removed_task_count": len(old_keys - new_keys),
        "new_geometry_task_count": len(new_keys - old_keys),
        "migrated_task_count": migrated,
        "split_parent_count": sum(new_counts.get(pid, 0) > count for pid, count in parent_counts.items()),
        "merged_parent_count": sum(new_counts.get(pid, 0) < count for pid, count in parent_counts.items()),
        "unchanged_half_reuse_count": unchanged_reuse,
    }
    return orders, stats, diagnostics


def _create_inner_state(
    robots: Sequence[Sequence[Weld]], config: ABMAConfig, rng: random.Random,
    x_up: float, x_low: float, references: Tuple[float, float, float],
    parent: Optional[PartitionSolution], parent_robots: Optional[Sequence[Sequence[Weld]]],
) -> InnerSearchState:
    evaluators = [RouteEvaluator(welds, config.motion_model()) for welds in robots]
    inherited_route_cache_robot_count = 0
    inherited_route_cache_entry_count = 0
    translated_route_cache_robot_count = 0
    if config.incremental_enabled and parent is not None and parent.inner_state is not None:
        parent_state = parent.inner_state
        for robot_index, evaluator in enumerate(evaluators):
            if robot_index >= len(parent_state.robots) or robot_index >= len(parent_state.evaluators):
                continue
            current_identity = tuple(
                (str(getattr(weld, "parent_id", weld.id)), _canonical_geometry(weld, config.geometry_tolerance))
                for weld in robots[robot_index]
            )
            parent_identity = tuple(
                (str(getattr(weld, "parent_id", weld.id)), _canonical_geometry(weld, config.geometry_tolerance))
                for weld in parent_state.robots[robot_index]
            )
            if current_identity == parent_identity:
                evaluator.cache = parent_state.evaluators[robot_index].cache
                inherited_route_cache_robot_count += 1
                inherited_route_cache_entry_count += len(evaluator.cache)
                continue
            parent_by_identity: Dict[Tuple[object, ...], List[int]] = {}
            for parent_index, identity in enumerate(parent_identity):
                parent_by_identity.setdefault(identity, []).append(parent_index)
            mapping = {
                child_index: parent_by_identity[identity][0]
                for child_index, identity in enumerate(current_identity)
                if len(parent_by_identity.get(identity, [])) == 1
            }
            if mapping:
                evaluator.inherited_cache = parent_state.evaluators[robot_index].cache
                evaluator.inherited_index_map = mapping
                translated_route_cache_robot_count += 1
                inherited_route_cache_entry_count += len(evaluator.inherited_cache)
    if config.incremental_enabled:
        orders, stats, repair = _incremental_initial_routes(
            robots, evaluators, config, parent, parent_robots, x_up, x_low
        )
        repair["inherited_route_cache_robot_count"] = inherited_route_cache_robot_count
        repair["translated_route_cache_robot_count"] = translated_route_cache_robot_count
        repair["inherited_route_cache_entry_count"] = inherited_route_cache_entry_count
    else:
        orders, stats = _initialize_joint_routes(robots, evaluators, parent)
        repair = {"parent_available": parent is not None, "affected_robots": [0, 1, 2, 3],
                  "incremental_disabled": True}
    fitness, _, _, _ = _system_fitness(stats, config, references)
    destroy = {name: 1.0 for name in ("random", "worst", "related", "boundary", "parent")}
    repair_weights = {name: 1.0 for name in ("greedy", "regret2", "regret3")}
    return InnerSearchState(
        robots=[list(route) for route in robots], evaluators=evaluators,
        current_orders=[list(order) for order in orders], current_stats=[dict(x) for x in stats],
        best_orders=[list(order) for order in orders], best_stats=[dict(x) for x in stats],
        current_fitness=fitness, best_fitness=fitness, rng=rng,
        destroy_weights=destroy, repair_weights=repair_weights,
        destroy_score={name: 0.0 for name in destroy},
        repair_score={name: 0.0 for name in repair_weights},
        destroy_use={name: 0 for name in destroy}, repair_use={name: 0 for name in repair_weights},
        temperature=max(EPS, config.initial_temperature_ratio * max(EPS, fitness)),
        affected_robots=list(repair.get("affected_robots", [0, 1, 2, 3])),
        repair_diagnostics=repair,
    )


def _advance_inner_state(
    state: InnerSearchState, config: ABMAConfig, x_up: float, x_low: float,
    references: Tuple[float, float, float], target_alns: int, target_vnd: int,
    fidelity: str,
) -> None:
    start = time.perf_counter()
    allowed = set(state.affected_robots)
    if fidelity == "mid":
        ranked = sorted(range(4), key=lambda i: float(state.best_stats[i].get("total_time", 0.0)))
        allowed.update((ranked[0], ranked[-1]))
    elif fidelity == "high":
        allowed.update(range(4))
    if not allowed:
        allowed.update(range(4))

    while state.alns_completed < target_alns:
        mutable = [i for i in sorted(allowed) if len(state.current_orders[i]) > 1]
        if not mutable:
            state.alns_completed = target_alns
            break
        if state.rng.random() < config.critical_robot_rate:
            robot = max(mutable, key=lambda i: float(state.current_stats[i].get("total_time", 0.0)))
        else:
            robot = state.rng.choice(mutable)
        destroy_name = _weighted_choice(state.rng, state.destroy_weights)
        repair_name = _weighted_choice(state.rng, state.repair_weights)
        fraction = state.rng.uniform(config.destroy_fraction_min, config.destroy_fraction_max)
        count = max(1, int(round(len(state.current_orders[robot]) * fraction)))
        partial, removed = _destroy_route(
            state.current_orders[robot], state.robots[robot], state.evaluators[robot],
            destroy_name, count, state.rng, x_up if robot < 2 else x_low,
        )
        if removed:
            order = _repair_route(partial, removed, state.evaluators[robot],
                                  {"greedy": 1, "regret2": 2, "regret3": 3}[repair_name])
            _, route_stats = state.evaluators[robot].evaluate(order)
            candidate_stats = [dict(item) for item in state.current_stats]
            candidate_stats[robot] = route_stats
            candidate_fitness, _, _, _ = _system_fitness(candidate_stats, config, references)
            delta = candidate_fitness - state.current_fitness
            accept = delta <= 0.0 or state.rng.random() < math.exp(-delta / max(EPS, state.temperature))
            reward = 0.0
            if candidate_fitness < state.best_fitness - EPS:
                state.best_fitness = candidate_fitness
                state.best_orders = [list(item) for item in state.current_orders]
                state.best_orders[robot] = list(order)
                state.best_stats = [dict(item) for item in candidate_stats]
                state.global_improving_moves += 1
                reward = 8.0
            elif candidate_fitness < state.current_fitness - EPS:
                state.improving_moves += 1
                reward = 4.0
            elif accept:
                reward = 1.0
            if accept:
                state.current_orders[robot] = order
                state.current_stats = candidate_stats
                state.current_fitness = candidate_fitness
                state.accepted_moves += 1
            state.destroy_score[destroy_name] += reward
            state.repair_score[repair_name] += reward
            state.destroy_use[destroy_name] += 1
            state.repair_use[repair_name] += 1
        state.temperature *= config.cooling_rate
        state.alns_completed += 1
        if config.enable_alns_adaptation and state.alns_completed % max(1, config.alns_segment_length) == 0:
            rho = config.reaction_factor
            for weights, scores, uses in (
                (state.destroy_weights, state.destroy_score, state.destroy_use),
                (state.repair_weights, state.repair_score, state.repair_use),
            ):
                for name in weights:
                    if uses[name]:
                        weights[name] = (1.0 - rho) * weights[name] + rho * max(0.1, scores[name] / uses[name])
                    scores[name] = 0.0
                    uses[name] = 0

    operators = ("two_opt", "relocate", "swap", "or_opt2")
    while state.vnd_completed < target_vnd:
        improved = False
        for robot in sorted(allowed, key=lambda i: float(state.best_stats[i].get("total_time", 0.0)), reverse=True):
            for operator in operators:
                for checks, order in enumerate(_iter_vnd_candidates(state.best_orders[robot], operator), 1):
                    if checks > config.max_move_checks_per_operator:
                        break
                    _, route_stats = state.evaluators[robot].evaluate(order)
                    stats = [dict(item) for item in state.best_stats]
                    stats[robot] = route_stats
                    fitness, _, _, _ = _system_fitness(stats, config, references)
                    if fitness < state.best_fitness - EPS:
                        state.best_orders[robot] = list(order)
                        state.best_stats = stats
                        state.best_fitness = fitness
                        improved = True
                        break
                if improved:
                    break
        state.vnd_completed += 1
        if not improved:
            # A completed local-optimum pass satisfies all later equal-scope
            # VND requests without repeating identical checks.
            state.vnd_completed = target_vnd
            break
    state.elapsed_time += time.perf_counter() - start


def _inner_state_stats(state: InnerSearchState, fidelity: str) -> List[Dict[str, object]]:
    calls = sum(item.calls for item in state.evaluators)
    hits = sum(item.hits for item in state.evaluators)
    return [{
        "scope": "joint_four_robot", "fidelity": fidelity,
        "iterations": state.alns_completed, "vnd_iterations": state.vnd_completed,
        "accepted_moves": state.accepted_moves, "improving_moves": state.improving_moves,
        "global_improving_moves": state.global_improving_moves,
        "route_eval_count": calls, "route_cache_hit_count": hits,
        "route_cache_hit_rate": hits / calls if calls else 0.0,
        "final_inner_fitness": state.best_fitness, "elapsed_time": state.elapsed_time,
        "destroy_weights": dict(state.destroy_weights), "repair_weights": dict(state.repair_weights),
    }]


def _stable_partition_seed(base_seed: int, key: Tuple[float, float]) -> int:
    payload = f"{base_seed}|{key[0]:.12f}|{key[1]:.12f}".encode("utf-8")
    return int.from_bytes(hashlib.blake2b(payload, digest_size=8).digest(), "big")


def _weighted_median(
    values: Sequence[Tuple[float, float]],
    default: float,
) -> float:
    if not values:
        return default
    ordered = sorted((x, max(0.0, weight)) for x, weight in values)
    total = sum(weight for _, weight in ordered)
    if total <= EPS:
        return ordered[len(ordered) // 2][0]
    threshold = 0.5 * total
    cumulative = 0.0
    for x, weight in ordered:
        cumulative += weight
        if cumulative >= threshold:
            return x
    return ordered[-1][0]


def _latin_hypercube_2d(
    rng: random.Random,
    count: int,
    lo: float,
    hi: float,
) -> List[Tuple[float, float]]:
    if count <= 0:
        return []
    first = [(index + rng.random()) / count for index in range(count)]
    second = [(index + rng.random()) / count for index in range(count)]
    rng.shuffle(second)
    span = hi - lo
    return [
        (lo + span * a, lo + span * b)
        for a, b in zip(first, second)
    ]


def _split_weld_at_horizontal_boundary(
    weld: Weld,
    boundary_y: float,
    eps: float,
) -> List[Tuple[str, Tuple[float, float, float], Tuple[float, float, float]]]:
    """Split only by the fixed upper/lower boundary for initialization.

    This helper does not assign by midpoint. It returns complete line segments
    that lie in the upper or lower half-plane.
    """
    start = weld.start_point()
    end = weld.end_point()
    y1, y2 = start[1], end[1]
    if y1 >= boundary_y - eps and y2 >= boundary_y - eps:
        return [("upper", start, end)]
    if y1 <= boundary_y + eps and y2 <= boundary_y + eps:
        return [("lower", start, end)]
    dy = y2 - y1
    if abs(dy) <= eps:
        return [("upper", start, end)]
    parameter = (boundary_y - y1) / dy
    intersection = tuple(
        start[index] + parameter * (end[index] - start[index])
        for index in range(3)
    )
    if y1 > boundary_y:
        return [("upper", start, intersection), ("lower", intersection, end)]
    return [("lower", start, intersection), ("upper", intersection, end)]


class ABMASolver:
    def __init__(
        self,
        welds: Sequence[Weld],
        config: Optional[ABMAConfig] = None,
    ):
        self.welds = list(welds)
        self.config = config or ABMAConfig()
        self.config.validate()
        self.rng = random.Random(self.config.seed)
        self.references = (1.0, 1.0, 1.0)
        self.reference_source = "unset"
        self.partition_cache: Dict[Tuple[float, float], PartitionSolution] = {}
        self.assignment_cache: Dict[Tuple[float, float], Tuple[List[List[Weld]], Dict[str, object]]] = {}
        self.assignment_cache_hits = 0
        self.assignment_cache_misses = 0
        self.warm_archive: List[PartitionSolution] = []
        self.cache_hits = 0
        self.cache_misses = 0
        self.partition_evaluations = 0
        self.objective_evaluations = 0
        self.trace = IncumbentTrace()
        self.trace_started = time.perf_counter()
        self.fidelity_counts = {"low": 0, "mid": 0, "high": 0}
        self.promotion_counts = {"low_to_mid": 0, "mid_to_high": 0}
        self.screened_rejections = 0
        self.audited_rejections = 0
        self.false_rejections = 0
        self.repair_totals: Dict[str, float] = {}
        self.promotion_policy = AdaptivePromotionPolicy(self.config)
        self.promotion_decision_count = 0
        self.gray_zone_promotion_count = 0
        self.candidate_sequence_digest = hashlib.sha256()
        self.acceptance_sequence_digest = hashlib.sha256()
        self.inner_rng_state_digest = hashlib.sha256()

    @staticmethod
    def _digest_update(digest, values: Sequence[object]) -> None:
        payload = json.dumps(list(values), ensure_ascii=False, separators=(",", ":"), default=str)
        digest.update(payload.encode("utf-8"))
        digest.update(b"\n")

    def _key(self, x_up: float, x_low: float) -> Tuple[float, float]:
        decimals = self.config.partition_cache_decimals
        return round(x_up, decimals), round(x_low, decimals)

    def _repair_boundary(self, value: float, parent: float) -> float:
        lo = max(0.0, self.config.boundary_margin)
        hi = min(PLATFORM_W_M, PLATFORM_W_M - self.config.boundary_margin)
        if value < lo:
            return 0.5 * (lo + parent)
        if value > hi:
            return 0.5 * (hi + parent)
        return value

    def _nearest_warm_solution(
        self,
        x_up: float,
        x_low: float,
    ) -> Optional[PartitionSolution]:
        if not self.config.enable_warm_start or not self.warm_archive:
            return None
        return min(
            self.warm_archive,
            key=lambda solution: (
                (solution.x_up - x_up) ** 2 + (solution.x_low - x_low) ** 2
            ),
        )

    def _fitness_values(
        self,
        makespan: float,
        load: float,
        distance: float,
    ) -> float:
        if self.config.normalization_mode == OFFICIAL_MODE:
            assert self.config.normalization_spec is not None
            return normalized_objective(
                {"makespan": makespan, "load_imbalance": load, "idle_distance": distance},
                self.config.normalization_spec,
            )
        ref_makespan, ref_load, ref_distance = self.references
        return (
            self.config.weight_makespan * makespan / max(EPS, ref_makespan)
            + self.config.weight_load * load / max(EPS, ref_load)
            + self.config.weight_distance * distance / max(EPS, ref_distance)
        )

    def _fitness(self, solution: PartitionSolution) -> float:
        return self._fitness_values(
            solution.makespan,
            solution.load_imbalance,
            solution.total_idle_distance,
        )

    def _check_assignment_feasibility(
        self,
        assignment_stats: Dict[str, object],
        key: Tuple[float, float],
    ) -> None:
        if int(assignment_stats.get("unassigned_subweld_count", 0)) > 0:
            raise RuntimeError(f"unassigned sub-weld at partition {key}")
        max_error = float(assignment_stats.get("max_length_error", math.inf))
        if max_error > self.config.geometry_tolerance:
            raise RuntimeError(
                f"weld-length conservation error {max_error:.3e} at partition {key}"
            )
        if int(assignment_stats.get("discarded_short_subweld_count", 0)) > 0:
            raise RuntimeError(f"a nonzero sub-weld was discarded at partition {key}")

    def _assign_partition(
        self,
        x_up: float,
        x_low: float,
    ) -> Tuple[List[List[Weld]], Dict[str, object]]:
        key = self._key(x_up, x_low)
        # Keep the legacy benchmark path scientifically and computationally
        # unchanged.  Partition reuse is an optimization owned by the two
        # multifidelity variants and must not leak into the speed baseline.
        if self.config.abma_variant in {"legacy", "legacy_exact_fast"}:
            _profile_count("partition_assignment_count")
            with _profile_stage("time_partition_assignment"):
                robots, stats = core.assign_welds_to_robots_split(
                    self.welds,
                    x_up,
                    x_low,
                    eps=self.config.split_epsilon,
                    min_subweld_length=self.config.min_subweld_length,
                )
            self._check_assignment_feasibility(stats, key)
            return robots, stats
        cached = self.assignment_cache.get(key)
        if cached is not None:
            self.assignment_cache_hits += 1
            _profile_count("assignment_cache_hit_count")
            robots, stats = cached
            return robots, dict(stats)
        self.assignment_cache_misses += 1
        _profile_count("assignment_cache_miss_count")
        _profile_count("partition_assignment_count")
        with _profile_stage("time_partition_assignment"):
            robots, stats = core.assign_welds_to_robots_split(
                self.welds,
                x_up,
                x_low,
                eps=self.config.split_epsilon,
                min_subweld_length=self.config.min_subweld_length,
            )
        self._check_assignment_feasibility(stats, key)
        self.assignment_cache[key] = (robots, dict(stats))
        return robots, stats

    def _deterministic_reference_metrics(self) -> Tuple[float, float, float]:
        center = 0.5 * PLATFORM_W_M
        robots, _ = self._assign_partition(center, center)
        model = self.config.motion_model()
        route_stats: List[Dict[str, object]] = []
        for robot_welds in robots:
            evaluator = RouteEvaluator(robot_welds, model)
            order = _repair_route(
                [], range(len(robot_welds)), evaluator, regret_k=2
            )
            _, stats = evaluator.evaluate(order)
            route_stats.append(stats)
        makespan, load, distance = _system_metrics(route_stats)
        # References must be strictly positive. The fallback is used only for
        # a degenerate zero-valued metric and is deterministic across solvers.
        return (
            max(1.0, makespan),
            max(1.0, load),
            max(1.0, distance),
        )

    def _initialize_references(self) -> None:
        if self.config.normalization_mode == OFFICIAL_MODE:
            self.references = (1.0, 1.0, 1.0)
            self.reference_source = "externally_fixed_unified_protocol"
            return
        explicit = self.config.explicit_references()
        if explicit is not None:
            self.references = explicit
            self.reference_source = "externally_fixed"
        else:
            self.references = self._deterministic_reference_metrics()
            self.reference_source = "deterministic_center_partition_baseline"

    def _legacy_evaluate(self, x_up: float, x_low: float) -> PartitionSolution:
        key = self._key(x_up, x_low)
        # The protocol budget counts outer complete-system objective
        # observations.  A cache hit avoids repeated internal route work but
        # is still one candidate observation by the ABMA search, just as route
        # caches inside the other solvers do not erase their outer evaluation.
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
        robots, assignment_stats = self._assign_partition(x_up, x_low)
        warm_solution = self._nearest_warm_solution(x_up, x_low)
        local_rng = random.Random(_stable_partition_seed(self.config.seed, key))
        orders, route_stats, alns_stats = optimize_partition_joint_alns(
            robots,
            self.config,
            local_rng,
            x_up,
            x_low,
            self.references,
            warm_solution=warm_solution,
        )
        self._digest_update(self.inner_rng_state_digest, [key, repr(local_rng.getstate())])
        makespan, load, distance = _system_metrics(route_stats)
        solution = PartitionSolution(
            x_up=x_up,
            x_low=x_low,
            makespan=makespan,
            load_imbalance=load,
            total_idle_distance=distance,
            fitness=self._fitness_values(makespan, load, distance),
            robots_stats=[dict(item) for item in route_stats],
            robot_orders=[list(order) for order in orders],
            robot_order_ids=[],
            robot_direction_flags=[
                [bool(flag) for flag in stats.get("direction_flags", [])]
                for stats in route_stats
            ],
            assignment_stats=assignment_stats,
            alns_stats=alns_stats,
        )
        with _profile_stage("time_subweld_identity_mapping"):
            solution.robot_order_ids = [
                [str(robots[index][task].id) for task in orders[index]]
                for index in range(4)
            ]

        self.warm_archive.append(solution.clone())
        max_archive = max(20, 4 * self.config.population_size)
        if len(self.warm_archive) > max_archive:
            self.warm_archive = sorted(
                self.warm_archive, key=lambda item: item.fitness
            )[:max_archive]
        if self.config.enable_partition_cache:
            self.partition_cache[key] = solution.clone()
        self.trace.observe(self.objective_evaluations, solution.fitness, solution.makespan,
                           solution.load_imbalance, solution.total_idle_distance,
                           time.perf_counter() - self.trace_started)
        return solution

    def _fidelity_budget(self, level: str) -> Tuple[int, int]:
        return {
            "low": (self.config.low_alns_iterations, self.config.low_vnd_iterations),
            "mid": (self.config.mid_alns_iterations, self.config.mid_vnd_iterations),
            "high": (self.config.high_alns_iterations, self.config.high_vnd_iterations),
        }[level]

    def _refresh_solution_from_state(
        self, solution: PartitionSolution, robots: Sequence[Sequence[Weld]], level: str
    ) -> PartitionSolution:
        state = solution.inner_state
        assert state is not None
        makespan, load, distance = _system_metrics(state.best_stats)
        solution.makespan = makespan
        solution.load_imbalance = load
        solution.total_idle_distance = distance
        solution.fitness = self._fitness_values(makespan, load, distance)
        solution.robots_stats = [dict(item) for item in state.best_stats]
        solution.robot_orders = [list(order) for order in state.best_orders]
        solution.robot_order_ids = [
            [str(robots[index][task].id) for task in state.best_orders[index]]
            for index in range(4)
        ]
        solution.robot_direction_flags = [
            [bool(flag) for flag in stats.get("direction_flags", [])]
            for stats in state.best_stats
        ]
        solution.alns_stats = _inner_state_stats(state, level)
        solution.fidelity_level = level
        solution.high_certified = level == "high"
        solution.repair_diagnostics = dict(state.repair_diagnostics)
        return solution

    def _promote_solution(
        self, solution: PartitionSolution, robots: Sequence[Sequence[Weld]], level: str
    ) -> PartitionSolution:
        order = {"low": 0, "mid": 1, "high": 2}
        if order.get(solution.fidelity_level, -1) >= order[level]:
            return solution
        state = solution.inner_state
        assert state is not None
        target_alns, target_vnd = self._fidelity_budget(level)
        if level == "high":
            _profile_count("high_fidelity_promotion_count")
        _advance_inner_state(
            state, self.config, solution.x_up, solution.x_low, self.references,
            target_alns, target_vnd, level,
        )
        self.fidelity_counts[level] += 1
        solution = self._refresh_solution_from_state(solution, robots, level)
        if self.config.enable_partition_cache:
            self.partition_cache[self._key(solution.x_up, solution.x_low)] = solution.clone()
        return solution

    def _optimized_evaluate(
        self, x_up: float, x_low: float, parent: Optional[PartitionSolution],
        required_fidelity: str,
    ) -> PartitionSolution:
        key = self._key(x_up, x_low)
        self.objective_evaluations += 1
        cached = self.partition_cache.get(key) if self.config.enable_partition_cache else None
        if cached is not None:
            self.cache_hits += 1
            solution = cached.clone()
            robots, _ = self._assign_partition(x_up, x_low)
        else:
            self.cache_misses += 1
            self.partition_evaluations += 1
            robots, assignment_stats = self._assign_partition(x_up, x_low)
            warm = parent or self._nearest_warm_solution(x_up, x_low)
            parent_robots = None
            if warm is not None:
                parent_robots, _ = self._assign_partition(warm.x_up, warm.x_low)
            seed = _stable_partition_seed(self.config.seed, key)
            state = _create_inner_state(
                robots, self.config, random.Random(seed), x_up, x_low,
                self.references, warm, parent_robots,
            )
            solution = PartitionSolution(
                x_up=x_up, x_low=x_low, assignment_stats=assignment_stats,
                fidelity_level="none", high_certified=False, inner_state=state,
                repair_diagnostics=dict(state.repair_diagnostics),
            )
        solution = self._promote_solution(solution, robots, required_fidelity)
        if self.config.enable_partition_cache:
            incumbent = self.partition_cache.get(key)
            levels = {"none": -1, "low": 0, "mid": 1, "high": 2}
            if incumbent is None or levels[solution.fidelity_level] >= levels[incumbent.fidelity_level]:
                self.partition_cache[key] = solution.clone()
        if solution.high_certified:
            self.warm_archive.append(solution.clone())
            max_archive = max(20, 4 * self.config.population_size)
            if len(self.warm_archive) > max_archive:
                self.warm_archive = sorted(self.warm_archive, key=lambda item: item.fitness)[:max_archive]
        for name, value in solution.repair_diagnostics.items():
            if isinstance(value, bool):
                self.repair_totals[name] = self.repair_totals.get(name, 0.0) + float(value)
            elif isinstance(value, (int, float)):
                self.repair_totals[name] = self.repair_totals.get(name, 0.0) + float(value)
        self.trace.observe(
            self.objective_evaluations, solution.fitness, solution.makespan,
            solution.load_imbalance, solution.total_idle_distance,
            time.perf_counter() - self.trace_started,
        )
        return solution

    def _raw_evaluate(
        self, x_up: float, x_low: float, parent: Optional[PartitionSolution] = None,
        required_fidelity: str = "high",
    ) -> PartitionSolution:
        self._digest_update(self.candidate_sequence_digest, [
            self.objective_evaluations + 1, float(x_up).hex(), float(x_low).hex(),
            required_fidelity,
        ])
        with _profile_stage("time_outer_de"):
            if self.config.abma_variant in {"legacy", "legacy_exact_fast"}:
                return self._legacy_evaluate(x_up, x_low)
            if not self.config.multifidelity_enabled:
                required_fidelity = "high"
                # incremental_only deliberately preserves the historical inner budget.
                old = (self.config.high_alns_iterations, self.config.high_vnd_iterations)
                self.config.high_alns_iterations, self.config.high_vnd_iterations = (
                    self.config.alns_iterations, self.config.vnd_iterations
                )
                try:
                    return self._optimized_evaluate(x_up, x_low, parent, required_fidelity)
                finally:
                    self.config.high_alns_iterations, self.config.high_vnd_iterations = old
            return self._optimized_evaluate(x_up, x_low, parent, required_fidelity)

    def _promotion_features(
        self, solution: PartitionSolution, parent: PartitionSolution, generation: int,
        population_diversity: float,
    ) -> PromotionFeatures:
        diagnostics = solution.repair_diagnostics
        inserted = float(diagnostics.get("inserted_task_count", 0.0))
        preserved = float(diagnostics.get("preserved_task_count", 0.0))
        migrated = float(diagnostics.get("migrated_task_count", 0.0))
        total = max(1.0, float(solution.assignment_stats.get("subweld_count", inserted + preserved)))
        affected = min(1.0, max(0.0, (inserted + migrated) / total))
        preserved_ratio = preserved / max(1.0, preserved + inserted)
        boundary_delta = math.hypot(solution.x_up - parent.x_up, solution.x_low - parent.x_low)
        return PromotionFeatures(
            affected_task_ratio=affected,
            preserved_task_ratio=preserved_ratio,
            boundary_delta=boundary_delta,
            generation_index=generation,
            population_boundary_diversity=population_diversity,
        )

    def _sample_f(self, mean: float) -> float:
        while True:
            value = mean + 0.1 * math.tan(
                math.pi * (self.rng.random() - 0.5)
            )
            if value > 0.0:
                return min(1.0, value)

    def _sample_cr(self, mean: float) -> float:
        return min(1.0, max(0.0, self.rng.gauss(mean, 0.1)))

    def _informed_boundary_estimate(self) -> Tuple[float, float]:
        boundary_y = core.partition_world_y()
        upper_values: List[Tuple[float, float]] = []
        lower_values: List[Tuple[float, float]] = []
        for weld in self.welds:
            for half, start, end in _split_weld_at_horizontal_boundary(
                weld, boundary_y, self.config.split_epsilon
            ):
                length = math.dist(start, end)
                if length <= self.config.split_epsilon:
                    continue
                target = upper_values if half == "upper" else lower_values
                # Use both segment endpoints, not midpoint-based assignment.
                target.append((start[0], 0.5 * length))
                target.append((end[0], 0.5 * length))
        center = 0.5 * PLATFORM_W_M
        return (
            _weighted_median(upper_values, center),
            _weighted_median(lower_values, center),
        )

    def _initial_partition_vectors(
        self,
        lo: float,
        hi: float,
    ) -> List[Tuple[float, float]]:
        vectors: List[Tuple[float, float]] = []
        if self.config.use_problem_informed_initialization:
            center = 0.5 * (lo + hi)
            vectors.append((center, center))
            upper, lower = self._informed_boundary_estimate()
            vectors.append(
                (
                    min(hi, max(lo, upper)),
                    min(hi, max(lo, lower)),
                )
            )
        remaining = self.config.population_size - len(vectors)
        vectors.extend(_latin_hypercube_2d(self.rng, remaining, lo, hi))
        return vectors[: self.config.population_size]

    def solve(self) -> ABMAResult:
        start = time.perf_counter()
        self.trace_started = start
        self._initialize_references()
        if (
            self.config.max_objective_evaluations
            and self.config.max_objective_evaluations < self.config.population_size
        ):
            raise ValueError(
                "max_objective_evaluations must be 0 or at least population_size"
            )
        lo = max(0.0, self.config.boundary_margin)
        hi = min(PLATFORM_W_M, PLATFORM_W_M - self.config.boundary_margin)
        population = [
            self._raw_evaluate(x_up, x_low)
            for x_up, x_low in self._initial_partition_vectors(lo, hi)
        ]

        memory_f = [0.5] * self.config.memory_size
        memory_cr = [0.5] * self.config.memory_size
        memory_index = 0
        external_archive: List[PartitionSolution] = []
        best = min(population, key=lambda solution: solution.fitness).clone()
        history = [best.fitness]
        no_improve = 0
        early_stopped = False
        generations_completed = 0
        budget_exhausted = False

        for generation in range(self.config.generations):
            ranked = sorted(population, key=lambda solution: solution.fitness)
            center_up = sum(item.x_up for item in population) / len(population)
            center_low = sum(item.x_low for item in population) / len(population)
            population_diversity = math.sqrt(sum(
                (item.x_up - center_up) ** 2 + (item.x_low - center_low) ** 2
                for item in population
            ) / len(population)) / max(EPS, PLATFORM_W_M)
            p_count = max(
                2, int(math.ceil(self.config.p_best_rate * len(population)))
            )
            next_population: List[PartitionSolution] = []
            successful_f: List[float] = []
            successful_cr: List[float] = []
            improvements: List[float] = []
            provisional_low_best = math.inf
            provisional_mid_best = math.inf

            for index, target in enumerate(population):
                if (
                    self.config.max_objective_evaluations
                    and self.objective_evaluations
                    >= self.config.max_objective_evaluations
                ):
                    budget_exhausted = True
                    next_population.extend(population[index:])
                    break
                memory_slot = self.rng.randrange(self.config.memory_size)
                if self.config.enable_shade_adaptation:
                    f_value = self._sample_f(memory_f[memory_slot])
                    cr_value = self._sample_cr(memory_cr[memory_slot])
                else:
                    f_value = 0.5
                    cr_value = 0.9

                pbest = self.rng.choice(ranked[:p_count])
                candidate_indices = [
                    item for item in range(len(population)) if item != index
                ]
                r1_index = self.rng.choice(candidate_indices)
                r1 = population[r1_index]
                union = [
                    solution
                    for item, solution in enumerate(population)
                    if item not in (index, r1_index)
                ] + external_archive
                r2 = self.rng.choice(union) if union else population[
                    self.rng.choice(candidate_indices)
                ]

                mutant = (
                    target.x_up
                    + f_value * (pbest.x_up - target.x_up)
                    + f_value * (r1.x_up - r2.x_up),
                    target.x_low
                    + f_value * (pbest.x_low - target.x_low)
                    + f_value * (r1.x_low - r2.x_low),
                )
                forced_dimension = self.rng.randrange(2)
                target_vector = (target.x_up, target.x_low)
                trial_vector = [
                    mutant[dimension]
                    if dimension == forced_dimension or self.rng.random() < cr_value
                    else target_vector[dimension]
                    for dimension in range(2)
                ]
                trial = self._raw_evaluate(
                    self._repair_boundary(trial_vector[0], target.x_up),
                    self._repair_boundary(trial_vector[1], target.x_low),
                    parent=target,
                    required_fidelity=("low" if self.config.multifidelity_enabled else "high"),
                )

                if self.config.multifidelity_enabled and not trial.high_certified:
                    robots, _ = self._assign_partition(trial.x_up, trial.x_low)
                    features = self._promotion_features(
                        trial, target, generation, population_diversity
                    )
                    low_fitness = trial.fitness
                    low_is_provisional = low_fitness < provisional_low_best - EPS
                    provisional_low_best = min(provisional_low_best, trial.fitness)
                    self.promotion_decision_count += 1
                    scheduled_audit = (
                        self.promotion_decision_count % self.config.rejected_audit_interval == 0
                    )
                    low_decision = self.promotion_policy.decide(
                        "low", low_fitness, target.fitness, low_is_provisional, features
                    )
                    if low_decision.gray_zone_reasons:
                        self.gray_zone_promotion_count += 1
                    mid_fitness: Optional[float] = None
                    mid_decision: Optional[PromotionDecision] = None
                    if scheduled_audit or not low_decision.reject:
                        self.promotion_counts["low_to_mid"] += 1
                        trial = self._promote_solution(trial, robots, "mid")
                        mid_fitness = trial.fitness
                        mid_is_provisional = mid_fitness < provisional_mid_best - EPS
                        provisional_mid_best = min(provisional_mid_best, trial.fitness)
                        mid_decision = self.promotion_policy.decide(
                            "mid", mid_fitness, target.fitness, mid_is_provisional, features
                        )
                        if mid_decision.gray_zone_reasons:
                            self.gray_zone_promotion_count += 1
                        if scheduled_audit or not mid_decision.reject:
                            self.promotion_counts["mid_to_high"] += 1
                            trial = self._promote_solution(trial, robots, "high")

                    counterfactual_reject = low_decision.reject or bool(
                        mid_decision is not None and mid_decision.reject
                    )
                    if trial.high_certified:
                        self.promotion_policy.record(
                            low_fitness, mid_fitness, trial.fitness, features
                        )
                        if scheduled_audit and counterfactual_reject:
                            self.audited_rejections += 1
                            if trial.fitness <= target.fitness:
                                self.false_rejections += 1
                    else:
                        self.screened_rejections += 1
                        next_population.append(target)
                        continue

                selected = trial.fitness <= target.fitness
                self._digest_update(self.acceptance_sequence_digest, [
                    generation, index, selected, float(target.fitness).hex(),
                    float(trial.fitness).hex(),
                ])
                if selected:
                    next_population.append(trial)
                    external_archive.append(target.clone())
                    improvement = target.fitness - trial.fitness
                    if improvement > EPS:
                        successful_f.append(f_value)
                        successful_cr.append(cr_value)
                        improvements.append(improvement)
                else:
                    next_population.append(target)

            population = next_population
            max_external = max(
                1, int(self.config.archive_rate * self.config.population_size)
            )
            if len(external_archive) > max_external:
                external_archive = self.rng.sample(
                    external_archive, max_external
                )

            if self.config.enable_shade_adaptation and successful_f:
                total_improvement = sum(improvements)
                weights = [
                    improvement / total_improvement
                    for improvement in improvements
                ]
                denominator = sum(
                    weight * value
                    for weight, value in zip(weights, successful_f)
                )
                memory_f[memory_index] = (
                    sum(
                        weight * value * value
                        for weight, value in zip(weights, successful_f)
                    )
                    / max(EPS, denominator)
                )
                memory_cr[memory_index] = sum(
                    weight * value
                    for weight, value in zip(weights, successful_cr)
                )
                memory_index = (memory_index + 1) % self.config.memory_size

            generation_best = min(
                population, key=lambda solution: solution.fitness
            )
            generations_completed = generation + 1
            if (
                best.fitness - generation_best.fitness
                > self.config.early_stop_min_delta
            ):
                best = generation_best.clone()
                no_improve = 0
            else:
                no_improve += 1
            history.append(best.fitness)

            if self.config.verbose:
                print(
                    f"ABMA generation {generation + 1}/{self.config.generations}: "
                    f"fitness={best.fitness:.6f}, makespan={best.makespan:.2f}, "
                    f"x_up={best.x_up:.3f}, x_low={best.x_low:.3f}"
                )
            if (
                self.config.early_stop_patience > 0
                and no_improve >= self.config.early_stop_patience
            ):
                early_stopped = True
                break
            if budget_exhausted:
                break

        budget_exhausted = budget_exhausted or bool(
            self.config.max_objective_evaluations
            and self.objective_evaluations >= self.config.max_objective_evaluations
        )
        stop_reason = ("objective_budget" if budget_exhausted else
                       "early_stopping" if early_stopped else "generation_limit")
        diagnostics = {
            "partition_cache_hits": self.cache_hits,
            "partition_cache_misses": self.cache_misses,
            "partition_cache_hit_rate": self.cache_hits
            / max(1, self.cache_hits + self.cache_misses),
            "assignment_cache_hits": self.assignment_cache_hits,
            "assignment_cache_misses": self.assignment_cache_misses,
            "assignment_cache_hit_rate": self.assignment_cache_hits
            / max(1, self.assignment_cache_hits + self.assignment_cache_misses),
            "warm_archive_size": len(self.warm_archive),
            "shade_memory_f": memory_f,
            "shade_memory_cr": memory_cr,
            "no_improve_generations": no_improve,
            "partition_evaluations": self.partition_evaluations,
            "objective_evaluation_count": self.objective_evaluations,
            "max_objective_evaluations": self.config.max_objective_evaluations,
            "objective_budget_exhausted": budget_exhausted,
            "stop_reason": stop_reason,
            "checkpoint_trace": self.trace.records,
            "shade_adaptation_enabled": self.config.enable_shade_adaptation,
            "alns_adaptation_enabled": self.config.enable_alns_adaptation,
            "inner_acceptance_scope": "joint_four_robot_system_objective",
            "direction_decoder_objective": "minimum_route_time",
            "reference_source": self.reference_source,
            "abma_variant": self.config.abma_variant,
            "candidate_name": (
                "ABMA-Incremental-MultiFidelity-v2"
                if self.config.abma_variant == "incremental_multifidelity"
                else f"ABMA-{self.config.abma_variant}"
            ),
            "incremental_repair_enabled": self.config.incremental_enabled,
            "multifidelity_enabled": self.config.multifidelity_enabled,
            "fidelity_evaluation_counts": dict(self.fidelity_counts),
            "promotion_counts": dict(self.promotion_counts),
            "screened_rejection_count": self.screened_rejections,
            "audited_rejection_count": self.audited_rejections,
            "false_rejection_count": self.false_rejections,
            "false_rejection_rate": self.false_rejections / max(1, self.audited_rejections),
            "promotion_decision_count": self.promotion_decision_count,
            "gray_zone_promotion_count": self.gray_zone_promotion_count,
            "adaptive_promotion": self.promotion_policy.diagnostics(),
            "high_certification_invariant": all(item.high_certified for item in population),
            "repair_diagnostic_totals": dict(self.repair_totals),
            "outer_rng_stream": "python_random_seeded_by_solver_seed",
            "inner_rng_stream": "blake2b_solver_seed_and_partition_key",
            "candidate_sequence_hash": self.candidate_sequence_digest.hexdigest(),
            "candidate_acceptance_hash": self.acceptance_sequence_digest.hexdigest(),
            "outer_rng_final_state_hash": hashlib.sha256(
                repr(self.rng.getstate()).encode("utf-8")
            ).hexdigest(),
            "inner_rng_final_state_hash": self.inner_rng_state_digest.hexdigest(),
            "abma_final_profile": abma_profile_payload(self.config),
            "profile_hash": abma_profile_hash(self.config),
        }
        return ABMAResult(
            best=best,
            history=history,
            references=self.references,
            generations_completed=generations_completed,
            early_stopped=early_stopped,
            elapsed_time=time.perf_counter() - start,
            diagnostics=diagnostics,
        )


def collect_metrics(
    result: ABMAResult,
    config: ABMAConfig,
    weld_count: int,
) -> Dict[str, object]:
    best = result.best
    total_travel_time = sum(
        float(item.get("total_travel_time", 0.0)) for item in best.robots_stats
    )
    total_weld_time = sum(
        float(item.get("total_weld_time", 0.0)) for item in best.robots_stats
    )
    reversed_count = sum(
        int(item.get("reversed_weld_count", 0)) for item in best.robots_stats
    )
    assignment = best.assignment_stats
    return {
        "solver_name": SOLVER_NAME,
        "seed": config.seed,
        "weld_count": weld_count,
        "x_up": best.x_up,
        "x_low": best.x_low,
        "fitness": best.fitness,
        "makespan": best.makespan,
        "load_imbalance": best.load_imbalance,
        "total_idle_distance": best.total_idle_distance,
        "total_travel_time": total_travel_time,
        "total_weld_time": total_weld_time,
        "reversed_weld_count": reversed_count,
        "subweld_count": assignment.get("subweld_count", 0),
        "split_weld_count": assignment.get("split_weld_count", 0),
        "short_subweld_count": assignment.get("short_subweld_count", 0),
        "unassigned_subweld_count": assignment.get(
            "unassigned_subweld_count", 0
        ),
        "max_length_error": assignment.get("max_length_error", 0.0),
        "population_size": config.population_size,
        "max_generations": config.generations,
        "generations_completed": result.generations_completed,
        "early_stopped": result.early_stopped,
        "alns_iterations": config.alns_iterations,
        "elapsed_time": result.elapsed_time,
        "weld_speed": config.weld_speed,
        "travel_speed": config.travel_speed,
        "acceleration": config.acceleration,
        "safe_z": config.safe_z,
        "weight_makespan": config.weight_makespan,
        "weight_load": config.weight_load,
        "weight_distance": config.weight_distance,
        "time_model": "corrected",
        "assignment_mode": "split",
        "split_timing_mode": "parent_aware",
        "direction_mode": "bidirectional",
        "route_type": "open",
        "reference_makespan": result.references[0],
        "reference_load": result.references[1],
        "reference_distance": result.references[2],
        "ref_makespan": result.references[0],
        "ref_load": result.references[1],
        "ref_distance": result.references[2],
        **result.diagnostics,
    }


def save_metrics_csv(metrics: Dict[str, object], path: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    exists = os.path.exists(path)
    csv_metrics = dict(metrics)
    adaptive = csv_metrics.get("adaptive_promotion")
    if isinstance(adaptive, dict):
        # The complete per-audit trace remains in result.json.  A CSV cell is
        # not a suitable container for thousands of structured audit records
        # and can exceed the standard-library parser field limit, so retain
        # only the deterministic aggregate diagnostics in metrics.csv.
        csv_adaptive = dict(adaptive)
        records = csv_adaptive.pop("records", [])
        csv_adaptive["record_count"] = len(records) if isinstance(records, list) else 0
        csv_metrics["adaptive_promotion"] = json.dumps(
            csv_adaptive,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    with open(path, "a", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(csv_metrics))
        if not exists:
            writer.writeheader()
        writer.writerow(csv_metrics)


def _self_check() -> None:
    z = 0.1
    welds = [
        Weld("a", 1.0, 9.0, z, 2.0, 9.0, z),
        Weld("b", 4.0, 10.0, z, 5.0, 10.0, z),
        Weld("c", 7.0, 3.0, z, 8.0, 3.0, z),
        Weld("d", 12.0, 4.0, z, 13.0, 4.0, z),
        Weld("e", 16.0, 9.0, z, 17.0, 9.0, z),
        Weld("cross", 9.0, 8.0, z, 11.0, 8.0, z),
        Weld("cross_y", 3.0, 5.0, z, 4.0, 7.0, z),
    ]
    config = ABMAConfig(
        population_size=4,
        generations=1,
        alns_iterations=6,
        vnd_iterations=1,
        max_move_checks_per_operator=10,
        early_stop_patience=0,
        verbose=False,
    )
    result = ABMASolver(welds, config).solve()
    assert math.isfinite(result.best.fitness)
    assert result.best.makespan >= 0.0
    assert int(result.best.assignment_stats.get("unassigned_subweld_count", 0)) == 0
    assert float(result.best.assignment_stats.get("max_length_error", math.inf)) <= config.geometry_tolerance
    assert len(result.best.robot_orders) == 4
    assert len(result.best.robot_direction_flags) == 4
    for order, flags in zip(
        result.best.robot_orders, result.best.robot_direction_flags
    ):
        assert sorted(order) == list(range(len(order)))
        assert len(order) == len(flags)


def main() -> None:
    global _ACTIVE_PROFILER
    parser = argparse.ArgumentParser(
        description="Adaptive bilevel memetic algorithm for MRTA welding"
    )
    parser.add_argument("--instance-path", help="Frozen instance XLSX (formal mode).")
    parser.add_argument("--source-excel", help="Raw assembly XLSX (debug generation only).")
    parser.add_argument("--excel-path", help="Deprecated alias for --source-excel.")
    parser.add_argument("--group-count", type=int)
    parser.add_argument("--weld-count", type=int, default=None, help="Deprecated expected actual weld count; never a group count.")
    parser.add_argument("--instance-seed", type=int)
    parser.add_argument("--solver-seed", type=int)
    parser.add_argument("--seed", type=int, default=None, help="Deprecated alias for --solver-seed.")
    parser.add_argument("--population-size", type=int, default=12)
    parser.add_argument("--generations", type=int, default=20)
    parser.add_argument("--max-objective-evaluations", type=int, default=0,
                        help="Maximum outer complete-system candidate observations; cache hits still count; 0 disables the cap.")
    parser.add_argument("--abma-variant", choices=[
        "legacy", "legacy_exact_fast", "exact_fast_incremental_multifidelity",
        "incremental_only", "multifidelity_only", "incremental_multifidelity",
    ], default="legacy")
    parser.add_argument("--alns-iterations", type=int, default=80)
    parser.add_argument("--vnd-iterations", type=int, default=3)
    parser.add_argument("--low-alns-iterations", type=int, default=15)
    parser.add_argument("--mid-alns-iterations", type=int, default=50)
    parser.add_argument("--high-alns-iterations", type=int, default=100)
    parser.add_argument("--low-vnd-iterations", type=int, default=0)
    parser.add_argument("--mid-vnd-iterations", type=int, default=1)
    parser.add_argument("--high-vnd-iterations", type=int, default=3)
    parser.add_argument("--low-promotion-margin", type=float, default=0.03)
    parser.add_argument("--mid-promotion-margin", type=float, default=0.03)
    parser.add_argument("--rejected-audit-interval", type=int, default=20)
    parser.add_argument("--audit-quantile", type=float, default=0.95)
    parser.add_argument("--minimum-audit-samples", type=int, default=50)
    parser.add_argument("--promotion-tolerance", type=float, default=1.0e-12)
    parser.add_argument("--near-target-margin", type=float, default=0.01)
    parser.add_argument("--small-boundary-delta", type=float, default=0.10)
    parser.add_argument("--large-boundary-delta", type=float, default=5.0)
    parser.add_argument("--high-preserved-task-ratio", type=float, default=0.80)
    parser.add_argument("--high-affected-task-ratio", type=float, default=0.40)
    parser.add_argument("--low-population-diversity", type=float, default=0.01)
    parser.add_argument("--disable-adaptive-promotion", action="store_true")
    parser.add_argument("--early-stop-patience", type=int, default=0)
    parser.add_argument("--route-time-limit-s", type=float, default=0.0)
    parser.add_argument("--critical-robot-rate", type=float, default=0.65)
    parser.add_argument("--boundary-margin", type=float, default=0.0)
    parser.add_argument("--weight-makespan", type=float, default=0.70)
    parser.add_argument("--weight-load", type=float, default=0.20)
    parser.add_argument("--weight-distance", type=float, default=0.10)
    parser.add_argument("--reference-makespan", type=float)
    parser.add_argument("--reference-load", type=float)
    parser.add_argument("--reference-distance", type=float)
    parser.add_argument("--normalization-mode", choices=[OFFICIAL_MODE, LEGACY_MODE], default=LEGACY_MODE)
    parser.add_argument("--ideal-makespan", type=float)
    parser.add_argument("--ideal-load-imbalance", type=float)
    parser.add_argument("--ideal-distance", type=float)
    parser.add_argument("--baseline-makespan", type=float)
    parser.add_argument("--baseline-load-imbalance", type=float)
    parser.add_argument("--baseline-distance", type=float)
    parser.add_argument("--scale-makespan", type=float)
    parser.add_argument("--scale-load-imbalance", type=float)
    parser.add_argument("--scale-distance", type=float)
    parser.add_argument("--weld-speed", type=float, default=0.0108)
    parser.add_argument("--travel-speed", type=float, default=0.20)
    parser.add_argument("--acceleration", type=float, default=0.50)
    parser.add_argument("--safe-z", type=float, default=0.30)
    parser.add_argument("--split-epsilon", type=float, default=1.0e-9)
    parser.add_argument("--geometry-tolerance", type=float, default=1.0e-8)
    parser.add_argument("--min-subweld-length", type=float, default=1.0e-6)
    parser.add_argument("--partition-cache-decimals", type=int, default=6)
    parser.add_argument("--disable-partition-cache", action="store_true")
    parser.add_argument("--disable-warm-start", action="store_true")
    parser.add_argument("--disable-shade-adaptation", action="store_true")
    parser.add_argument("--disable-alns-adaptation", action="store_true")
    parser.add_argument("--disable-informed-initialization", action="store_true")
    parser.add_argument("--metrics-csv")
    parser.add_argument("--result-json")
    parser.add_argument("--profile-json", help="Opt-in internal stage profiling evidence.")
    parser.add_argument("--cprofile-output", help="Opt-in deterministic cProfile binary output.")
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument(
        "--weld-z-mm", type=float, default=DEFAULT_WELD_Z_M * 1000.0
    )
    parser.add_argument(
        "--min-weld-length-mm",
        type=float,
        default=DEFAULT_MIN_WELD_LENGTH_M * 1000.0,
    )
    args = parser.parse_args()

    if args.self_check:
        _self_check()
        print("ABMA self-check passed")
        return
    if args.excel_path:
        if args.source_excel:
            parser.error("use --source-excel or deprecated --excel-path, not both")
        print("Warning: --excel-path is deprecated; use --source-excel.", file=sys.stderr)
        args.source_excel = args.excel_path
    solver_seed = resolve_solver_seed(args.solver_seed, args.seed)

    welds, input_metadata = load_solver_weld_input(
        instance_path=args.instance_path,
        source_excel=args.source_excel,
        group_count=args.group_count,
        instance_seed=args.instance_seed,
        input_units="mm",
        thickness_m=0.01,
        spacing_m=0.03,
        allow_rotate=True,
        min_weld_length_m=args.min_weld_length_mm / 1000.0,
        weld_z_m=args.weld_z_mm / 1000.0,
        expected_weld_count=args.weld_count,
    )
    print(
        f"Input instance: actual_weld_count={len(welds)}, "
        f"instance_seed={input_metadata.get('instance_seed')}, solver_seed={solver_seed}, "
        f"instance_hash={input_metadata.get('instance_hash')}"
    )
    official_values = (
        args.ideal_makespan, args.ideal_load_imbalance, args.ideal_distance,
        args.baseline_makespan, args.baseline_load_imbalance, args.baseline_distance,
        args.scale_makespan, args.scale_load_imbalance, args.scale_distance,
    )
    reference_values = (args.reference_makespan, args.reference_load, args.reference_distance)
    normalization_spec = None
    if args.normalization_mode == OFFICIAL_MODE:
        if any(value is not None for value in reference_values):
            raise ValueError("legacy references cannot be mixed with official normalization")
        if not all(value is not None for value in official_values):
            raise ValueError("official normalization requires all ideal, baseline, and scale values")
        normalization_spec = {
            "mode": OFFICIAL_MODE,
            "ideal": dict(zip(("makespan", "load_imbalance", "idle_distance"), map(float, official_values[:3]))),
            "baseline": {"algorithm": BASELINE_ALGORITHM, "metrics": dict(zip(("makespan", "load_imbalance", "idle_distance"), map(float, official_values[3:6])))},
            "scale": dict(zip(("makespan", "load_imbalance", "idle_distance"), map(float, official_values[6:9]))),
            "floors": {},
            "weights": [args.weight_makespan, args.weight_load, args.weight_distance],
        }
        validate_normalization_spec(normalization_spec)
    elif any(value is not None for value in official_values):
        raise ValueError("ideal/baseline/scale arguments require official normalization")
    config = ABMAConfig(
        seed=solver_seed,
        population_size=args.population_size,
        generations=args.generations,
        max_objective_evaluations=args.max_objective_evaluations,
        abma_variant=args.abma_variant,
        alns_iterations=args.alns_iterations,
        vnd_iterations=args.vnd_iterations,
        low_alns_iterations=args.low_alns_iterations,
        mid_alns_iterations=args.mid_alns_iterations,
        high_alns_iterations=args.high_alns_iterations,
        low_vnd_iterations=args.low_vnd_iterations,
        mid_vnd_iterations=args.mid_vnd_iterations,
        high_vnd_iterations=args.high_vnd_iterations,
        low_promotion_margin=args.low_promotion_margin,
        mid_promotion_margin=args.mid_promotion_margin,
        rejected_audit_interval=args.rejected_audit_interval,
        adaptive_promotion_enabled=not args.disable_adaptive_promotion,
        audit_quantile=args.audit_quantile,
        minimum_audit_samples=args.minimum_audit_samples,
        promotion_tolerance=args.promotion_tolerance,
        near_target_margin=args.near_target_margin,
        small_boundary_delta=args.small_boundary_delta,
        large_boundary_delta=args.large_boundary_delta,
        high_preserved_task_ratio=args.high_preserved_task_ratio,
        high_affected_task_ratio=args.high_affected_task_ratio,
        low_population_diversity=args.low_population_diversity,
        early_stop_patience=args.early_stop_patience,
        route_time_limit_s=args.route_time_limit_s,
        critical_robot_rate=args.critical_robot_rate,
        boundary_margin=args.boundary_margin,
        weight_makespan=args.weight_makespan,
        weight_load=args.weight_load,
        weight_distance=args.weight_distance,
        reference_makespan=args.reference_makespan,
        reference_load=args.reference_load,
        reference_distance=args.reference_distance,
        normalization_mode=args.normalization_mode,
        normalization_spec=normalization_spec,
        weld_speed=args.weld_speed,
        travel_speed=args.travel_speed,
        acceleration=args.acceleration,
        safe_z=args.safe_z,
        split_epsilon=args.split_epsilon,
        geometry_tolerance=args.geometry_tolerance,
        min_subweld_length=args.min_subweld_length,
        partition_cache_decimals=args.partition_cache_decimals,
        enable_partition_cache=not args.disable_partition_cache,
        enable_warm_start=not args.disable_warm_start,
        enable_shade_adaptation=not args.disable_shade_adaptation,
        enable_alns_adaptation=not args.disable_alns_adaptation,
        use_problem_informed_initialization=not args.disable_informed_initialization,
        verbose=not args.quiet,
    )
    stage_profiler = ABMAProfiler() if args.profile_json else None
    cpu_profiler = cProfile.Profile() if args.cprofile_output else None
    _ACTIVE_PROFILER = stage_profiler
    if cpu_profiler is not None:
        cpu_profiler.enable()
    result = ABMASolver(welds, config).solve()
    serialization_started = time.perf_counter() if stage_profiler is not None else 0.0
    metrics = collect_metrics(result, config, len(welds))
    metrics.update(instance_metrics(input_metadata, welds, solver_seed))
    if normalization_spec is not None:
        raw_metrics = {"makespan": result.best.makespan, "load_imbalance": result.best.load_imbalance,
                       "idle_distance": result.best.total_idle_distance}
        components = normalized_components(raw_metrics, normalization_spec)
        metrics.update({
            "normalization_mode": OFFICIAL_MODE,
            "normalization_source": "externally_fixed_unified_protocol",
            "normalized_makespan": components["makespan"],
            "normalized_load_imbalance": components["load_imbalance"],
            "normalized_idle_distance": components["idle_distance"],
            "fitness": normalized_objective(raw_metrics, normalization_spec),
            "normalization": normalization_spec,
        })
        for key in ("reference_makespan", "reference_load", "reference_distance", "ref_makespan", "ref_load", "ref_distance"):
            metrics.pop(key, None)
    else:
        metrics["normalization_mode"] = LEGACY_MODE
    robot_metrics = build_robot_metrics(
        result.best.robot_order_ids, result.best.robot_direction_flags, result.best.robots_stats
    )
    system_metrics = build_system_metrics(robot_metrics, metrics["fitness"])
    metrics.update(flatten_robot_metrics(robot_metrics))
    metrics.update({
        "makespan": system_metrics["makespan"],
        "load_imbalance": system_metrics["load_imbalance"],
        "total_weld_time": system_metrics["total_weld_time"],
        "total_travel_time": system_metrics["total_travel_time"],
        "total_idle_distance": system_metrics["total_idle_distance"],
        "sum_robot_total_time": system_metrics["sum_robot_total_time"],
        "algorithm_time": result.elapsed_time,
    })
    # Deprecated aliases retained for historical CSV readers.
    metrics["seed"] = solver_seed
    metrics["weld_count"] = len(welds)
    print(json.dumps(metrics, ensure_ascii=False, indent=2, default=str))

    if args.metrics_csv:
        save_metrics_csv(metrics, args.metrics_csv)
    if args.result_json:
        payload = build_standard_result(
            SOLVER_NAME, input_metadata.get("instance_hash", ""), solver_seed,
            result.best.x_up, result.best.x_low, robot_metrics, system_metrics,
            result.elapsed_time,
        )
        payload.update({
            "metrics": metrics,
            "config": asdict(config),
            "history": result.history,
            "checkpoint_trace": result.diagnostics.get("checkpoint_trace", []),
            "robot_order_ids": result.best.robot_order_ids,
            "robot_orders": result.best.robot_orders,
            "robot_direction_flags": result.best.robot_direction_flags,
            "assignment_stats": result.best.assignment_stats,
            "alns_stats": result.best.alns_stats,
        })
        with open(args.result_json, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, default=str)
    if stage_profiler is not None:
        stage_profiler.add_time(
            "time_result_serialization", time.perf_counter() - serialization_started
        )
    if cpu_profiler is not None:
        cpu_profiler.disable()
        os.makedirs(os.path.dirname(os.path.abspath(args.cprofile_output)), exist_ok=True)
        cpu_profiler.dump_stats(args.cprofile_output)
    if args.profile_json:
        assert stage_profiler is not None
        profile_payload = stage_profiler.payload()
        if args.cprofile_output:
            raw_stats = pstats.Stats(args.cprofile_output).stats
            functions = []
            for (filename, line, function), (primitive, calls, own, cumulative, _) in raw_stats.items():
                functions.append({
                    "function": f"{filename}:{line}({function})",
                    "primitive_calls": primitive,
                    "total_calls": calls,
                    "own_time": own,
                    "cumulative_time": cumulative,
                })
            functions.sort(key=lambda item: (-float(item["cumulative_time"]), str(item["function"])))
            profile_payload["top_functions"] = functions[:50]
        os.makedirs(os.path.dirname(os.path.abspath(args.profile_json)), exist_ok=True)
        with open(args.profile_json, "w", encoding="utf-8") as handle:
            json.dump(profile_payload, handle, ensure_ascii=False, indent=2)
    _ACTIVE_PROFILER = None


if __name__ == "__main__":
    main()
