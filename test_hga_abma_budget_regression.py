import unittest

from generate_welds import load_frozen_weld_instance
from MRTA_ABMA import ABMAConfig, ABMASolver
from MRTA_HGA_PAPER_ALIGNED_CONTROL import PaperAlignedHGA, PaperAlignedHGAIndividual


INSTANCE = r"data\instances\selected\instance_w30.xlsx"


class HGAABMABudgetRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.welds, _ = load_frozen_weld_instance(INSTANCE)

    def test_hga_reports_unique_and_duplicate_complete_candidates(self):
        solver = PaperAlignedHGA(
            self.welds, 42, max_objective_evaluations=3,
            enable_project_fallback=False, enable_partition=False,
        )
        individual = solver.decode_and_repair_individual(
            PaperAlignedHGAIndividual(10, 10), randomize_missing=False
        )
        robots, _ = solver._assignment(10, 10)
        solver._evaluate(individual, robots)
        self.assertEqual(solver.counts["unique_complete_candidate_count"], 1)
        self.assertEqual(solver.counts["duplicate_complete_candidate_count"], 1)
        self.assertEqual(solver.counts["fitness_evaluation_count"], 2)

    def test_hga_initialization_budget_stops_exactly(self):
        solver = PaperAlignedHGA(
            self.welds, 42, max_objective_evaluations=6,
            enable_project_fallback=False, enable_partition=False,
            enable_paper_vnd=False,
        )
        _, stats = solver.run_paper(mu=20, lamb=1, max_offspring=20)
        self.assertEqual(stats["objective_evaluation_count"], 6)
        self.assertEqual(stats["stop_reason"], "objective_budget")
        self.assertEqual(stats["unique_complete_candidate_count"] +
                         stats["duplicate_complete_candidate_count"], 6)
        for key in ("assignment_cache_hits", "assignment_cache_misses",
                    "route_cache_hits", "route_cache_misses"):
            self.assertIn(key, stats)

    def test_abma_cache_hit_still_consumes_one_outer_observation(self):
        config = ABMAConfig(
            seed=42, population_size=4, generations=0, verbose=False,
            abma_variant="incremental_multifidelity",
            low_alns_iterations=1, mid_alns_iterations=1, high_alns_iterations=1,
            low_vnd_iterations=0, mid_vnd_iterations=0, high_vnd_iterations=0,
        )
        solver = ABMASolver(self.welds, config)
        solver._initialize_references()
        solver._raw_evaluate(10, 10, required_fidelity="high")
        solver._raw_evaluate(10, 10, required_fidelity="high")
        self.assertEqual(solver.objective_evaluations, 2)
        self.assertEqual(solver.cache_hits, 1)
        self.assertEqual(solver.partition_evaluations, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
