import json
import unittest
from pathlib import Path

from atomic_problem_core import load_atomic_instance
from MRTA_EPRK_MA import EPRKConfig, EPRKMASolver


class EPRKReproducibilityTests(unittest.TestCase):
    def test_same_seed_same_budget_same_solution(self):
        instance = load_atomic_instance("data/instances/atomic_l5_l1/instance_w30_atomic.json")
        spec = json.loads(Path("atomic_normalization_spec.json").read_text(encoding="utf-8"))["instances"]["w30"]
        config = EPRKConfig(population_size=8, population_count=1, max_local_improvements=2)
        a = EPRKMASolver(instance, spec, 103, config, primary_budget=80); ba = a.run()
        b = EPRKMASolver(instance, spec, 103, config, primary_budget=80); bb = b.run()
        self.assertEqual(ba.phenotype_key, bb.phenotype_key); self.assertEqual(ba.fitness, bb.fitness)
        self.assertEqual(a.primary_evaluations, b.primary_evaluations)


if __name__ == "__main__": unittest.main()
