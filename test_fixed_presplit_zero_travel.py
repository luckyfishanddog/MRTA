import math
import unittest

import mrta_problem_core as core
from generate_welds import Weld
from MRTA_ABMA_FIXED_PRESPLIT import FAST_VARIANT, REFERENCE_VARIANT, FixedCacheContext, FixedRouteEvaluator


class FixedZeroTravelTests(unittest.TestCase):
    def evaluate(self, variant):
        model = core.MotionModel(); context = FixedCacheContext(variant, model)
        welds = [Weld("a", 0, 8, .1, 1, 8, .1), Weld("b", 1, 8, .1, 2, 8, .1)]
        FixedRouteEvaluator.active_context = context
        try:
            evaluator = FixedRouteEvaluator(welds, model)
            result = evaluator.evaluate([0, 1]); evaluator.evaluate([0, 1])
            return result, context.snapshot()
        finally:
            FixedRouteEvaluator.active_context = None

    def test_connected_is_exact_zero(self):
        (cost, stats), diagnostics = self.evaluate(REFERENCE_VARIANT)
        self.assertEqual(stats["total_travel_time"], 0.0); self.assertEqual(stats["total_idle_distance"], 0.0)
        self.assertEqual(stats["selected_zero_connected_transition_count"], 1)
        self.assertTrue(math.isclose(cost, 2 / .0108)); self.assertGreater(diagnostics["zero_connected_transition_old_nonzero_count"], 0)

    def test_fast_cache_is_exact_and_hits(self):
        reference = self.evaluate(REFERENCE_VARIANT); fast = self.evaluate(FAST_VARIANT)
        self.assertEqual(reference[0], fast[0])
        self.assertGreater(fast[1]["pair_cache_size"], 0)


if __name__ == "__main__": unittest.main()
