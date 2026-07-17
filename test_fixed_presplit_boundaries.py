import unittest

from generate_welds import Weld
from weld_fixed_presplit import assign_fixed_welds, build_boundary_candidates, presplit_welds


def weld(name, x1, y1, x2, y2):
    return Weld(name, x1, y1, 0.1, x2, y2, 0.1)


class FixedBoundaryTests(unittest.TestCase):
    def test_candidates_exclude_cuts_and_empty_sides(self):
        fixed = presplit_welds([weld("left", 1, 8, 2, 8), weld("right", 8, 8, 9, 8)], "s")
        values = [item["x"] for item in build_boundary_candidates(fixed, "upper")["candidates"]]
        self.assertTrue(values); self.assertNotIn(0.0, values); self.assertNotIn(20.0, values)
        self.assertTrue(all(not (1 < x < 2 or 8 < x < 9) for x in values))

    def test_complete_unique_assignment_and_left_priority(self):
        fixed = presplit_welds([
            weld("ul", 1, 8, 2, 8), weld("ur", 8, 8, 9, 8),
            weld("ll", 1, 2, 2, 2), weld("lr", 8, 2, 9, 2),
            weld("vertical", 5, 7, 5, 9),
        ], "s")
        robots, stats = assign_fixed_welds(fixed, 5, 5)
        ids = [w.id for route in robots for w in route]
        self.assertEqual(len(ids), len(fixed)); self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(stats["unassigned_subweld_count"], 0)
        self.assertIn(next(w for w in fixed if w.parent_weld_id == "vertical"), robots[0])
        with self.assertRaisesRegex(ValueError, "cuts fixed weld"):
            assign_fixed_welds(fixed, 1.5, 5)

    def test_no_valid_internal_boundary_fails(self):
        fixed = presplit_welds([weld("only", 10, 8, 10, 9)], "s")
        with self.assertRaisesRegex(ValueError, "no valid internal boundary"):
            build_boundary_candidates(fixed, "upper")


if __name__ == "__main__": unittest.main()
