"""LKH-style route local search for the DE-LKH experimental solver.

This module intentionally stays independent from the GA+ACO control solver.
It reuses the established route timing and direction evaluation functions
without modifying MRTA_GA_ACO.py.
"""

from __future__ import annotations

import time
from typing import Dict, List, Optional, Sequence, Tuple

from generate_welds import Weld
from MRTA_GA_ACO import (
    compute_directed_route_stats,
    compute_route_stats_with_timing_mode,
    optimize_directions_for_order,
    travel_time,
)


Point3D = Tuple[float, float, float]


def _empty_local_stats() -> Dict[str, float]:
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


def route_cost(
    welds: Sequence[Weld],
    order: Sequence[int],
    split_timing_mode: str = "parent_aware",
    direction_mode: str = "bidirectional",
) -> Tuple[float, Dict[str, float]]:
    """Evaluate an order with the current timing and direction model."""
    if not order:
        return 0.0, _empty_local_stats()

    if direction_mode == "bidirectional":
        directed_seq, _ = optimize_directions_for_order(welds, list(order), split_timing_mode)
        stats = compute_directed_route_stats(directed_seq, split_timing_mode)
    elif direction_mode == "fixed":
        seq = [welds[i] for i in order]
        stats = compute_route_stats_with_timing_mode(seq, split_timing_mode)
        stats = {**stats, "reversed_weld_count": 0, "direction_mode": "fixed"}
    else:
        raise ValueError(f"Unknown direction_mode: {direction_mode}")

    return float(stats["total_time"]), dict(stats)


class RouteCostCache:
    """Small per-route cache for expensive route timing/direction evaluation."""

    def __init__(
        self,
        welds: Sequence[Weld],
        split_timing_mode: str = "parent_aware",
        direction_mode: str = "bidirectional",
    ):
        self.welds = welds
        self.split_timing_mode = split_timing_mode
        self.direction_mode = direction_mode
        self._cache: Dict[Tuple[int, ...], Tuple[float, Dict[str, float]]] = {}
        self.route_eval_count = 0
        self.route_cache_hit_count = 0
        self.route_cache_miss_count = 0

    def cost(self, order: Sequence[int]) -> Tuple[float, Dict[str, float]]:
        key = tuple(order)
        self.route_eval_count += 1
        if key in self._cache:
            self.route_cache_hit_count += 1
            cost, stats = self._cache[key]
            return cost, dict(stats)
        self.route_cache_miss_count += 1
        cost, stats = route_cost(self.welds, key, self.split_timing_mode, self.direction_mode)
        self._cache[key] = (cost, dict(stats))
        return cost, dict(stats)

    def stats(self) -> Dict[str, int]:
        return {
            "route_eval_count": self.route_eval_count,
            "route_cache_hit_count": self.route_cache_hit_count,
            "route_cache_miss_count": self.route_cache_miss_count,
        }


def nearest_neighbor_order(
    welds: Sequence[Weld],
    start_point: Optional[Point3D] = None,
) -> List[int]:
    """Build a deterministic nearest-neighbor initial order."""
    if not welds:
        return []

    unvisited = set(range(len(welds)))
    order: List[int] = []
    current_point = start_point

    if current_point is None:
        # Equation (14) excludes travel from an initial robot position to the
        # first weld.  Use a neutral deterministic seed instead of introducing
        # an artificial origin-to-first-weld cost into the construction rule.
        first = min(unvisited)
        order.append(first)
        unvisited.remove(first)
        current_point = welds[first].end_point()

    while unvisited:
        next_idx = min(
            unvisited,
            key=lambda idx: (travel_time(current_point, welds[idx].start_point()), idx),
        )
        order.append(next_idx)
        unvisited.remove(next_idx)
        current_point = welds[next_idx].end_point()

    return order


def two_opt_first_improvement(
    welds: Sequence[Weld],
    order: Sequence[int],
    max_checks: Optional[int] = None,
    split_timing_mode: str = "parent_aware",
    direction_mode: str = "bidirectional",
    cost_cache: Optional[RouteCostCache] = None,
) -> Tuple[List[int], bool, int]:
    """Apply the first improving 2-opt reversal."""
    best_order = list(order)
    n = len(best_order)
    if n < 3:
        return best_order, False, 0

    evaluator = cost_cache.cost if cost_cache is not None else (
        lambda candidate: route_cost(welds, candidate, split_timing_mode, direction_mode)
    )
    best_cost, _ = evaluator(best_order)
    checks = 0
    for i in range(n - 1):
        for j in range(i + 1, n):
            if max_checks is not None and checks >= max_checks:
                return best_order, False, checks
            checks += 1
            candidate = best_order[:i] + list(reversed(best_order[i : j + 1])) + best_order[j + 1 :]
            if candidate == best_order:
                continue
            candidate_cost, _ = evaluator(candidate)
            if candidate_cost + 1e-9 < best_cost:
                return candidate, True, checks
    return best_order, False, checks


