import json
import unittest
from pathlib import Path

from atomic_problem_core import load_atomic_instance
from MRTA_EPRK_MA import EPRKMASolver


class EPRKRandomKeyTests(unittest.TestCase):
    def setUp(self):
        instance = load_atomic_instance("data/instances/atomic_l5_l1/instance_w30_atomic.json")
        spec = json.loads(Path("atomic_normalization_spec.json").read_text(encoding="utf-8"))["instances"]["w30"]
        self.solver = EPRKMASolver(instance, spec, 101, primary_budget=10)

    def test_equal_keys_tie_break_by_atomic_id(self):
        genes = [0.4, 0.4] + [0.5] * len(self.solver.evaluator.ids)
        routes = self.solver.decode(genes)[2]
        self.assertTrue(all(list(route) == sorted(route) for route in routes))

    def test_phenotype_key_ignores_float_values_that_keep_order(self):
        a = [0.4, 0.4] + [i / 1000 for i in range(len(self.solver.evaluator.ids))]
        b = [0.41, 0.41] + [0.2 + i / 1000 for i in range(len(self.solver.evaluator.ids))]
        self.assertEqual(self.solver.decode(a)[3], self.solver.decode(b)[3])

    def test_route_reencoding_changes_only_selected_ids(self):
        genes = self.solver._random_genes(); before = genes[:]
        route = list(self.solver.decode(genes)[2][0]); self.solver._encode_route(genes, list(reversed(route)))
        changed = {self.solver.evaluator.ids[i] for i in range(len(self.solver.evaluator.ids)) if genes[i+2] != before[i+2]}
        self.assertTrue(changed.issubset(set(route)))


if __name__ == "__main__": unittest.main()
