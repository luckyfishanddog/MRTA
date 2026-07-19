"""Phase 7B DE-LKH Hybrid Solver.

This experimental solver keeps GA+ACO as the control group and implements a
separate DE outer loop plus LKH-style route local search.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import random
import sys
import time
from dataclasses import dataclass, field
from typing import Dict, List, Sequence, Tuple

from generate_welds import (
    DEFAULT_MIN_WELD_LENGTH_M,
    DEFAULT_WELD_Z_M,
    PLATFORM_W_M,
    Weld,
    instance_metrics,
    load_solver_weld_input,
    resolve_solver_seed,
)
import MRTA_GA_ACO as gaaco
from route_local_search import improve_route_lkh_style
from solver_output_metrics import (
    IncumbentTrace, build_robot_metrics, build_standard_result,
    build_system_metrics, flatten_robot_metrics,
)


TIME_MODEL = "corrected"
ASSIGNMENT_MODE = "split"
SPLIT_TIMING_MODE = "parent_aware"
DIRECTION_MODE = "bidirectional"

DE_POP_SIZE = 12
DE_MAX_GEN = 10
DE_F = 0.7
DE_CR = 0.9

MAX_LOCAL_ITERATIONS = 15
ENABLE_2OPT = True
ENABLE_RELOCATE = True
ENABLE_SWAP = False

SOLVER_NAME = "DE-LKH-Hybrid"


@dataclass
class DEIndividual:
    x_up: float
    x_low: float
    makespan: float = float("inf")
    load_imb: float = float("inf")
    total_distance: float = float("inf")
    robots_stats: List[Dict[str, float]] = field(default_factory=list)
    assignment_stats: Dict[str, object] = field(default_factory=dict)
    robot_orders: List[List[int]] = field(default_factory=list)
    fitness: float = float("inf")
    ref_makespan: float = 1.0
    ref_load: float = 1.0
    ref_distance: float = 1.0


METRIC_FIELDS = [
    "solver_name",
    "instance_path",
    "instance_seed",
    "solver_seed",
    "instance_hash",
    "target_weld_count",
    "requested_group_count",
    "placed_group_count",
    "actual_weld_count",
    "total_original_weld_length",
    "seed",
    "weld_count",
    "time_model",
    "assignment_mode",
    "split_timing_mode",
    "direction_mode",
    "route_type",
    "objective_mode",
    "weld_speed",
    "travel_speed",
    "acceleration",
    "safe_z",
    "fitness",
    "weight_makespan",
    "weight_load",
    "weight_distance",
    "ref_makespan",
    "ref_load",
    "ref_distance",
    "x_up",
    "x_low",
    "makespan",
    "load_imb",
    "total_idle_distance",
    "total_weld_time",
    "total_travel_time",
    "algo_time",
    "objective_evaluation_count",
    "max_objective_evaluations",
    "objective_budget_exhausted",
    "robot_counts",
    "original_weld_count",
    "subweld_count",
    "split_weld_count",
    "cross_region_weld_count",
    "assigned_subweld_count",
    "unassigned_subweld_count",
    "parent_id_count",
    "split_segment_count",
    "reversed_weld_count_total",
    "setup_post_saved_time_total",
    "merged_setup_post_count_total",
    "pure_weld_time_total",
    "setup_post_time_total",
    "de_pop_size",
    "de_max_gen",
    "de_F",
    "de_CR",
    "max_local_iterations",
    "enable_2opt",
    "enable_relocate",
    "enable_swap",
    "local_search_improvement_total",
    "two_opt_improvement_count_total",
    "relocate_improvement_count_total",
    "swap_improvement_count_total",
    "de_actual_gen",
    "de_early_stopped",
    "de_no_improve_generations",
    "route_eval_count_total",
    "route_cache_hit_count_total",
    "route_cache_miss_count_total",
    "route_cache_hit_rate",
    "local_search_time_total",
    "local_search_stopped_by_time_limit_count",
    "local_search_move_checks_total",
    "max_move_checks_per_operator",
    "route_time_limit_s",
    "use_route_cache",
    "early_stop_patience",
    "early_stop_min_delta",
]

COLLISION_AUDIT_METRIC_FIELDS = [
    "collision_audit_enabled",
    "collision_check_mode",
    "collision_safety_distance",
    "collision_band_width",
    "collision_dt",
    "base_makespan_before_collision",
    "collision_adjusted_makespan",
    "collision_wait_time_total",
    "upper_pair_wait_time",
    "lower_pair_wait_time",
    "upper_pair_violation_count_before",
    "lower_pair_violation_count_before",
    "upper_pair_violation_count_after",
    "lower_pair_violation_count_after",
    "upper_pair_min_separation_before",
    "lower_pair_min_separation_before",
    "upper_pair_min_separation_after",
    "lower_pair_min_separation_after",
]
for _robot_id in range(4):
    METRIC_FIELDS.extend([
        f"robot_{_robot_id}_total_weld_time", f"robot_{_robot_id}_total_travel_time",
        f"robot_{_robot_id}_total_time", f"robot_{_robot_id}_total_idle_distance",
        f"robot_{_robot_id}_task_count",
    ])
METRIC_FIELDS.extend(["sum_robot_total_time", "stop_reason"])

METRIC_FIELDS.extend(COLLISION_AUDIT_METRIC_FIELDS)


def configure_gaaco_modes(
    time_model: str,
    assignment_mode: str,
    split_timing_mode: str,
    direction_mode: str,
) -> None:
    global TIME_MODEL, ASSIGNMENT_MODE, SPLIT_TIMING_MODE, DIRECTION_MODE
    TIME_MODEL = time_model
    ASSIGNMENT_MODE = assignment_mode
    SPLIT_TIMING_MODE = split_timing_mode
    DIRECTION_MODE = direction_mode
    gaaco.TIME_MODEL = time_model
    gaaco.ASSIGNMENT_MODE = assignment_mode
    gaaco.SPLIT_TIMING_MODE = split_timing_mode
    gaaco.DIRECTION_MODE = direction_mode


def _collision_metrics_disabled(args=None):
    return {
        "collision_audit_enabled": False,
        "collision_check_mode": "",
        "collision_safety_distance": getattr(args, "collision_safety_distance", ""),
        "collision_band_width": getattr(args, "collision_band_width", ""),
        "collision_dt": getattr(args, "collision_dt", ""),
        "base_makespan_before_collision": "",
        "collision_adjusted_makespan": "",
        "collision_wait_time_total": "",
        "upper_pair_wait_time": "",
        "lower_pair_wait_time": "",
        "upper_pair_violation_count_before": "",
        "lower_pair_violation_count_before": "",
        "upper_pair_violation_count_after": "",
        "lower_pair_violation_count_after": "",
        "upper_pair_min_separation_before": "",
        "lower_pair_min_separation_before": "",
        "upper_pair_min_separation_after": "",
        "lower_pair_min_separation_after": "",
    }


def _collision_metrics_from_audit(audit_stats):
    return {
        "collision_audit_enabled": True,
        "collision_check_mode": audit_stats.get("collision_check_mode", ""),
        "collision_safety_distance": audit_stats.get("safety_distance", ""),
        "collision_band_width": audit_stats.get("band_width", ""),
        "collision_dt": audit_stats.get("dt", ""),
        "base_makespan_before_collision": audit_stats.get("base_makespan", ""),
        "collision_adjusted_makespan": audit_stats.get("collision_adjusted_makespan", ""),
        "collision_wait_time_total": audit_stats.get("collision_wait_time_total", ""),
        "upper_pair_wait_time": audit_stats.get("upper_pair_wait_time", ""),
        "lower_pair_wait_time": audit_stats.get("lower_pair_wait_time", ""),
        "upper_pair_violation_count_before": audit_stats.get("upper_pair_violation_count_before", ""),
        "lower_pair_violation_count_before": audit_stats.get("lower_pair_violation_count_before", ""),
        "upper_pair_violation_count_after": audit_stats.get("upper_pair_violation_count_after", ""),
        "lower_pair_violation_count_after": audit_stats.get("lower_pair_violation_count_after", ""),
        "upper_pair_min_separation_before": audit_stats.get("upper_pair_min_separation_before", ""),
        "lower_pair_min_separation_before": audit_stats.get("lower_pair_min_separation_before", ""),
        "upper_pair_min_separation_after": audit_stats.get("upper_pair_min_separation_after", ""),
        "lower_pair_min_separation_after": audit_stats.get("lower_pair_min_separation_after", ""),
    }


def run_collision_audit_if_enabled(args, robots, robot_orders, x_up, x_low):
    if not getattr(args, "enable_collision_audit", False):
        return None, _collision_metrics_disabled(args)
    import collision_aware_schedule as cas

    directed_sequences = cas.make_directed_sequences_from_robot_orders(
        robots,
        robot_orders,
        split_timing_mode=args.split_timing_mode,
    )
    audit_stats = cas.audit_boundary_collisions_for_four_robots(
        directed_sequences,
        x_up=x_up,
        x_low=x_low,
        safety_distance=args.collision_safety_distance,
        band_width=args.collision_band_width,
        dt=args.collision_dt,
    )
    return audit_stats, _collision_metrics_from_audit(audit_stats)


def print_collision_audit_summary(audit_stats):
    if not audit_stats:
        return
    print("Collision audit summary (post-processing only):")
    print(f"  base_makespan_before_collision: {audit_stats['base_makespan']:.6f}")
    print(f"  collision_adjusted_makespan   : {audit_stats['collision_adjusted_makespan']:.6f}")
    print(f"  collision_wait_time_total     : {audit_stats['collision_wait_time_total']:.6f}")
    print(f"  upper violations before/after : {audit_stats['upper_pair_violation_count_before']} / {audit_stats['upper_pair_violation_count_after']}")
    print(f"  lower violations before/after : {audit_stats['lower_pair_violation_count_before']} / {audit_stats['lower_pair_violation_count_after']}")


def _empty_robot_stats() -> Dict[str, float]:
    return {
        "total_time": 0.0,
        "total_weld_time": 0.0,
        "total_travel_time": 0.0,
        "total_idle_distance": 0.0,
        "setup_post_saved_time": 0.0,
        "merged_setup_post_count": 0,
        "pure_weld_time": 0.0,
        "setup_post_time": 0.0,
        "reversed_weld_count": 0,
        "local_search_iterations": 0,
        "two_opt_improvement_count": 0,
        "relocate_improvement_count": 0,
        "swap_improvement_count": 0,
        "local_search_initial_cost": 0.0,
        "local_search_final_cost": 0.0,
        "local_search_improvement": 0.0,
        "route_eval_count": 0,
        "route_cache_hit_count": 0,
        "route_cache_miss_count": 0,
        "local_search_time": 0.0,
        "local_search_stopped_by_time_limit": False,
        "local_search_move_checks_total": 0,
    }


def evaluate_partition_de_lkh(
    welds: Sequence[Weld],
    x_up: float,
    x_low: float,
    max_local_iterations: int = MAX_LOCAL_ITERATIONS,
    enable_2opt: bool = ENABLE_2OPT,
    enable_relocate: bool = ENABLE_RELOCATE,
    enable_swap: bool = ENABLE_SWAP,
    split_timing_mode: str = SPLIT_TIMING_MODE,
    direction_mode: str = DIRECTION_MODE,
    use_route_cache: bool = True,
    max_move_checks_per_operator: int | None = 200,
    route_time_limit_s: float = 0.0,
) -> Tuple[
    float,
    float,
    float,
    List[Dict[str, float]],
    Dict[str, object],
    List[List[int]],
]:
    robots, assignment_stats = gaaco.get_assignment_for_partition(
        list(welds), x_up, x_low, assignment_mode="split"
    )

    if not gaaco.assignment_is_feasible(assignment_stats):
        return (
            float("inf"),
            float("inf"),
            float("inf"),
            [_empty_robot_stats() for _ in range(4)],
            assignment_stats,
            [[] for _ in range(4)],
        )

    robots_stats: List[Dict[str, float]] = []
    robot_orders: List[List[int]] = []
    for robot_welds in robots:
        if not robot_welds:
            robots_stats.append(_empty_robot_stats())
            robot_orders.append([])
            continue
        order, stats = improve_route_lkh_style(
            robot_welds,
            max_local_iterations=max_local_iterations,
            enable_2opt=enable_2opt,
            enable_relocate=enable_relocate,
            enable_swap=enable_swap,
            split_timing_mode=split_timing_mode,
            direction_mode=direction_mode,
            use_cache=use_route_cache,
            max_move_checks_per_operator=max_move_checks_per_operator,
            time_limit_s=route_time_limit_s if route_time_limit_s > 0 else None,
        )
        robots_stats.append(stats)
        robot_orders.append(order)

    total_times = [float(stats.get("total_time", 0.0)) for stats in robots_stats]
    makespan = max(total_times) if total_times else 0.0
    load_imb = (max(total_times) - min(total_times)) if total_times else 0.0
    total_distance = sum(float(stats.get("total_idle_distance", 0.0)) for stats in robots_stats)
    return makespan, load_imb, total_distance, robots_stats, assignment_stats, robot_orders


def repair_bounds(x_up: float, x_low: float, boundary_margin: float = 0.0) -> Tuple[float, float]:
    margin = max(0.0, min(float(boundary_margin), PLATFORM_W_M / 2.0))
    lo = margin
    hi = PLATFORM_W_M - margin
    if lo > hi:
        lo, hi = 0.0, PLATFORM_W_M
    return min(max(x_up, lo), hi), min(max(x_low, lo), hi)


def random_individual(rng: random.Random, boundary_margin: float = 0.0) -> DEIndividual:
    lo = max(0.0, float(boundary_margin))
    hi = min(PLATFORM_W_M, PLATFORM_W_M - float(boundary_margin))
    if lo > hi:
        lo, hi = 0.0, PLATFORM_W_M
    return DEIndividual(rng.uniform(lo, hi), rng.uniform(lo, hi))


def _evaluate_individual(
    individual: DEIndividual,
    welds: Sequence[Weld],
    max_local_iterations: int,
    enable_2opt: bool,
    enable_relocate: bool,
    enable_swap: bool,
    split_timing_mode: str,
    direction_mode: str,
    use_route_cache: bool,
    max_move_checks_per_operator: int | None,
    route_time_limit_s: float,
    objective_refs: Tuple[float, float, float],
) -> DEIndividual:
    (
        individual.makespan,
        individual.load_imb,
        individual.total_distance,
        individual.robots_stats,
        individual.assignment_stats,
        individual.robot_orders,
    ) = evaluate_partition_de_lkh(
        welds,
        individual.x_up,
        individual.x_low,
        max_local_iterations=max_local_iterations,
        enable_2opt=enable_2opt,
        enable_relocate=enable_relocate,
        enable_swap=enable_swap,
        split_timing_mode=split_timing_mode,
        direction_mode=direction_mode,
        use_route_cache=use_route_cache,
        max_move_checks_per_operator=max_move_checks_per_operator,
        route_time_limit_s=route_time_limit_s,
    )
    individual.ref_makespan, individual.ref_load, individual.ref_distance = objective_refs
    individual.fitness = gaaco.weighted_objective(
        individual.makespan,
        individual.load_imb,
        individual.total_distance,
        *objective_refs,
    )
    return individual


def mutate_rand_1(
    population: Sequence[DEIndividual],
    idx: int,
    rng: random.Random,
    F: float,
) -> Tuple[float, float]:
    candidates = [i for i in range(len(population)) if i != idx]
    if len(candidates) < 3:
        raise ValueError("DE/rand/1/bin requires pop_size >= 4")
    a_idx, b_idx, c_idx = rng.sample(candidates, 3)
    a = population[a_idx]
    b = population[b_idx]
    c = population[c_idx]
    return a.x_up + F * (b.x_up - c.x_up), a.x_low + F * (b.x_low - c.x_low)


def binomial_crossover(
    target: DEIndividual,
    mutant: Tuple[float, float],
    rng: random.Random,
    CR: float,
) -> Tuple[float, float]:
    target_values = (target.x_up, target.x_low)
    forced_dim = rng.randrange(2)
    trial = []
    for dim in range(2):
        if dim == forced_dim or rng.random() < CR:
            trial.append(mutant[dim])
        else:
            trial.append(target_values[dim])
    return trial[0], trial[1]


def run_de_lkh(welds: Sequence[Weld], args) -> DEIndividual:
    if args.de_pop_size < 4:
        raise ValueError("--de-pop-size must be at least 4 for DE/rand/1/bin")
    if args.max_objective_evaluations and args.max_objective_evaluations < args.de_pop_size:
        raise ValueError("--max-objective-evaluations must be 0 or at least --de-pop-size")

    objective_refs = gaaco.resolve_objective_references(
        list(welds),
        getattr(args, "ref_makespan", None),
        getattr(args, "ref_load", None),
        getattr(args, "ref_distance", None),
    )
    rng = random.Random(args.seed)
    population = [
        random_individual(rng, boundary_margin=args.boundary_margin)
        for _ in range(args.de_pop_size)
    ]
    objective_evaluation_count = 0
    trace = IncumbentTrace()
    trace_started = time.perf_counter()

    def evaluate(individual):
        nonlocal objective_evaluation_count
        result = _evaluate_individual(
            individual,
            welds,
            args.max_local_iterations,
            args.enable_2opt,
            args.enable_relocate,
            args.enable_swap,
            args.split_timing_mode,
            args.direction_mode,
            args.use_route_cache,
            args.max_move_checks_per_operator,
            args.route_time_limit_s,
            objective_refs,
        )
        objective_evaluation_count += 1
        trace.observe(objective_evaluation_count, result.fitness, result.makespan,
                      result.load_imb, result.total_distance,
                      time.perf_counter() - trace_started)
        return result

    population = [evaluate(individual) for individual in population]

    best = min(population, key=lambda individual: individual.fitness)
    best_so_far = best.fitness
    no_improve_generations = 0
    actual_gen = 0
    early_stopped = False
    budget_exhausted = False

    for gen in range(args.de_max_gen):
        next_population: List[DEIndividual] = []
        for idx, target in enumerate(population):
            if args.max_objective_evaluations and objective_evaluation_count >= args.max_objective_evaluations:
                budget_exhausted = True
                next_population.extend(population[idx:])
                break
            mutant = mutate_rand_1(population, idx, rng, args.de_F)
            trial_x_up, trial_x_low = binomial_crossover(target, mutant, rng, args.de_CR)
            trial_x_up, trial_x_low = repair_bounds(
                trial_x_up, trial_x_low, boundary_margin=args.boundary_margin
            )
            trial = evaluate(
                DEIndividual(trial_x_up, trial_x_low),
            )
            next_population.append(trial if trial.fitness <= target.fitness else target)
        population = next_population
        best = min(population, key=lambda individual: individual.fitness)
        actual_gen = gen + 1
        if best_so_far - best.fitness > args.early_stop_min_delta:
            best_so_far = best.fitness
            no_improve_generations = 0
        else:
            no_improve_generations += 1
        print(
            f"DE generation {gen + 1}/{args.de_max_gen}: "
            f"best_fitness={best.fitness:.6f}, makespan={best.makespan:.2f}, "
            f"x_up={best.x_up:.3f}, x_low={best.x_low:.3f}"
        )
        if args.early_stop_patience > 0 and no_improve_generations >= args.early_stop_patience:
            early_stopped = True
            print(
                f"DE early stopped at generation {actual_gen}: "
                f"no_improve_generations={no_improve_generations}"
            )
            break
        if budget_exhausted:
            break

    best = min(population, key=lambda individual: individual.fitness)
    best.de_actual_gen = actual_gen
    best.de_early_stopped = early_stopped
    best.de_no_improve_generations = no_improve_generations
    budget_exhausted = budget_exhausted or bool(
        args.max_objective_evaluations
        and objective_evaluation_count >= args.max_objective_evaluations
    )
    best.objective_evaluation_count = objective_evaluation_count
    best.max_objective_evaluations = args.max_objective_evaluations
    best.objective_budget_exhausted = budget_exhausted
    best.checkpoint_trace = trace.records
    best.stop_reason = ("objective_budget" if budget_exhausted else
                        "early_stopping" if early_stopped else "generation_limit")
    return best


def collect_de_lkh_metrics(
    best: DEIndividual,
    algo_time: float,
    seed: int,
    weld_count: int,
    args,
) -> Dict[str, object]:
    assignment_stats = best.assignment_stats or {}
    time_params = gaaco.get_active_time_params()
    robot_counts = [
        int(assignment_stats.get(f"robot_{idx}_task_count", 0))
        for idx in range(4)
    ]
    total_weld_time = sum(float(stats.get("total_weld_time", 0.0)) for stats in best.robots_stats)
    total_travel_time = sum(float(stats.get("total_travel_time", 0.0)) for stats in best.robots_stats)
    reversed_weld_count_total = sum(int(stats.get("reversed_weld_count", 0)) for stats in best.robots_stats)
    setup_post_saved_time_total = sum(
        float(stats.get("setup_post_saved_time", 0.0)) for stats in best.robots_stats
    )
    merged_setup_post_count_total = sum(
        int(stats.get("merged_setup_post_count", 0)) for stats in best.robots_stats
    )
    pure_weld_time_total = sum(float(stats.get("pure_weld_time", 0.0)) for stats in best.robots_stats)
    setup_post_time_total = sum(float(stats.get("setup_post_time", 0.0)) for stats in best.robots_stats)
    local_search_improvement_total = sum(
        float(stats.get("local_search_improvement", 0.0)) for stats in best.robots_stats
    )
    two_opt_improvement_count_total = sum(
        int(stats.get("two_opt_improvement_count", 0)) for stats in best.robots_stats
    )
    relocate_improvement_count_total = sum(
        int(stats.get("relocate_improvement_count", 0)) for stats in best.robots_stats
    )
    swap_improvement_count_total = sum(
        int(stats.get("swap_improvement_count", 0)) for stats in best.robots_stats
    )
    route_eval_count_total = sum(int(stats.get("route_eval_count", 0)) for stats in best.robots_stats)
    route_cache_hit_count_total = sum(int(stats.get("route_cache_hit_count", 0)) for stats in best.robots_stats)
    route_cache_miss_count_total = sum(int(stats.get("route_cache_miss_count", 0)) for stats in best.robots_stats)
    local_search_time_total = sum(float(stats.get("local_search_time", 0.0)) for stats in best.robots_stats)
    local_search_stopped_by_time_limit_count = sum(
        1 for stats in best.robots_stats if stats.get("local_search_stopped_by_time_limit")
    )
    local_search_move_checks_total = sum(
        int(stats.get("local_search_move_checks_total", 0)) for stats in best.robots_stats
    )
    route_cache_hit_rate = (
        route_cache_hit_count_total / route_eval_count_total
        if route_eval_count_total > 0 else 0.0
    )

    metrics = {
        "solver_name": SOLVER_NAME,
        "seed": seed,
        "weld_count": weld_count,
        "time_model": TIME_MODEL,
        "assignment_mode": ASSIGNMENT_MODE,
        "split_timing_mode": args.split_timing_mode,
        "direction_mode": args.direction_mode,
        "route_type": "open",
        "objective_mode": "equation_19_fixed_references",
        "weld_speed": time_params["weld_speed"],
        "travel_speed": time_params["travel_speed"],
        "acceleration": time_params["acc"],
        "safe_z": time_params["safe_z"],
        "fitness": best.fitness,
        "weight_makespan": gaaco.WEIGHT_MAKESPAN,
        "weight_load": gaaco.WEIGHT_LOAD,
        "weight_distance": gaaco.WEIGHT_DISTANCE,
        "ref_makespan": best.ref_makespan,
        "ref_load": best.ref_load,
        "ref_distance": best.ref_distance,
        "x_up": best.x_up,
        "x_low": best.x_low,
        "makespan": best.makespan,
        "load_imb": best.load_imb,
        "total_idle_distance": best.total_distance,
        "total_weld_time": total_weld_time,
        "total_travel_time": total_travel_time,
        "algo_time": algo_time,
        "objective_evaluation_count": getattr(best, "objective_evaluation_count", 0),
        "max_objective_evaluations": getattr(best, "max_objective_evaluations", 0),
        "objective_budget_exhausted": getattr(best, "objective_budget_exhausted", False),
        "robot_counts": str(robot_counts),
        "original_weld_count": assignment_stats.get("original_weld_count", weld_count),
        "subweld_count": assignment_stats.get("subweld_count", sum(robot_counts)),
        "split_weld_count": assignment_stats.get("split_weld_count", 0),
        "cross_region_weld_count": assignment_stats.get("cross_region_weld_count", 0),
        "assigned_subweld_count": assignment_stats.get("assigned_subweld_count", sum(robot_counts)),
        "unassigned_subweld_count": assignment_stats.get("unassigned_subweld_count", 0),
        "parent_id_count": assignment_stats.get("parent_id_count", weld_count),
        "split_segment_count": assignment_stats.get("split_segment_count", 0),
        "reversed_weld_count_total": reversed_weld_count_total,
        "setup_post_saved_time_total": setup_post_saved_time_total,
        "merged_setup_post_count_total": merged_setup_post_count_total,
        "pure_weld_time_total": pure_weld_time_total,
        "setup_post_time_total": setup_post_time_total,
        "de_pop_size": args.de_pop_size,
        "de_max_gen": args.de_max_gen,
        "de_F": args.de_F,
        "de_CR": args.de_CR,
        "max_local_iterations": args.max_local_iterations,
        "enable_2opt": args.enable_2opt,
        "enable_relocate": args.enable_relocate,
        "enable_swap": args.enable_swap,
        "local_search_improvement_total": local_search_improvement_total,
        "two_opt_improvement_count_total": two_opt_improvement_count_total,
        "relocate_improvement_count_total": relocate_improvement_count_total,
        "swap_improvement_count_total": swap_improvement_count_total,
        "de_actual_gen": getattr(best, "de_actual_gen", args.de_max_gen),
        "de_early_stopped": getattr(best, "de_early_stopped", False),
        "de_no_improve_generations": getattr(best, "de_no_improve_generations", 0),
        "route_eval_count_total": route_eval_count_total,
        "route_cache_hit_count_total": route_cache_hit_count_total,
        "route_cache_miss_count_total": route_cache_miss_count_total,
        "route_cache_hit_rate": route_cache_hit_rate,
        "local_search_time_total": local_search_time_total,
        "local_search_stopped_by_time_limit_count": local_search_stopped_by_time_limit_count,
        "local_search_move_checks_total": local_search_move_checks_total,
        "max_move_checks_per_operator": args.max_move_checks_per_operator,
        "route_time_limit_s": args.route_time_limit_s,
        "use_route_cache": args.use_route_cache,
        "early_stop_patience": args.early_stop_patience,
        "early_stop_min_delta": args.early_stop_min_delta,
    }
    metrics.update(_collision_metrics_disabled(args))
    return metrics


def save_metrics_csv(metrics: Dict[str, object], csv_path: str) -> bool:
    os.makedirs(os.path.dirname(os.path.abspath(csv_path)), exist_ok=True)
    file_exists = os.path.exists(csv_path)
    if file_exists:
        with open(csv_path, mode="r", newline="", encoding="utf-8-sig") as f:
            reader = csv.reader(f)
            existing_header = next(reader, None)
        if existing_header != METRIC_FIELDS:
            print(f"Warning: CSV header mismatch for {csv_path}; skip writing to avoid pollution.")
            return False

    with open(csv_path, mode="a", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=METRIC_FIELDS)
        if not file_exists:
            writer.writeheader()
        writer.writerow({field: metrics.get(field, "") for field in METRIC_FIELDS})
    return True


def print_summary(best: DEIndividual, metrics: Dict[str, object]) -> None:
    print("\n" + "=" * 80)
    print("DE-LKH Hybrid Solver result")
    print("=" * 80)
    print(f"x_up={best.x_up:.6f} m, x_low={best.x_low:.6f} m")
    print(f"fitness={best.fitness:.6f} (equation 19)")
    print(f"makespan={best.makespan:.2f} s")
    print(f"load_imb={best.load_imb:.2f} s")
    print(f"total_travel_time={metrics['total_travel_time']:.2f} s")
    print(f"total_idle_distance={best.total_distance:.2f} m")
    print(f"robot_counts={metrics['robot_counts']}")
    print(f"subweld_count={metrics['subweld_count']}")
    print(f"split_weld_count={metrics['split_weld_count']}")
    print(f"reversed_weld_count_total={metrics['reversed_weld_count_total']}")
    print(f"algo_time={metrics['algo_time']:.2f} s")
    print(f"de_actual_gen={metrics['de_actual_gen']}")
    print(f"de_early_stopped={metrics['de_early_stopped']}")
    print(f"route_eval_count_total={metrics['route_eval_count_total']}")
    print(f"route_cache_hit_rate={metrics['route_cache_hit_rate']:.4f}")
    print("=" * 80)


def _build_self_check_args():
    return argparse.Namespace(
        seed=42,
        de_pop_size=4,
        de_max_gen=1,
        de_F=0.7,
        de_CR=0.9,
        max_local_iterations=1,
        max_move_checks_per_operator=10,
        route_time_limit_s=0.0,
        use_route_cache=True,
        early_stop_patience=2,
        early_stop_min_delta=1e-6,
        split_timing_mode=SPLIT_TIMING_MODE,
        direction_mode=DIRECTION_MODE,
        enable_2opt=True,
        enable_relocate=True,
        enable_swap=False,
        boundary_margin=0.0,
        max_objective_evaluations=0,
    )


def _self_check_de_lkh_smoke() -> None:
    configure_gaaco_modes(TIME_MODEL, ASSIGNMENT_MODE, SPLIT_TIMING_MODE, DIRECTION_MODE)
    z = 0.1
    welds = [
        Weld("a", 1.0, 9.0, z, 2.0, 9.0, z),
        Weld("b", 4.0, 12.0, z, 5.0, 12.0, z),
        Weld("c", 7.0, 4.0, z, 8.0, 4.0, z),
        Weld("d", 12.0, 6.0, z, 13.0, 6.0, z),
        Weld("e", 16.0, 10.0, z, 17.0, 10.0, z),
    ]
    best = run_de_lkh(welds, _build_self_check_args())
    assert best.makespan < float("inf")
    assert getattr(best, "de_actual_gen", 0) <= _build_self_check_args().de_max_gen
    assert int(best.assignment_stats.get("unassigned_subweld_count", 0)) == 0
    metrics = collect_de_lkh_metrics(best, 0.0, 42, len(welds), _build_self_check_args())
    assert "route_eval_count_total" in metrics


def _self_check_collision_audit_integration_de_lkh() -> None:
    args = _build_self_check_args()
    args.enable_collision_audit = True
    args.collision_safety_distance = 0.5
    args.collision_band_width = 0.5
    args.collision_dt = 0.5
    configure_gaaco_modes(TIME_MODEL, ASSIGNMENT_MODE, SPLIT_TIMING_MODE, DIRECTION_MODE)
    z = 0.1
    welds = [
        Weld("de_r0", 9.8, 7.0, z, 9.9, 7.0, z),
        Weld("de_r1", 10.1, 7.0, z, 10.2, 7.0, z),
        Weld("de_r2", 1.0, 2.0, z, 1.2, 2.0, z),
        Weld("de_r3", 14.0, 2.0, z, 14.2, 2.0, z),
    ]
    best = run_de_lkh(welds, args)
    robots, _ = gaaco.get_assignment_for_partition(list(welds), best.x_up, best.x_low, assignment_mode="split")
    audit_stats, metrics = run_collision_audit_if_enabled(args, robots, best.robot_orders, best.x_up, best.x_low)
    assert audit_stats is not None
    assert "collision_adjusted_makespan" in metrics


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Phase 7B DE-LKH Hybrid Solver.")
    parser.add_argument("--instance-path", help="Frozen instance XLSX (formal mode).")
    parser.add_argument("--source-excel", help="Raw assembly XLSX (debug generation only).")
    parser.add_argument("--excel-path", default=None, help="Deprecated alias for --source-excel.")
    parser.add_argument("--group-count", type=int, default=None)
    parser.add_argument("--weld-count", type=int, default=None, help="Deprecated expected actual weld count; never a group count.")
    parser.add_argument("--instance-seed", type=int, default=None)
    parser.add_argument("--solver-seed", type=int, default=None)
    parser.add_argument("--seed", type=int, default=None, help="Deprecated alias for --solver-seed.")
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--de-pop-size", type=int, default=DE_POP_SIZE)
    parser.add_argument("--de-max-gen", type=int, default=DE_MAX_GEN)
    parser.add_argument("--max-objective-evaluations", type=int, default=0,
                        help="Maximum complete system candidate evaluations; 0 disables the cap.")
    parser.add_argument("--de-F", type=float, default=DE_F)
    parser.add_argument("--de-CR", type=float, default=DE_CR)
    parser.add_argument("--max-local-iterations", type=int, default=MAX_LOCAL_ITERATIONS)
    parser.add_argument("--max-move-checks-per-operator", type=int, default=200)
    parser.add_argument("--route-time-limit-s", type=float, default=0.0)
    parser.add_argument("--use-route-cache", dest="use_route_cache", action="store_true", default=True)
    parser.add_argument("--no-route-cache", dest="use_route_cache", action="store_false")
    parser.add_argument("--early-stop-patience", type=int, default=5)
    parser.add_argument("--early-stop-min-delta", type=float, default=1e-6)
    parser.add_argument("--direction-mode", choices=["fixed", "bidirectional"], default="bidirectional")
    parser.add_argument("--split-timing-mode", choices=["conservative", "parent_aware"], default="parent_aware")
    parser.add_argument("--ref-makespan", type=float, default=None,
                        help="式(19)固定 Cref；三个参考值必须同时提供，否则按实例确定性生成")
    parser.add_argument("--ref-load", type=float, default=None, help="式(19)固定 Bref")
    parser.add_argument("--ref-distance", type=float, default=None, help="式(19)固定 Dref")
    parser.add_argument("--weight-makespan", type=float, default=gaaco.WEIGHT_MAKESPAN)
    parser.add_argument("--normalization-mode", choices=[gaaco.OFFICIAL_MODE, gaaco.LEGACY_MODE], default=gaaco.LEGACY_MODE)
    parser.add_argument("--ideal-makespan", type=float)
    parser.add_argument("--ideal-load-imbalance", type=float)
    parser.add_argument("--ideal-distance", type=float)
    parser.add_argument("--baseline-makespan", type=float)
    parser.add_argument("--baseline-load-imbalance", type=float)
    parser.add_argument("--baseline-distance", type=float)
    parser.add_argument("--scale-makespan", type=float)
    parser.add_argument("--scale-load-imbalance", type=float)
    parser.add_argument("--scale-distance", type=float)
    parser.add_argument("--weight-load", type=float, default=gaaco.WEIGHT_LOAD)
    parser.add_argument("--weight-distance", type=float, default=gaaco.WEIGHT_DISTANCE)
    parser.add_argument("--weld-speed", type=float, default=gaaco.CORRECTED_WELD_SPEED)
    parser.add_argument("--travel-speed", type=float, default=gaaco.CORRECTED_TRAVEL_SPEED)
    parser.add_argument("--acceleration", type=float, default=gaaco.CORRECTED_ACC)
    parser.add_argument("--safe-z", type=float, default=gaaco.CORRECTED_SAFE_Z)
    parser.add_argument("--disable-2opt", action="store_true")
    parser.add_argument("--disable-relocate", action="store_true")
    parser.add_argument("--enable-swap", action="store_true")
    parser.add_argument("--boundary-margin", type=float, default=0.0)
    parser.add_argument("--metrics-csv", default=None)
    parser.add_argument("--result-json", default=None)
    parser.add_argument("--output-image", default=None)
    parser.add_argument("--weld-z-mm", type=float, default=DEFAULT_WELD_Z_M * 1000.0)
    parser.add_argument("--min-weld-length-mm", type=float, default=DEFAULT_MIN_WELD_LENGTH_M * 1000.0)
    parser.add_argument("--enable-collision-audit", action="store_true")
    parser.add_argument("--collision-safety-distance", type=float, default=0.5)
    parser.add_argument("--collision-band-width", type=float, default=None)
    parser.add_argument("--collision-dt", type=float, default=1.0)
    args = parser.parse_args()

    if args.self_check:
        _self_check_de_lkh_smoke()
        _self_check_collision_audit_integration_de_lkh()
        print("DE-LKH self-checks passed")
        return

    if args.excel_path:
        if args.source_excel:
            parser.error("use --source-excel or deprecated --excel-path, not both")
        print("Warning: --excel-path is deprecated; use --source-excel.", file=sys.stderr)
        args.source_excel = args.excel_path
    solver_seed = resolve_solver_seed(args.solver_seed, args.seed)
    args.seed = solver_seed

    args.enable_2opt = not args.disable_2opt
    args.enable_relocate = not args.disable_relocate
    gaaco.WEIGHT_MAKESPAN = args.weight_makespan
    gaaco.WEIGHT_LOAD = args.weight_load
    gaaco.WEIGHT_DISTANCE = args.weight_distance
    gaaco.OBJECTIVE_NORMALIZATION_MODE = args.normalization_mode
    gaaco.OBJECTIVE_NORMALIZATION_SPEC = gaaco.build_external_normalization_spec(args)
    gaaco.CORRECTED_WELD_SPEED = args.weld_speed
    gaaco.CORRECTED_TRAVEL_SPEED = args.travel_speed
    gaaco.CORRECTED_ACC = args.acceleration
    gaaco.CORRECTED_SAFE_Z = args.safe_z
    configure_gaaco_modes(TIME_MODEL, ASSIGNMENT_MODE, args.split_timing_mode, args.direction_mode)
    random.seed(solver_seed)

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
    print(
        f"DE params: pop_size={args.de_pop_size}, max_gen={args.de_max_gen}, "
        f"F={args.de_F}, CR={args.de_CR}"
    )
    print(
        f"Local search: max_iter={args.max_local_iterations}, "
        f"2opt={args.enable_2opt}, relocate={args.enable_relocate}, swap={args.enable_swap}, "
        f"cache={args.use_route_cache}, max_checks={args.max_move_checks_per_operator}"
    )

    start_time = time.time()
    best = run_de_lkh(welds, args)
    algo_time = time.time() - start_time

    metrics = collect_de_lkh_metrics(best, algo_time, solver_seed, len(welds), args)
    metrics.update(instance_metrics(input_metadata, welds, solver_seed))
    if args.normalization_mode == gaaco.OFFICIAL_MODE:
        raw_metrics = {"makespan": best.makespan, "load_imbalance": best.load_imb,
                       "idle_distance": best.total_distance}
        components = gaaco.normalized_components(raw_metrics, gaaco.OBJECTIVE_NORMALIZATION_SPEC)
        metrics.update({
            "normalization_mode": gaaco.OFFICIAL_MODE,
            "normalization_source": "externally_fixed_unified_protocol",
            "normalized_makespan": components["makespan"],
            "normalized_load_imbalance": components["load_imbalance"],
            "normalized_idle_distance": components["idle_distance"],
            "fitness": gaaco.normalized_objective(raw_metrics, gaaco.OBJECTIVE_NORMALIZATION_SPEC),
            "normalization": gaaco.OBJECTIVE_NORMALIZATION_SPEC,
        })
    else:
        metrics["normalization_mode"] = gaaco.LEGACY_MODE
    robots, _ = gaaco.get_assignment_for_partition(
        list(welds), best.x_up, best.x_low, assignment_mode="split"
    )
    robot_order_ids = [
        [str(robots[index][task].id) for task in best.robot_orders[index]]
        for index in range(4)
    ]
    robot_direction_flags = [
        [bool(flag) for flag in stats.get("direction_flags", [])]
        for stats in best.robots_stats
    ]
    robot_metrics = build_robot_metrics(robot_order_ids, robot_direction_flags, best.robots_stats)
    system_metrics = build_system_metrics(robot_metrics, metrics["fitness"])
    metrics.update(flatten_robot_metrics(robot_metrics))
    metrics.update({
        "makespan": system_metrics["makespan"],
        "load_imb": system_metrics["load_imbalance"],
        "load_imbalance": system_metrics["load_imbalance"],
        "total_weld_time": system_metrics["total_weld_time"],
        "total_travel_time": system_metrics["total_travel_time"],
        "total_idle_distance": system_metrics["total_idle_distance"],
        "sum_robot_total_time": system_metrics["sum_robot_total_time"],
        "algorithm_time": algo_time,
        "stop_reason": getattr(best, "stop_reason", "unknown"),
    })
    if args.enable_collision_audit:
        robots, _ = gaaco.get_assignment_for_partition(
            list(welds),
            best.x_up,
            best.x_low,
            assignment_mode="split",
        )
        audit_stats, collision_metrics = run_collision_audit_if_enabled(
            args,
            robots,
            best.robot_orders,
            best.x_up,
            best.x_low,
        )
        metrics.update(collision_metrics)
        print_collision_audit_summary(audit_stats)
    print_summary(best, metrics)

    if args.metrics_csv:
        if save_metrics_csv(metrics, args.metrics_csv):
            print(f"DE-LKH metrics appended to: {args.metrics_csv}")

    if args.result_json:
        payload = build_standard_result(
            SOLVER_NAME, input_metadata.get("instance_hash", ""), solver_seed,
            best.x_up, best.x_low, robot_metrics, system_metrics, algo_time,
        )
        payload.update({
            "metrics": metrics,
            "robot_orders": best.robot_orders,
            "robot_order_ids": robot_order_ids,
            "robot_direction_flags": robot_direction_flags,
            "assignment_stats": best.assignment_stats,
            "checkpoint_trace": getattr(best, "checkpoint_trace", []),
        })
        result_path = os.path.abspath(args.result_json)
        os.makedirs(os.path.dirname(result_path), exist_ok=True)
        with open(result_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, default=str)

    if args.output_image:
        print("DE-LKH visualization is not implemented in Phase 7B.")


if __name__ == "__main__":
    main()
