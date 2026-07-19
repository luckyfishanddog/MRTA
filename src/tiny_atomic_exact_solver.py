"""Exhaustive event/route/direction oracle for 4--8 atomic-weld cases."""

from __future__ import annotations

import itertools
import json
import math
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

from atomic_problem_core import AtomicInstance, AtomicWeld, atomic_payload_hash, event_pair_routes, generate_boundary_events
from atomic_route_evaluator import AtomicRouteEvaluator, SystemResult
from mrta_problem_core import DEFAULT_MOTION_MODEL


def _weld(case: str, index: int, half: str, start, end, parent: str | None = None) -> AtomicWeld:
    length = math.dist(start, end); source = f"tiny-{case}"
    return AtomicWeld(f"{case}-a{index}", parent or f"{case}-p{index}", parent or f"{case}-p{index}", half, 1, 1,
                      tuple(start), tuple(end), length, abs(end[0] - start[0]), length / DEFAULT_MOTION_MODEL.weld_speed,
                      False, False, source)


def build_tiny_instance(name: str, specs: Sequence[Tuple[str, tuple, tuple, str | None]]) -> AtomicInstance:
    welds = tuple(_weld(name, i, half, start, end, parent) for i, (half, start, end, parent) in enumerate(specs))
    metadata = {"source_instance_hash": f"tiny-{name}", "model_id": "atomic_weld_partition_routing_v1"}
    ahash = atomic_payload_hash(welds, metadata)
    return AtomicInstance(name, welds, tuple(generate_boundary_events(welds, "upper")),
                          tuple(generate_boundary_events(welds, "lower")), metadata, ahash)


def build_tiny_cases() -> Dict[str, AtomicInstance]:
    z = 0.1
    return {
        "shared_endpoint_4": build_tiny_instance("shared_endpoint_4", [
            ("upper", (1, 9, z), (2, 9, z), "u-chain"), ("upper", (2, 9, z), (3, 9, z), "u-chain"),
            ("lower", (12, 3, z), (13, 3, z), "l-chain"), ("lower", (13, 3, z), (14, 3, z), "l-chain")]),
        "balanced_5": build_tiny_instance("balanced_5", [
            ("upper", (1, 8, z), (2, 8, z), None), ("upper", (8, 10, z), (9, 10, z), None),
            ("upper", (15, 9, z), (16, 9, z), None), ("lower", (3, 2, z), (4, 2, z), None),
            ("lower", (14, 4, z), (15, 4, z), None)]),
        "direction_6": build_tiny_instance("direction_6", [
            ("upper", (1, 8, z), (2, 9, z), None), ("upper", (4, 9, z), (3, 8, z), None),
            ("upper", (14, 8, z), (15, 9, z), None), ("lower", (1, 2, z), (2, 3, z), None),
            ("lower", (10, 3, z), (11, 2, z), None), ("lower", (17, 2, z), (18, 3, z), None)]),
        "vertical_x_7": build_tiny_instance("vertical_x_7", [
            ("upper", (2, 8, z), (2, 10, z), None), ("upper", (5, 9, z), (6, 9, z), None),
            ("upper", (14, 9, z), (15, 9, z), None), ("lower", (2, 2, z), (3, 2, z), None),
            ("lower", (7, 3, z), (8, 3, z), None), ("lower", (13, 2, z), (14, 2, z), None),
            ("lower", (17, 4, z), (18, 4, z), None)]),
        "mixed_8": build_tiny_instance("mixed_8", [
            ("upper", (1, 8, z), (2, 8, z), "ua"), ("upper", (2, 8, z), (3, 8, z), "ua"),
            ("upper", (11, 10, z), (12, 9, z), None), ("upper", (17, 8, z), (18, 8, z), None),
            ("lower", (1, 2, z), (2, 2, z), "la"), ("lower", (2, 2, z), (3, 2, z), "la"),
            ("lower", (11, 4, z), (12, 3, z), None), ("lower", (17, 2, z), (18, 2, z), None)])
    }


def solve_exact(instance: AtomicInstance, normalization_spec=None) -> Dict[str, Any]:
    evaluator = AtomicRouteEvaluator(instance, normalization_spec)
    best: SystemResult | None = None; best_events = None; candidates = 0
    for upper in instance.upper_events:
        for lower in instance.lower_events:
            assigned = event_pair_routes(upper, lower)
            permutations = [list(itertools.permutations(ids)) if ids else [()] for ids in assigned]
            for routes in itertools.product(*permutations):
                candidates += 1; result = evaluator.evaluate_system(routes)
                if best is None or result.fitness < best.fitness - 1e-12:
                    best = result; best_events = (upper.event_index, lower.event_index)
    assert best is not None
    return {"case": instance.name, "atomic_weld_count": len(instance.atomic_welds), "candidate_count": candidates,
            "upper_event_index": best_events[0], "lower_event_index": best_events[1], **best.to_dict(), **evaluator.counters()}


def run_all(output: Path = Path("tiny_atomic_exact_results.json")) -> Dict[str, Any]:
    from MRTA_EPRK_MA import EPRKConfig, EPRKMASolver
    results = {}
    for name, instance in build_tiny_cases().items():
        spec = {"atomic_instance_hash": instance.atomic_instance_hash,
                "ideal": {"makespan": 0.0, "load_imbalance": 0.0, "idle_distance": 0.0},
                "scale": {"makespan": 1.0, "load_imbalance": 1.0, "idle_distance": 1.0},
                "weights": [0.7, 0.2, 0.1]}
        exact = solve_exact(instance, spec)
        config = EPRKConfig(population_size=20, population_count=1, candidate_list_size=8, max_local_improvements=10)
        solver = EPRKMASolver(instance, spec, 20260716, config, primary_budget=2000)
        heuristic = solver.run()
        gap = 100.0 * (heuristic.fitness - exact["fitness"]) / max(1e-12, exact["fitness"])
        exact.update({"eprk_fitness": heuristic.fitness, "eprk_gap_percent": gap,
                      "eprk_reached_global_optimum": abs(gap) <= 1e-9,
                      "eprk_primary_evaluations": solver.primary_evaluations})
        results[name] = exact
    payload = {"status": "passed", "case_count": len(results), "results": results}
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return payload


if __name__ == "__main__": print(json.dumps(run_all(), ensure_ascii=False, indent=2))