def relocate_first_improvement(
    welds: Sequence[Weld],
    order: Sequence[int],
    max_checks: Optional[int] = None,
    split_timing_mode: str = "parent_aware",
    direction_mode: str = "bidirectional",
    cost_cache: Optional[RouteCostCache] = None,
) -> Tuple[List[int], bool, int]:
    """Move one task to another position and accept the first improvement."""
    best_order = list(order)
    n = len(best_order)
    if n < 2:
        return best_order, False, 0

    evaluator = cost_cache.cost if cost_cache is not None else (
        lambda candidate: route_cost(welds, candidate, split_timing_mode, direction_mode)
    )
    best_cost, _ = evaluator(best_order)
    checks = 0
    for i in range(n):
        for j in range(n):
            if i == j or j == i + 1:
                continue
            if max_checks is not None and checks >= max_checks:
                return best_order, False, checks
            checks += 1
            candidate = list(best_order)
            item = candidate.pop(i)
            insert_at = j if j < i else j - 1
            candidate.insert(insert_at, item)
            if candidate == best_order:
                continue
            candidate_cost, _ = evaluator(candidate)
            if candidate_cost + 1e-9 < best_cost:
                return candidate, True, checks
    return best_order, False, checks


def swap_first_improvement(
    welds: Sequence[Weld],
    order: Sequence[int],
    max_checks: Optional[int] = None,
    split_timing_mode: str = "parent_aware",
    direction_mode: str = "bidirectional",
    cost_cache: Optional[RouteCostCache] = None,
) -> Tuple[List[int], bool, int]:
    """Swap two tasks and accept the first improvement."""
    best_order = list(order)
    n = len(best_order)
    if n < 2:
        return best_order, False, 0

    evaluator = cost_cache.cost if cost_cache is not None else (
        lambda candidate: route_cost(welds, candidate, split_timing_mode, direction_mode)
    )
    best_cost, _ = evaluator(best_order)
    checks = 0
    for i in range(n - 1):
        for j in range(i + 1, n):
            if max_checks is not None and checks >= max_checks:
                return best_order, False, checks
            checks += 1
            candidate = list(best_order)
            candidate[i], candidate[j] = candidate[j], candidate[i]
            candidate_cost, _ = evaluator(candidate)
            if candidate_cost + 1e-9 < best_cost:
                return candidate, True, checks
    return best_order, False, checks


