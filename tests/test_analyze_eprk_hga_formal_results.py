from __future__ import annotations

import unittest

import analyze_eprk_hga_formal_results as analysis


class FormalAnalysisPrimitiveTests(unittest.TestCase):
    def test_01_relative_gap_negative_favors_eprk(self):
        self.assertAlmostEqual(-10.0, analysis.relative_gap(9.0, 10.0))

    def test_02_relative_gap_positive_favors_hga(self):
        self.assertAlmostEqual(10.0, analysis.relative_gap(11.0, 10.0))

    def test_03_wilcoxon_effect_positive_for_eprk_improvements(self):
        result = analysis.wilcoxon_signed_rank([1.0, 2.0, 3.0, 4.0])
        self.assertEqual(1.0, result["rank_biserial"])
        self.assertEqual(0.0, result["w_minus"])

    def test_04_wilcoxon_zero_policy_is_wilcox(self):
        result = analysis.wilcoxon_signed_rank([0.0, 1.0, -1.0])
        self.assertEqual(1, result["zero_count"])
        self.assertEqual(2, result["n_nonzero"])

    def test_05_all_zero_wilcoxon_is_nonsignificant(self):
        result = analysis.wilcoxon_signed_rank([0.0] * 30)
        self.assertEqual(1.0, result["p_two_sided"])
        self.assertEqual(0.0, result["rank_biserial"])

    def test_06_holm_is_monotone_in_sorted_order(self):
        raw = [0.01, 0.04, 0.03]
        adjusted = analysis.holm_adjust(raw)
        ordered = [adjusted[index] for index in sorted(range(3), key=raw.__getitem__)]
        self.assertEqual(ordered, sorted(ordered))
        self.assertTrue(all(value >= raw[index] for index, value in enumerate(adjusted)))

    def test_07_bootstrap_is_deterministic(self):
        first = analysis.bootstrap_median_ci([1.0, 2.0, 3.0], 123)
        second = analysis.bootstrap_median_ci([1.0, 2.0, 3.0], 123)
        self.assertEqual(first, second)

    def test_08_tie_tolerance_classification(self):
        self.assertEqual("tie", analysis.classify_gap(analysis.TIE_TOLERANCE / 2))
        self.assertEqual("win", analysis.classify_gap(-1e-6))
        self.assertEqual("loss", analysis.classify_gap(1e-6))


if __name__ == "__main__":
    unittest.main()
