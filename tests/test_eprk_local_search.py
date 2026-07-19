import json
import unittest
from pathlib import Path

from project_paths import resolve_project_path

from atomic_problem_core import load_atomic_instance
from MRTA_EPRK_MA import EPRKConfig, EPRKMASolver


class EPRKLocalSearchTests(unittest.TestCase):
    def setUp(self):
        instance = load_atomic_instance("data/instances/atomic_l5_l1/instance_w30_atomic.json")
        spec = json.loads(resolve_project_path("atomic_normalization_spec.json").read_text(encoding="utf-8"))["instances"]["w30"]
        self.solver = EPRKMASolver(instance, spec, 104, EPRKConfig(max_local_improvements=2, candidate_list_size=4), primary_budget=30)

    def test_local_search_candidates_consume_primary_and_do_not_worsen(self):
        start = self.solver.evaluate(self.solver._random_genes()); before = self.solver.primary_evaluations
        result = self.solver.local_search(start)
        self.assertGreater(self.solver.primary_evaluations, before)
        self.assertLessEqual(result.fitness, start.fitness + 1e-12)

    def test_cached_incremental_route_reuse_equals_full_result(self):
        individual = self.solver.evaluate(self.solver._random_genes())
        first = self.solver.evaluator.evaluate_system(individual.routes)
        hits = self.solver.evaluator.route_cache_hits
        second = self.solver.evaluator.evaluate_system(individual.routes)
        self.assertEqual(first, second); self.assertGreaterEqual(self.solver.evaluator.route_cache_hits, hits + 4)


if __name__ == "__main__": unittest.main()
