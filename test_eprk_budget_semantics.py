import json
import unittest
from pathlib import Path

from atomic_problem_core import load_atomic_instance
from MRTA_EPRK_MA import EPRKConfig, EPRKMASolver


class EPRKBudgetTests(unittest.TestCase):
    def setUp(self):
        self.instance = load_atomic_instance("data/instances/atomic_l5_l1/instance_w30_atomic.json")
        self.spec = json.loads(Path("atomic_normalization_spec.json").read_text(encoding="utf-8"))["instances"]["w30"]

    def test_phenotype_cache_hit_still_consumes_primary(self):
        solver = EPRKMASolver(self.instance, self.spec, 101, primary_budget=2); genes = solver._random_genes()
        solver.evaluate(genes); solver.evaluate(genes)
        self.assertEqual(solver.primary_evaluations, 2); self.assertEqual(solver.phenotype_cache_hits, 1)

    def test_exact_budget_stop(self):
        config = EPRKConfig(population_size=6, population_count=1, max_local_improvements=1)
        solver = EPRKMASolver(self.instance, self.spec, 101, config, primary_budget=37)
        best = solver.run(); self.assertEqual(solver.primary_evaluations, 37)
        self.assertEqual(solver.metrics(best)["stop_reason"], "objective_budget")

    def test_existing_formal_gate_remains_fail_closed(self):
        protocol = json.loads(Path("UNIFIED_EXPERIMENT_PROTOCOL.json").read_text(encoding="utf-8"))
        self.assertFalse(protocol["approval_gates"]["user_approved_formal_execution"])
        self.assertFalse(protocol["formal_run_approved"])


if __name__ == "__main__": unittest.main()
