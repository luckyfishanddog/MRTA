import json
import unittest
from pathlib import Path

from atomic_problem_core import load_atomic_instance
from MRTA_EPRK_MA import EPRKMASolver


class EPRKDecoderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.instance = load_atomic_instance("data/instances/atomic_l5_l1/instance_w30_atomic.json")
        cls.spec = json.loads(Path("atomic_normalization_spec.json").read_text(encoding="utf-8"))["instances"]["w30"]

    def test_fixed_length_floor_mapping_and_complete_decode(self):
        solver = EPRKMASolver(self.instance, self.spec, 101, primary_budget=1)
        genes = [0.999999999, 0.0] + [0.5] * len(self.instance.atomic_welds)
        up, low, routes, _ = solver.decode(genes)
        self.assertEqual(up, len(self.instance.upper_events) - 1); self.assertEqual(low, 0)
        flattened = [x for route in routes for x in route]
        self.assertEqual(len(flattened), len(set(flattened)))
        self.assertEqual(set(flattened), set(solver.evaluator.ids))

    def test_wrong_chromosome_length_fails(self):
        solver = EPRKMASolver(self.instance, self.spec, 101, primary_budget=1)
        with self.assertRaises(ValueError): solver.decode([0.1, 0.2])


if __name__ == "__main__": unittest.main()
