import unittest

from tiny_atomic_exact_solver import build_tiny_cases, solve_exact


class EPRKTinyExactTests(unittest.TestCase):
    def test_five_cases_cover_four_to_eight_welds_and_are_reproducible(self):
        cases = build_tiny_cases(); self.assertEqual(len(cases), 5)
        self.assertEqual(sorted(len(x.atomic_welds) for x in cases.values()), [4, 5, 6, 7, 8])
        for instance in cases.values():
            a = solve_exact(instance); b = solve_exact(instance)
            self.assertAlmostEqual(a["fitness"], b["fitness"], places=12)
            self.assertGreater(a["candidate_count"], 0)


if __name__ == "__main__": unittest.main()
