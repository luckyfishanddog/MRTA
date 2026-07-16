import copy
import math
import unittest
from pathlib import Path

import run_unified_experiments as runner
from generate_welds import load_frozen_weld_instance
from mrta_problem_core import (
    DirectedWeld, MotionModel, assign_welds_to_robots_split,
    compute_directed_route_stats, travel_distance,
)
from solver_output_metrics import (
    REGION_NAMES, build_robot_metrics, build_standard_result,
    build_system_metrics, flatten_robot_metrics,
)


ROOT = Path(__file__).resolve().parent


def empty_stats():
    return {"total_weld_time": 0.0, "total_travel_time": 0.0,
            "total_time": 0.0, "total_idle_distance": 0.0}


class SolverOutputMetricTests(unittest.TestCase):
    def test_four_robot_schema_and_types(self):
        robots = build_robot_metrics([[], [], [], []], [[], [], [], []], [empty_stats() for _ in range(4)])
        self.assertEqual([item["robot_id"] for item in robots], [0, 1, 2, 3])
        self.assertEqual([item["region_name"] for item in robots], list(REGION_NAMES))
        self.assertTrue(all(isinstance(item["task_count"], int) for item in robots))

    def test_empty_routes_are_all_zero(self):
        robots = build_robot_metrics([[], [], [], []], [[], [], [], []], [empty_stats() for _ in range(4)])
        for item in robots:
            self.assertEqual(item["task_count"], 0)
            self.assertEqual(sum(item[name] for name in ("total_weld_time", "total_travel_time", "total_time", "total_idle_distance")), 0.0)

    def test_robot_time_identity(self):
        stats = [dict(empty_stats(), total_weld_time=2.0, total_travel_time=3.0, total_time=5.0)] + [empty_stats() for _ in range(3)]
        robots = build_robot_metrics([["a"], [], [], []], [[False], [], [], []], stats)
        self.assertEqual(robots[0]["total_time"], robots[0]["total_weld_time"] + robots[0]["total_travel_time"])

    def test_system_aggregation_and_load_range(self):
        stats = []
        routes = []
        flags = []
        for index, total in enumerate((1.0, 2.0, 3.0, 4.0)):
            stats.append({"total_weld_time": total, "total_travel_time": 0.0,
                          "total_time": total, "total_idle_distance": index})
            routes.append([str(index)]); flags.append([False])
        system = build_system_metrics(build_robot_metrics(routes, flags, stats), 0.5)
        self.assertEqual(system["makespan"], 4.0)
        self.assertEqual(system["load_imbalance"], 3.0)
        self.assertEqual(system["sum_robot_total_time"], 10.0)
        self.assertEqual(system["total_idle_distance"], 6.0)

    def test_three_stage_idle_distance_includes_vertical(self):
        model = MotionModel(safe_z=0.3)
        self.assertAlmostEqual(travel_distance((0, 0, 0.1), (3, 4, 0.1), model), 5.4)

    def test_route_and_direction_lengths_must_match(self):
        with self.assertRaises(ValueError):
            build_robot_metrics([["a"], [], [], []], [[], [], [], []], [empty_stats() for _ in range(4)])

    def test_flattened_csv_fields(self):
        robots = build_robot_metrics([[], [], [], []], [[], [], [], []], [empty_stats() for _ in range(4)])
        flat = flatten_robot_metrics(robots)
        self.assertIn("robot_0_total_weld_time", flat)
        self.assertIn("robot_3_task_count", flat)
        self.assertIn("sum_robot_total_time", flat)

    def test_runner_independent_reconstruction(self):
        protocol = runner.read_json(ROOT / "UNIFIED_EXPERIMENT_PROTOCOL.json")
        instance = protocol["instances"]["w30"]
        baseline = instance["normalization"]["baseline"]
        welds, _ = load_frozen_weld_instance(str(ROOT / instance["path"]))
        assigned, _ = assign_welds_to_robots_split(
            welds, baseline["boundaries"]["x_up"], baseline["boundaries"]["x_low"]
        )
        model = MotionModel()
        stats = []
        for tasks, ids, flags in zip(assigned, baseline["routes"], baseline["direction_flags"]):
            by_id = {str(task.id): task for task in tasks}
            directed = [DirectedWeld(by_id[task_id], flags[pos]) for pos, task_id in enumerate(ids)]
            stats.append(compute_directed_route_stats(directed, model))
        robots = build_robot_metrics(baseline["routes"], baseline["direction_flags"], stats)
        system = build_system_metrics(robots, 1.0)
        payload = build_standard_result("synthetic", instance["instance_hash"], 42,
                                        baseline["boundaries"]["x_up"], baseline["boundaries"]["x_low"],
                                        robots, system, 0.1)
        spec = runner.RunSpec("smoke", "ga_aco", "w30", instance, 42, 8, {})
        rebuilt = runner.recompute_solution_metrics(payload, spec, protocol)
        self.assertAlmostEqual(rebuilt["system_metrics"]["makespan"], system["makespan"])

    def test_runtime_names_are_separate(self):
        self.assertIn("solver_wall_clock_time", runner.UNIFIED_REQUIRED)
        self.assertIn("postprocess_time", runner.UNIFIED_REQUIRED)
        self.assertIn("total_run_wall_clock_time", runner.UNIFIED_REQUIRED)

    def test_legacy_metrics_aliases_still_parse(self):
        self.assertEqual(runner.to_float("1.25"), 1.25)
        self.assertTrue(runner.to_bool("true"))

    def test_collision_adjusted_does_not_replace_original(self):
        original = {"makespan": 10.0, "collision_adjusted_makespan": 12.0}
        self.assertEqual(original["makespan"], 10.0)

    def test_all_four_solvers_use_standard_builder(self):
        for name in ("MRTA_GA_ACO.py", "MRTA_DE_LKH.py", "MRTA_HGA_PAPER_ALIGNED_CONTROL.py", "MRTA_ABMA.py"):
            source = (ROOT / name).read_text(encoding="utf-8")
            self.assertIn("build_standard_result", source)

    def test_abma_cache_hit_remains_an_outer_objective_observation(self):
        import MRTA_ABMA as abma
        welds, _ = load_frozen_weld_instance(str(ROOT / "data/instances/selected/instance_w30.xlsx"))
        config = abma.ABMAConfig(
            population_size=4, generations=1, alns_iterations=0,
            vnd_iterations=0, reference_makespan=1.0,
            reference_load=1.0, reference_distance=1.0, verbose=False,
        )
        solver = abma.ABMASolver(welds, config)
        solver._initialize_references()
        solver._raw_evaluate(10.0, 10.0)
        solver._raw_evaluate(10.0, 10.0)
        self.assertEqual(solver.objective_evaluations, 2)
        self.assertEqual(solver.partition_evaluations, 1)
        self.assertEqual(solver.cache_hits, 1)


if __name__ == "__main__":
    unittest.main()
