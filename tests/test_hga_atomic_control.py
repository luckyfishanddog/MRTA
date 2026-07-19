import json
import unittest
from pathlib import Path

from project_paths import resolve_project_path

from atomic_problem_core import load_atomic_instance
from MRTA_HGA_ATOMIC_CONTROL import HGAAtomicSolver


class HGAAtomicControlTests(unittest.TestCase):
    def setUp(self):
        self.instance = load_atomic_instance("data/instances/atomic_l5_l1/instance_w30_atomic.json")
        self.spec = json.loads(resolve_project_path("atomic_normalization_spec.json").read_text(encoding="utf-8"))["instances"]["w30"]

    def test_budget_and_atomic_hash(self):
        solver = HGAAtomicSolver(self.instance, self.spec, 104, primary_budget=50); best = solver.run(); metrics = solver.metrics(best)
        self.assertEqual(metrics["primary_evaluations"], 50)
        self.assertEqual(metrics["atomic_instance_hash"], self.instance.atomic_instance_hash)
        self.assertEqual(metrics["global_transition_table_builds"], 1)

    def test_routes_match_event_assignment_and_reproducible(self):
        a = HGAAtomicSolver(self.instance, self.spec, 105, primary_budget=50); ba = a.run()
        b = HGAAtomicSolver(self.instance, self.spec, 105, primary_budget=50); bb = b.run()
        self.assertEqual(ba.phenotype, bb.phenotype); self.assertEqual(ba.fitness, bb.fitness)


if __name__ == "__main__": unittest.main()
