import unittest

import mrta_problem_core as core
from generate_welds import Weld
from MRTA_ABMA_FIXED_PRESPLIT import FAST_VARIANT, REFERENCE_VARIANT, FixedCacheContext, FixedRouteEvaluator


class FixedABMAEquivalenceTests(unittest.TestCase):
    def test_reference_fast_route_equivalence(self):
        welds = [Weld("a", 0, 8, .1, 1, 8, .1), Weld("b", 1, 8, .1, 2, 8, .1), Weld("c", 4, 8, .1, 3, 8, .1)]
        model = core.MotionModel(); results = []
        for variant in (REFERENCE_VARIANT, FAST_VARIANT):
            context = FixedCacheContext(variant, model); FixedRouteEvaluator.active_context = context
            try:
                evaluator = FixedRouteEvaluator(welds, model)
                results.append([evaluator.evaluate(order) for order in ([0, 1, 2], [2, 1, 0], [0, 2, 1])])
            finally:
                FixedRouteEvaluator.active_context = None
        self.assertEqual(results[0], results[1])


if __name__ == "__main__": unittest.main()
