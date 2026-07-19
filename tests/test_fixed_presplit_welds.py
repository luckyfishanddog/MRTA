import math
import unittest

from generate_welds import Weld
from weld_fixed_presplit import LMAX_M, presplit_welds


def weld(name, x1, y1, x2, y2):
    return Weld(name, x1, y1, 0.1, x2, y2, 0.1)


class FixedPreSplitWeldTests(unittest.TestCase):
    def test_long_rule_and_exact_limit(self):
        fixed = presplit_welds([weld("long", 0, 8, 6.6, 8), weld("limit", 10, 8, 15, 8)], "source")
        long_children = [w for w in fixed if w.parent_weld_id == "long"]
        limit_children = [w for w in fixed if w.parent_weld_id == "limit"]
        self.assertEqual(len(long_children), 6)
        self.assertTrue(all(math.isclose(w.horizontal_span_m, 1.1, abs_tol=1e-12) for w in long_children))
        self.assertEqual((len(limit_children), limit_children[0].horizontal_span_m), (1, LMAX_M))

    def test_y_first_and_parent_conservation(self):
        originals = [weld("cross-y", 1, 5, 3, 7), weld("diagonal-long", 0, 4, 12, 8)]
        fixed = presplit_welds(originals, "source")
        for original in originals:
            children = [w for w in fixed if w.parent_weld_id == original.id]
            self.assertTrue(math.isclose(sum(w.length for w in children), original.length, abs_tol=1e-9))
            self.assertTrue(math.isclose(sum(w.weld_time_s for w in children), original.length / 0.0108, abs_tol=1e-7))
        self.assertEqual({w.half_id for w in fixed if w.parent_weld_id == "cross-y"}, {"upper", "lower"})

    def test_stable_ids_ignore_orientation(self):
        forward = presplit_welds([weld("a", 0, 8, 6.6, 8)], "same")
        reverse = presplit_welds([weld("a", 6.6, 8, 0, 8)], "same")
        self.assertEqual([w.id for w in forward], [w.id for w in reverse])
        self.assertEqual(len({w.id for w in forward}), len(forward))


if __name__ == "__main__": unittest.main()
