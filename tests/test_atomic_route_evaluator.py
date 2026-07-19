import itertools
import math
import unittest

from atomic_route_evaluator import AtomicRouteEvaluator
from tiny_atomic_exact_solver import build_tiny_cases


class AtomicRouteEvaluatorTests(unittest.TestCase):
    def test_shared_endpoint_zero_is_bidirectional(self):
        instance = build_tiny_cases()["shared_endpoint_4"]
        evaluator = AtomicRouteEvaluator(instance)
        route = evaluator.evaluate_route(("shared_endpoint_4-a0", "shared_endpoint_4-a1"))
        self.assertEqual(route.travel_time, 0.0)
        self.assertEqual(route.idle_distance, 0.0)
        reverse = evaluator.evaluate_route(("shared_endpoint_4-a1", "shared_endpoint_4-a0"))
        self.assertEqual(reverse.travel_time, 0.0)

    def test_direction_dp_matches_direction_enumeration(self):
        instance = build_tiny_cases()["direction_6"]
        evaluator = AtomicRouteEvaluator(instance); order = tuple(w.id for w in instance.atomic_welds[:3])
        result = evaluator.evaluate_route(order)
        values = []
        for flags in itertools.product((0, 1), repeat=len(order)):
            total = sum(instance.by_id[x].weld_time_s for x in order)
            for i in range(1, len(order)):
                total += evaluator.transition_time[evaluator.index[order[i-1]]][flags[i-1]][evaluator.index[order[i]]][flags[i]]
            values.append(total)
        self.assertAlmostEqual(result.total_time, min(values), places=10)

    def test_one_global_table_and_cache(self):
        instance = build_tiny_cases()["balanced_5"]; evaluator = AtomicRouteEvaluator(instance)
        order = (instance.atomic_welds[0].id,); evaluator.evaluate_route(order); evaluator.evaluate_route(order)
        self.assertEqual(evaluator.table_build_count, 1)
        self.assertEqual(evaluator.route_cache_hits, 1)


if __name__ == "__main__": unittest.main()