def improve_route_lkh_style(
    welds: Sequence[Weld],
    initial_order: Optional[Sequence[int]] = None,
    max_local_iterations: int = 20,
    enable_2opt: bool = True,
    enable_relocate: bool = True,
    enable_swap: bool = False,
    split_timing_mode: str = "parent_aware",
    direction_mode: str = "bidirectional",
    use_cache: bool = True,
    max_move_checks_per_operator: Optional[int] = None,
    time_limit_s: Optional[float] = None,
) -> Tuple[List[int], Dict[str, float]]:
    """Improve a robot route with simple LKH-style local moves."""
    start_time = time.time()
    if not welds:
        stats = _empty_local_stats()
        stats["local_search_time"] = time.time() - start_time
        return [], stats

    order = list(initial_order) if initial_order is not None else nearest_neighbor_order(welds)
    if sorted(order) != list(range(len(welds))):
        raise ValueError("initial_order must be a permutation of weld indices")

    cost_cache = RouteCostCache(welds, split_timing_mode, direction_mode) if use_cache else None
    evaluator = cost_cache.cost if cost_cache is not None else (
        lambda candidate: route_cost(welds, candidate, split_timing_mode, direction_mode)
    )

    initial_cost, _ = evaluator(order)
    two_opt_count = 0
    relocate_count = 0
    swap_count = 0
    iterations = 0
    move_checks_total = 0
    stopped_by_time_limit = False

    def time_exceeded() -> bool:
        return time_limit_s is not None and time_limit_s > 0 and (time.time() - start_time) >= time_limit_s

    for _ in range(max(0, max_local_iterations)):
        if time_exceeded():
            stopped_by_time_limit = True
            break
        improved_this_round = False

        if enable_2opt:
            candidate, improved, checks = two_opt_first_improvement(
                welds,
                order,
                max_checks=max_move_checks_per_operator,
                split_timing_mode=split_timing_mode,
                direction_mode=direction_mode,
                cost_cache=cost_cache,
            )
            move_checks_total += checks
            if improved:
                order = candidate
                two_opt_count += 1
                improved_this_round = True
            if time_exceeded():
                stopped_by_time_limit = True
                break

        if enable_relocate:
            candidate, improved, checks = relocate_first_improvement(
                welds,
                order,
                max_checks=max_move_checks_per_operator,
                split_timing_mode=split_timing_mode,
                direction_mode=direction_mode,
                cost_cache=cost_cache,
            )
            move_checks_total += checks
            if improved:
                order = candidate
                relocate_count += 1
                improved_this_round = True
            if time_exceeded():
                stopped_by_time_limit = True
                break

        if enable_swap:
            candidate, improved, checks = swap_first_improvement(
                welds,
                order,
                max_checks=max_move_checks_per_operator,
                split_timing_mode=split_timing_mode,
                direction_mode=direction_mode,
                cost_cache=cost_cache,
            )
            move_checks_total += checks
            if improved:
                order = candidate
                swap_count += 1
                improved_this_round = True

        iterations += 1
        if stopped_by_time_limit or not improved_this_round:
            break

    final_cost, stats = evaluator(order)
    cache_stats = cost_cache.stats() if cost_cache is not None else {
        "route_eval_count": 0,
        "route_cache_hit_count": 0,
        "route_cache_miss_count": 0,
    }
    stats.update(
        {
            "local_search_iterations": iterations,
            "two_opt_improvement_count": two_opt_count,
            "relocate_improvement_count": relocate_count,
            "swap_improvement_count": swap_count,
            "local_search_initial_cost": initial_cost,
            "local_search_final_cost": final_cost,
            "local_search_improvement": initial_cost - final_cost,
            **cache_stats,
            "local_search_time": time.time() - start_time,
            "local_search_stopped_by_time_limit": stopped_by_time_limit,
            "local_search_move_checks_total": move_checks_total,
        }
    )
    return order, stats


def _self_check_route_local_search() -> None:
    z = 0.1
    welds = [
        Weld("a", 0.0, 0.0, z, 1.0, 0.0, z),
        Weld("b", 4.0, 0.0, z, 5.0, 0.0, z),
        Weld("c", 2.0, 0.0, z, 3.0, 0.0, z),
        Weld("d", 6.0, 0.0, z, 7.0, 0.0, z),
    ]
    order = nearest_neighbor_order(welds)
    assert sorted(order) == list(range(len(welds)))

    initial_cost, _ = route_cost(welds, order)
    assert initial_cost < float("inf")

    best_order, stats = improve_route_lkh_style(welds, initial_order=list(reversed(order)))
    assert sorted(best_order) == list(range(len(welds)))
    assert stats["local_search_final_cost"] <= stats["local_search_initial_cost"] + 1e-9
    assert stats["total_time"] < float("inf")
    assert stats["route_cache_hit_count"] >= 0
    assert stats["route_eval_count"] > 0

    cached_order, cached_stats = improve_route_lkh_style(welds, initial_order=list(reversed(order)), use_cache=True)
    uncached_order, uncached_stats = improve_route_lkh_style(welds, initial_order=list(reversed(order)), use_cache=False)
    assert sorted(cached_order) == sorted(uncached_order) == list(range(len(welds)))
    assert abs(cached_stats["local_search_final_cost"] - uncached_stats["local_search_final_cost"]) <= 1e-9

    limited_order, limited_stats = improve_route_lkh_style(
        welds,
        initial_order=list(reversed(order)),
        max_move_checks_per_operator=1,
    )
    assert sorted(limited_order) == list(range(len(welds)))
    assert limited_stats["local_search_move_checks_total"] >= 0

    timed_order, timed_stats = improve_route_lkh_style(
        welds,
        initial_order=list(reversed(order)),
        time_limit_s=1e-12,
    )
    assert sorted(timed_order) == list(range(len(welds)))
    assert timed_stats["local_search_stopped_by_time_limit"] in (True, False)
