import hashlib
import math
import unittest
from pathlib import Path

from atomic_problem_core import load_atomic_instance
from atomic_weld_preprocessor import preprocess_welds
from generate_welds import Weld


class AtomicWeldPreprocessorTests(unittest.TestCase):
    def test_exact_y6_then_long_split_and_parent_map(self):
        crossing = Weld("cross", 0, 5, .1, 2, 7, .1)
        atoms = preprocess_welds([crossing], "src")
        self.assertEqual(len(atoms), 2)
        self.assertTrue(all(a.is_y6_split for a in atoms))
        self.assertTrue(all(not (min(a.start[1], a.end[1]) < 6 < max(a.start[1], a.end[1])) for a in atoms))
        self.assertEqual({a.parent_original_id for a in atoms}, {"cross"})

    def test_6_6_horizontal_becomes_six_segments_of_1_1(self):
        atoms = preprocess_welds([Weld("long", 1, 8, .1, 7.6, 8, .1)], "src")
        self.assertEqual(len(atoms), 6)
        self.assertTrue(all(math.isclose(a.horizontal_length_m, 1.1, abs_tol=1e-10) for a in atoms))
        self.assertTrue(all(a.is_long_weld_split for a in atoms))

    def test_preprocessing_is_deterministic_and_saved_instances_validate(self):
        path = Path("data/instances/atomic_l5_l1/instance_w30_atomic.json")
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        first = load_atomic_instance(path); second = load_atomic_instance(path)
        self.assertEqual(first.atomic_instance_hash, second.atomic_instance_hash)
        self.assertEqual(before, hashlib.sha256(path.read_bytes()).hexdigest())


if __name__ == "__main__": unittest.main()
