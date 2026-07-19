import copy
import importlib
import math
import random
import unittest
from pathlib import Path

from generate_welds import load_frozen_weld_instance
from mrta_problem_core import MotionModel
from objective_normalization import (
    BASELINE_ALGORITHM, OFFICIAL_MODE, NormalizationSpec,
    build_deterministic_baseline, compute_normalization_spec,
    compute_theoretical_ideal_point, normalized_components,
    normalized_objective, normalization_spec_hash, validate_normalization_spec,
)


ROOT = Path(__file__).resolve().parents[1]


class ObjectiveNormalizationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.welds, cls.metadata = load_frozen_weld_instance(
            str(ROOT / "data/instances/selected/instance_w30.xlsx")
        )
        cls.model = MotionModel()
        cls.spec = compute_normalization_spec(
            cls.welds, cls.model, cls.metadata["instance_hash"]
        )

    def test_01_public_module_has_no_solver_import(self):
        source = (ROOT / "src/objective_normalization.py").read_text(encoding="utf-8")
        self.assertNotIn("import MRTA_", source)

    def test_02_theoretical_ideal(self):
        ideal = compute_theoretical_ideal_point(self.welds, self.model)
        expected = sum(w.length for w in self.welds) / self.model.weld_speed / 4.0
        self.assertAlmostEqual(ideal["makespan"], expected, places=10)
        self.assertEqual(ideal["load_imbalance"], 0.0)
        self.assertEqual(ideal["idle_distance"], 0.0)

    def test_03_baseline_algorithm_and_feasibility(self):
        baseline = self.spec.baseline
        self.assertEqual(baseline["algorithm"], BASELINE_ALGORITHM)
        self.assertEqual(baseline["assignment_stats"]["unassigned_subweld_count"], 0)
        self.assertEqual(len(baseline["routes"]), 4)
        self.assertEqual(len(baseline["direction_flags"]), 4)

    def test_04_input_order_insensitive(self):
        shuffled = list(self.welds)
        random.Random(9182).shuffle(shuffled)
        other = build_deterministic_baseline(shuffled, self.model, self.metadata["instance_hash"])
        self.assertEqual(other.baseline_hash, self.spec.baseline["baseline_hash"])

    def test_05_repeatability(self):
        other = build_deterministic_baseline(self.welds, self.model, self.metadata["instance_hash"])
        self.assertEqual(other.to_dict(), self.spec.baseline)

    def test_06_positive_finite_scales(self):
        self.assertTrue(all(math.isfinite(v) and v > 0 for v in self.spec.scale.values()))

    def test_07_weights_nonnegative_sum_one(self):
        validate_normalization_spec(self.spec)
        self.assertAlmostEqual(sum(self.spec.weights), 1.0)
        self.assertTrue(all(value >= 0 for value in self.spec.weights))

    def test_08_ideal_maps_to_zero(self):
        self.assertEqual(normalized_components(self.spec.ideal, self.spec), {
            "makespan": 0.0, "load_imbalance": 0.0, "idle_distance": 0.0,
        })

    def test_09_baseline_maps_by_declared_scale(self):
        components = normalized_components(self.spec.baseline["metrics"], self.spec)
        for key in components:
            expected = (self.spec.baseline["metrics"][key] - self.spec.ideal[key]) / self.spec.scale[key]
            self.assertAlmostEqual(components[key], expected)

    def test_10_no_upper_clipping(self):
        raw = {key: self.spec.ideal[key] + 2.5 * self.spec.scale[key] for key in self.spec.scale}
        self.assertTrue(all(value > 1.0 for value in normalized_components(raw, self.spec).values()))

    def test_11_tiny_negative_tolerance_zeroed(self):
        raw = dict(self.spec.ideal)
        raw["makespan"] -= 1e-11
        self.assertEqual(normalized_components(raw, self.spec)["makespan"], 0.0)

    def test_12_clearly_below_ideal_rejected(self):
        raw = dict(self.spec.ideal)
        raw["makespan"] -= 1.0
        with self.assertRaises(ValueError):
            normalized_components(raw, self.spec)

    def test_13_invalid_scale_rejected(self):
        bad = self.spec.to_dict(); bad["scale"]["makespan"] = 0.0
        with self.assertRaises(ValueError):
            validate_normalization_spec(bad)

    def test_14_hash_detects_tampering(self):
        original = normalization_spec_hash(self.spec)
        bad = self.spec.to_dict(); bad["scale"]["idle_distance"] *= 1.01
        self.assertNotEqual(original, normalization_spec_hash(bad))

    def test_15_weighted_objective_matches_components(self):
        raw = self.spec.baseline["metrics"]
        components = normalized_components(raw, self.spec)
        expected = sum(w * components[k] for w, k in zip(self.spec.weights, ("makespan", "load_imbalance", "idle_distance")))
        self.assertAlmostEqual(normalized_objective(raw, self.spec), expected)


class FourSolverObjectiveConsistencyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        welds, metadata = load_frozen_weld_instance(str(ROOT / "data/instances/selected/instance_w30.xlsx"))
        cls.spec = compute_normalization_spec(welds, MotionModel(), metadata["instance_hash"]).to_dict()
        cls.raw = cls.spec["baseline"]["metrics"]
        cls.expected = normalized_objective(cls.raw, cls.spec)

    def test_ga_and_de_shared_path(self):
        import MRTA_GA_ACO as ga
        ga.OBJECTIVE_NORMALIZATION_MODE = OFFICIAL_MODE
        ga.OBJECTIVE_NORMALIZATION_SPEC = self.spec
        self.assertAlmostEqual(ga.weighted_objective(self.raw["makespan"], self.raw["load_imbalance"], self.raw["idle_distance"], 1, 1, 1), self.expected)

    def test_hga_shared_path(self):
        import MRTA_HGA_PAPER_ALIGNED_CONTROL as hga
        refs = hga.ObjectiveRefs(1, 1, 1, self.spec)
        self.assertAlmostEqual(hga.objective(self.raw["makespan"], self.raw["load_imbalance"], self.raw["idle_distance"], refs), self.expected)

    def test_abma_shared_path(self):
        import MRTA_ABMA as abma
        config = abma.ABMAConfig(normalization_mode=OFFICIAL_MODE, normalization_spec=self.spec)
        route_stats = [
            {"total_time": self.raw["makespan"], "total_idle_distance": self.raw["idle_distance"]},
            {"total_time": self.raw["makespan"] - self.raw["load_imbalance"], "total_idle_distance": 0.0},
            {"total_time": self.raw["makespan"], "total_idle_distance": 0.0},
            {"total_time": self.raw["makespan"], "total_idle_distance": 0.0},
        ]
        self.assertAlmostEqual(abma._system_fitness(route_stats, config, (1, 1, 1))[0], self.expected)


if __name__ == "__main__":
    unittest.main()
