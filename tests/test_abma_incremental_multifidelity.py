import csv
import tempfile
import unittest
from pathlib import Path

from generate_welds import Weld, load_frozen_weld_instance
from MRTA_ABMA import (
    ABMAConfig, ABMASolver, AdaptivePromotionPolicy, PromotionFeatures,
    _canonical_geometry, save_metrics_csv,
)


INSTANCE = r"data\instances\selected\instance_w30.xlsx"


class ABMAIncrementalMultiFidelityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.welds, _ = load_frozen_weld_instance(INSTANCE)

    def config(self, **updates):
        values = dict(
            seed=42, population_size=4, generations=1,
            max_objective_evaluations=8, verbose=False,
            abma_variant="incremental_multifidelity",
            low_alns_iterations=1, mid_alns_iterations=2,
            high_alns_iterations=3, low_vnd_iterations=0,
            mid_vnd_iterations=0, high_vnd_iterations=0,
            rejected_audit_interval=2,
        )
        values.update(updates)
        return ABMAConfig(**values)

    def test_geometry_identity_does_not_depend_on_segment_label(self):
        a = Weld("parent_seg0", 1, 2, .1, 3, 4, .1, parent_id="parent")
        b = Weld("parent_seg9", 1, 2, .1, 3, 4, .1, parent_id="parent")
        self.assertEqual(_canonical_geometry(a, 1e-8), _canonical_geometry(b, 1e-8))

    def test_only_changed_half_is_marked_affected(self):
        solver = ABMASolver(self.welds, self.config(max_objective_evaluations=4))
        solver._initialize_references()
        parent = solver._raw_evaluate(10.0, 10.0, required_fidelity="high")
        child = solver._raw_evaluate(10.25, 10.0, parent=parent, required_fidelity="low")
        self.assertEqual(child.repair_diagnostics["affected_robots"], [0, 1])
        self.assertEqual(child.repair_diagnostics["unchanged_half_reuse_count"], 2)

    def test_fidelity_resume_is_monotone_and_high_certified(self):
        solver = ABMASolver(self.welds, self.config(max_objective_evaluations=4))
        solver._initialize_references()
        low = solver._raw_evaluate(9.7, 10.3, required_fidelity="low")
        low_value = low.fitness
        robots, _ = solver._assign_partition(low.x_up, low.x_low)
        mid = solver._promote_solution(low, robots, "mid")
        mid_value = mid.fitness
        high = solver._promote_solution(mid, robots, "high")
        self.assertLessEqual(mid_value, low_value + 1e-12)
        self.assertLessEqual(high.fitness, mid_value + 1e-12)
        self.assertTrue(high.high_certified)
        self.assertEqual(high.inner_state.alns_completed, 3)

    def test_population_and_final_best_are_high_certified(self):
        result = ABMASolver(self.welds, self.config()).solve()
        self.assertTrue(result.best.high_certified)
        self.assertTrue(result.diagnostics["high_certification_invariant"])
        self.assertEqual(result.diagnostics["objective_evaluation_count"], 8)

    def test_deterministic_replay(self):
        def run():
            result = ABMASolver(self.welds, self.config()).solve()
            return result.best.fitness, result.best.robot_order_ids, result.diagnostics["fidelity_evaluation_counts"]
        self.assertEqual(run(), run())

    def test_audited_quantile_and_warmup_are_deterministic(self):
        config = self.config(minimum_audit_samples=2, audit_quantile=0.95)
        policy = AdaptivePromotionPolicy(config)
        features = PromotionFeatures(.1, .5, 1.0, 0)
        self.assertFalse(policy.decide("low", 2.0, 1.0, False, features).reject)
        policy.record(1.2, 1.1, 1.0, features)
        policy.record(1.3, 1.15, 1.0, features)
        self.assertAlmostEqual(policy.q_low, .3)
        self.assertAlmostEqual(policy.q_mid, .15)
        first = policy.decide("low", 1.5, 1.0, False, features)
        second = policy.decide("low", 1.5, 1.0, False, features)
        self.assertEqual(first, second)
        self.assertTrue(first.reject)

    def test_gray_zone_forces_promotion(self):
        config = self.config(minimum_audit_samples=1)
        policy = AdaptivePromotionPolicy(config)
        normal = PromotionFeatures(.1, .5, 1.0, 0)
        policy.record(1.2, 1.1, 1.0, normal)
        near = policy.decide("mid", 1.005, 1.0, False, normal)
        self.assertFalse(near.reject)
        self.assertIn("near_target", near.gray_zone_reasons)
        low_diversity = PromotionFeatures(.1, .5, 1.0, 0, 0.001)
        diversity_decision = policy.decide("low", 2.0, 1.0, False, low_diversity)
        self.assertFalse(diversity_decision.reject)
        self.assertIn("low_population_diversity", diversity_decision.gray_zone_reasons)

    def test_outer_rng_is_unchanged_by_inner_promotion_depth(self):
        solver = ABMASolver(self.welds, self.config(max_objective_evaluations=4))
        solver._initialize_references()
        solution = solver._raw_evaluate(9.8, 10.2, required_fidelity="low")
        state_before = solver.rng.getstate()
        robots, _ = solver._assign_partition(solution.x_up, solution.x_low)
        solver._promote_solution(solution, robots, "high")
        self.assertEqual(state_before, solver.rng.getstate())

    def test_csv_compacts_full_adaptive_audit_trace(self):
        records = [{"low": 1.0, "mid": .9, "high": .8}] * 10000
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "metrics.csv"
            save_metrics_csv(
                {"fitness": .8, "adaptive_promotion": {"records": records, "q_low": .2}},
                str(path),
            )
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                row = next(csv.DictReader(handle))
            self.assertNotIn('"records"', row["adaptive_promotion"])
            self.assertIn('"record_count":10000', row["adaptive_promotion"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
