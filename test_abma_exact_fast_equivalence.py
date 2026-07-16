import itertools
import math
import random
import unittest

import mrta_problem_core as core
from generate_welds import Weld
from MRTA_ABMA import (
    ABMAConfig,
    ABMASolver,
    PartitionSolution,
    RouteEvaluator,
    _repair_route,
    _system_fitness,
)


def sample_welds():
    z = 0.1
    return [
        Weld("u0", 1.0, 9.0, z, 2.0, 9.5, z),
        Weld("u1", 5.0, 8.0, z, 6.0, 8.3, z),
        Weld("l0", 3.0, 3.0, z, 4.0, 3.2, z),
        Weld("l1", 14.0, 2.0, z, 15.0, 2.4, z),
        Weld("x0", 9.5, 8.5, z, 10.5, 8.7, z),
        Weld("y0", 7.0, 5.5, z, 7.3, 6.5, z),
    ]


class ABMAExactFastEquivalenceTests(unittest.TestCase):
    def setUp(self):
        self.welds = sample_welds()
        self.model = core.MotionModel()

    def evaluator_pair(self, welds=None):
        values = self.welds if welds is None else welds
        return RouteEvaluator(values, self.model), RouteEvaluator(values, self.model, True)

    def solve_pair(self, budget=8):
        base = dict(seed=42, population_size=4, generations=2,
                    max_objective_evaluations=budget, alns_iterations=5,
                    vnd_iterations=1, max_move_checks_per_operator=8,
                    early_stop_patience=0, verbose=False)
        return (
            ABMASolver(self.welds, ABMAConfig(abma_variant="legacy", **base)).solve(),
            ABMASolver(self.welds, ABMAConfig(abma_variant="legacy_exact_fast", **base)).solve(),
        )

    def test_01_profile_validates_and_keeps_legacy_inner_budget(self):
        config = ABMAConfig(abma_variant="legacy_exact_fast")
        config.validate()
        self.assertTrue(config.exact_fast_enabled)
        self.assertEqual((config.alns_iterations, config.vnd_iterations), (80, 3))

    def test_02_empty_route_is_exact(self):
        legacy, fast = self.evaluator_pair([])
        self.assertEqual(legacy.evaluate([]), fast.evaluate([]))

    def test_03_singleton_route_is_exact(self):
        legacy, fast = self.evaluator_pair(self.welds[:1])
        self.assertEqual(legacy.evaluate([0]), fast.evaluate([0]))

    def test_04_all_small_route_permutations_are_exact(self):
        legacy, fast = self.evaluator_pair(self.welds[:4])
        for order in itertools.permutations(range(4)):
            self.assertEqual(legacy.evaluate(order), fast.evaluate(order))

    def test_05_direction_flags_are_exact(self):
        legacy, fast = self.evaluator_pair(self.welds[:5])
        for order in ([0, 2, 1, 4, 3], [4, 3, 2, 1, 0]):
            self.assertEqual(legacy.evaluate(order)[1]["direction_flags"],
                             fast.evaluate(order)[1]["direction_flags"])

    def test_06_transition_time_and_distance_precomputation_is_exact(self):
        _, fast = self.evaluator_pair(self.welds[:3])
        for previous, current, pd, cd in itertools.product(range(3), range(3), (0, 1), (0, 1)):
            p = self.welds[previous].start_point() if pd else self.welds[previous].end_point()
            c = self.welds[current].end_point() if cd else self.welds[current].start_point()
            key = (previous, pd, current, cd)
            self.assertEqual(fast.transition_times[key], core.travel_time(p, c, self.model))
            self.assertEqual(fast.transition_distances[key], core.travel_distance(p, c, self.model))

    def test_07_route_cache_returns_exact_same_value(self):
        legacy, fast = self.evaluator_pair(self.welds[:4])
        order = [3, 1, 0, 2]
        self.assertEqual(legacy.evaluate(order), fast.evaluate(order))
        self.assertEqual(legacy.evaluate(order), fast.evaluate(order))
        self.assertEqual((legacy.hits, fast.hits), (1, 1))

    def test_08_regret_insertion_order_is_exact(self):
        legacy, fast = self.evaluator_pair(self.welds[:5])
        self.assertEqual(_repair_route([], range(5), legacy, 2),
                         _repair_route([], range(5), fast, 2))

    def test_09_system_fitness_is_exact(self):
        legacy_eval, fast_eval = self.evaluator_pair(self.welds[:4])
        stats_a = [legacy_eval.evaluate([0, 1, 2, 3])[1]] * 4
        stats_b = [fast_eval.evaluate([0, 1, 2, 3])[1]] * 4
        legacy_config = ABMAConfig(abma_variant="legacy")
        fast_config = ABMAConfig(abma_variant="legacy_exact_fast")
        legacy_config.validate(); fast_config.validate()
        self.assertEqual(_system_fitness(stats_a, legacy_config, (1.0, 1.0, 1.0)),
                         _system_fitness(stats_b, fast_config, (1.0, 1.0, 1.0)))

    def test_10_solution_clone_preserves_mutation_isolation(self):
        source = PartitionSolution(1.0, 2.0, robots_stats=[{"total_time": 3.0}],
                                   robot_orders=[[0, 1]], robot_order_ids=[["a", "b"]])
        cloned = source.clone()
        cloned.robots_stats[0]["total_time"] = 9.0
        cloned.robot_orders[0].append(2)
        self.assertEqual(source.robots_stats[0]["total_time"], 3.0)
        self.assertEqual(source.robot_orders[0], [0, 1])

    def test_11_outer_candidate_and_acceptance_hashes_are_exact(self):
        legacy, fast = self.solve_pair()
        for key in ("candidate_sequence_hash", "candidate_acceptance_hash",
                    "outer_rng_final_state_hash", "inner_rng_final_state_hash"):
            self.assertEqual(legacy.diagnostics[key], fast.diagnostics[key])

    def test_12_final_boundaries_routes_directions_and_metrics_are_exact(self):
        legacy, fast = self.solve_pair()
        left, right = legacy.best, fast.best
        self.assertEqual((left.x_up, left.x_low), (right.x_up, right.x_low))
        self.assertEqual(left.robot_order_ids, right.robot_order_ids)
        self.assertEqual(left.robot_direction_flags, right.robot_direction_flags)
        self.assertEqual((left.makespan, left.load_imbalance, left.total_idle_distance, left.fitness),
                         (right.makespan, right.load_imbalance, right.total_idle_distance, right.fitness))

    def test_13_trace_budget_and_stop_reason_are_exact(self):
        legacy, fast = self.solve_pair()
        self.assertEqual(legacy.history, fast.history)
        self.assertEqual(legacy.diagnostics["objective_evaluation_count"], 8)
        self.assertEqual(legacy.diagnostics["objective_evaluation_count"],
                         fast.diagnostics["objective_evaluation_count"])
        self.assertEqual(legacy.diagnostics["stop_reason"], fast.diagnostics["stop_reason"])
        def strip_time(trace):
            return [{k: v for k, v in item.items()
                     if k not in ("elapsed_time", "elapsed_algorithm_time")} for item in trace]
        self.assertEqual(strip_time(legacy.diagnostics["checkpoint_trace"]),
                         strip_time(fast.diagnostics["checkpoint_trace"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
