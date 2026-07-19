import copy
import json
import tempfile
import unittest
from pathlib import Path

import collision_aware_schedule as collision
import run_unified_experiments as runner
from project_paths import resolve_project_path
from generate_welds import Weld, load_frozen_weld_instance
from mrta_problem_core import DirectedWeld, MotionModel, assign_welds_to_robots_split, compute_directed_route_stats
from solver_output_metrics import build_robot_metrics, build_standard_result, build_system_metrics


ROOT = Path(__file__).resolve().parents[1]


class CollisionPostprocessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.protocol = runner.read_json(resolve_project_path("UNIFIED_EXPERIMENT_PROTOCOL.json"))

    def known_conflict_sequences(self):
        z = 0.1
        return [
            [DirectedWeld(Weld("r0", 9.8, 7.0, z, 9.9, 7.0, z))],
            [DirectedWeld(Weld("r1", 10.1, 7.0, z, 10.2, 7.0, z))],
            [], [],
        ]

    def frozen_payload(self):
        instance = self.protocol["instances"]["w30"]
        baseline = instance["normalization"]["baseline"]
        welds, _ = load_frozen_weld_instance(str(ROOT / instance["path"]))
        assigned, _ = assign_welds_to_robots_split(
            welds, baseline["boundaries"]["x_up"], baseline["boundaries"]["x_low"]
        )
        stats = []
        model = MotionModel()
        for tasks, ids, flags in zip(assigned, baseline["routes"], baseline["direction_flags"]):
            by_id = {str(task.id): task for task in tasks}
            directed = [DirectedWeld(by_id[task_id], flags[pos]) for pos, task_id in enumerate(ids)]
            stats.append(compute_directed_route_stats(directed, model))
        robots = build_robot_metrics(baseline["routes"], baseline["direction_flags"], stats)
        system = build_system_metrics(robots, 0.5)
        payload = build_standard_result(
            "GA+ACO", instance["instance_hash"], 42,
            baseline["boundaries"]["x_up"], baseline["boundaries"]["x_low"],
            robots, system, 12.5,
        )
        payload["metrics"] = {
            "x_up": baseline["boundaries"]["x_up"], "x_low": baseline["boundaries"]["x_low"],
            "makespan": system["makespan"], "fitness": 0.5,
        }
        unified = {
            "run_status": "success", "run_class": "budget_pilot", "solver_name": "GA+ACO",
            "solver_seed": 42, "instance_hash": instance["instance_hash"],
            "realized_objective_evaluations": 16000, "makespan": system["makespan"],
            "fitness": 0.5, "algorithm_time": 12.5, "solver_wall_clock_time": 13.0,
            "protocol_hash": "raw-protocol",
        }
        return payload, unified

    def write_attempt(self, folder, solver_name="GA+ACO", corrupt=False):
        run_dir = Path(folder)
        payload, unified = self.frozen_payload()
        payload["solver_name"] = solver_name
        unified["solver_name"] = solver_name
        if corrupt:
            payload.pop("solution")
        runner.atomic_write_json(run_dir / "result.json", payload)
        runner.atomic_write_json(run_dir / "unified_metrics.json", unified)
        return run_dir

    def test_01_empty_routes(self):
        audited = collision.audit_boundary_collisions_for_four_robots([[], [], [], []], 10, 10)
        self.assertEqual(audited["conflict_count"], 0)
        self.assertEqual(audited["collision_adjusted_makespan"], 0.0)

    def test_02_single_robot_route(self):
        sequence = self.known_conflict_sequences()[0]
        audited = collision.audit_boundary_collisions_for_four_robots([sequence, [], [], []], 10, 10)
        self.assertEqual(audited["conflict_count"], 0)

    def test_03_known_conflict_is_detected(self):
        audited = collision.audit_boundary_collisions_for_four_robots(self.known_conflict_sequences(), 10, 10)
        self.assertGreater(audited["conflict_count"], 0)

    def test_04_added_wait_is_nonnegative(self):
        audited = collision.audit_boundary_collisions_for_four_robots(self.known_conflict_sequences(), 10, 10)
        self.assertGreaterEqual(audited["added_waiting_time"], 0.0)

    def test_05_adjusted_makespan_not_below_raw_trace_makespan(self):
        audited = collision.audit_boundary_collisions_for_four_robots(self.known_conflict_sequences(), 10, 10)
        self.assertGreaterEqual(audited["collision_adjusted_makespan"], audited["base_makespan"])

    def test_06_raw_files_are_untouched(self):
        with tempfile.TemporaryDirectory() as folder:
            run_dir = self.write_attempt(folder)
            before = (runner.sha256_file(run_dir / "result.json"), runner.sha256_file(run_dir / "unified_metrics.json"))
            record = runner.postprocess_collision_attempt(self.protocol, run_dir / "unified_metrics.json")
            after = (runner.sha256_file(run_dir / "result.json"), runner.sha256_file(run_dir / "unified_metrics.json"))
            self.assertEqual(before, after)
            self.assertTrue(record["raw_files_unchanged"])

    def test_07_deterministic_replay(self):
        first = collision.audit_boundary_collisions_for_four_robots(self.known_conflict_sequences(), 10, 10)
        second = collision.audit_boundary_collisions_for_four_robots(self.known_conflict_sequences(), 10, 10)
        self.assertEqual(first, second)

    def test_08_same_postprocess_policy_across_three_solvers(self):
        names = ["GA+ACO", "Paper-Aligned-HGA-XCut-Control", "ABMA"]
        with tempfile.TemporaryDirectory() as folder:
            values = []
            for index, name in enumerate(names):
                run_dir = Path(folder) / str(index)
                run_dir.mkdir()
                self.write_attempt(run_dir, name)
                record = runner.postprocess_collision_attempt(self.protocol, run_dir / "unified_metrics.json")
                values.append((record["collision_adjusted_makespan"], record["added_waiting_time"],
                               record["conflict_count"], record["unresolved_conflict_count"]))
            self.assertEqual(values[0], values[1])
            self.assertEqual(values[1], values[2])

    def test_09_collision_timing_is_separate(self):
        with tempfile.TemporaryDirectory() as folder:
            run_dir = self.write_attempt(folder)
            original = runner.read_json(run_dir / "unified_metrics.json")
            record = runner.postprocess_collision_attempt(self.protocol, run_dir / "unified_metrics.json")
            current = runner.read_json(run_dir / "unified_metrics.json")
            self.assertGreaterEqual(record["collision_audit_time"], 0.0)
            self.assertEqual(current["algorithm_time"], original["algorithm_time"])
            self.assertNotIn("collision_audit_time", current)

    def test_10_failure_is_explicit_and_never_claims_success(self):
        with tempfile.TemporaryDirectory() as folder:
            run_dir = self.write_attempt(folder, corrupt=True)
            record = runner.postprocess_collision_attempt(self.protocol, run_dir / "unified_metrics.json")
            self.assertEqual(record["audit_status"], "failed")
            self.assertFalse(record["collision_success_claimed"])
            self.assertTrue(record["audit_failure_reason"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
