import copy
import hashlib
import json
import math
import tempfile
import unittest
from pathlib import Path

from legacy_normalization_compatibility import (
    ABSOLUTE_TOLERANCE,
    ALLOWED_SCOPE,
    RELATIVE_TOLERANCE,
    classify_pair,
    compare_values,
    validate_waiver_payload,
    verify_waiver_file,
)


SCOPE = "UNIFIED_EXPERIMENT_PROTOCOL.json#/instances/w30/normalization"


def spec():
    return {
        "mode": "ideal_baseline_range_v1",
        "ideal": {"makespan": 100.0, "load_imbalance": 0.0, "idle_distance": 0.0},
        "baseline": {
            "algorithm": "workload_balanced_cut_cheapest_insertion_2opt_dp_v1",
            "algorithm_version": "1.0.0",
            "instance_hash": "a" * 64,
            "scientific_model_hash": "b" * 64,
            "baseline_hash": "c" * 64,
            "metrics": {"makespan": 120.0, "load_imbalance": 5.0, "idle_distance": 2.0},
            "routes": [["a", "b"], [], [], []],
        },
        "scale": {"makespan": 20.0, "load_imbalance": 5.0, "idle_distance": 2.0},
        "floors": {"epsilon_makespan": 1e-7, "load_imbalance": 5.0, "epsilon_idle_distance": 1e-8},
        "weights": [0.7, 0.2, 0.1],
    }


def classify(left, right, **overrides):
    kwargs = dict(
        scope=SCOPE,
        instance_hash_match=True,
        algorithm_match=True,
        weights_match=True,
        baseline_policy_match=True,
        load_floor_policy_match=True,
        historical_metric_replay_match=True,
    )
    kwargs.update(overrides)
    return classify_pair(left, right, **kwargs)


def waiver_payload():
    return {
        "waiver_version": "1.0.0",
        "status": "approved_for_legacy_validation_only",
        "exact_hash_match": False,
        "compatibility_match": True,
        "scope": sorted(ALLOWED_SCOPE),
        "absolute_tolerance": ABSOLUTE_TOLERANCE,
        "relative_tolerance": RELATIVE_TOLERANCE,
        "protected_files_modified": False,
        "instances": [
            {"instance": key, "status": "legacy_float_compatibility_pass"}
            for key in ("w30", "w45", "w60")
        ],
    }


class LegacyNormalizationCompatibilityTests(unittest.TestCase):
    def test_01_small_numeric_drift_passes_compatibility(self):
        left, right = spec(), spec(); right["scale"]["makespan"] += 1e-13
        self.assertEqual(classify(left, right)["status"], "legacy_float_compatibility_pass")

    def test_02_two_e_minus_twelve_fails(self):
        left, right = spec(), spec(); right["scale"]["makespan"] += 2e-12
        self.assertEqual(classify(left, right)["status"], "failure")

    def test_03_instance_hash_mismatch_fails(self):
        left, right = spec(), spec(); right["scale"]["makespan"] += 1e-13
        self.assertEqual(classify(left, right, instance_hash_match=False)["status"], "failure")

    def test_04_weights_mismatch_fails(self):
        left, right = spec(), spec(); right["weights"] = [0.6, 0.3, 0.1]
        self.assertEqual(classify(left, right, weights_match=False)["status"], "failure")

    def test_05_baseline_policy_mismatch_fails(self):
        left, right = spec(), spec(); right["baseline"]["algorithm"] = "other"
        self.assertEqual(classify(left, right, baseline_policy_match=False)["status"], "failure")

    def test_06_load_floor_policy_mismatch_fails(self):
        left, right = spec(), spec(); right["floors"]["load_imbalance"] = 4.0
        self.assertEqual(classify(left, right, load_floor_policy_match=False)["status"], "failure")

    def test_07_missing_field_fails(self):
        left, right = spec(), spec(); del right["scale"]["idle_distance"]
        self.assertEqual(classify(left, right)["status"], "failure")

    def test_08_extra_field_fails(self):
        left, right = spec(), spec(); right["extra"] = 1
        self.assertEqual(classify(left, right)["status"], "failure")

    def test_09_non_numeric_change_fails(self):
        left, right = spec(), spec(); right["mode"] = "other"
        self.assertEqual(classify(left, right)["status"], "failure")

    def test_10_nan_and_inf_fail(self):
        for value in (math.nan, math.inf, -math.inf):
            left, right = spec(), spec(); right["scale"]["makespan"] = value
            self.assertEqual(classify(left, right)["status"], "failure")

    def test_11_unlisted_scope_cannot_be_waived(self):
        left, right = spec(), spec(); right["scale"]["makespan"] += 1e-13
        self.assertEqual(classify(left, right, scope="other.json#/normalization")["status"], "failure")

    def test_12_atomic_normalization_cannot_be_waived(self):
        left, right = spec(), spec(); right["scale"]["makespan"] += 1e-13
        path = "formal_atomic/instances/normalization/real_w30.json"
        self.assertEqual(classify(left, right, scope=path)["status"], "failure")

    def test_13_algorithm_result_cannot_be_waived(self):
        left, right = spec(), spec(); right["scale"]["makespan"] += 1e-13
        self.assertEqual(classify(left, right, scope="formal_atomic/runs/x/result.json")["status"], "failure")

    def test_14_modified_waiver_payload_fails(self):
        value = waiver_payload(); value["status"] = "modified"
        self.assertTrue(validate_waiver_payload(value))

    def test_15_waiver_hash_mismatch_fails(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); path = root / "w.json"; hash_path = root / "w.sha256"
            path.write_text(json.dumps(waiver_payload()), encoding="utf-8")
            hash_path.write_text("0" * 64, encoding="ascii")
            _, errors = verify_waiver_file(path, hash_path)
            self.assertIn("waiver hash mismatch", errors)

    def test_16_protected_file_change_is_not_waivable(self):
        value = waiver_payload(); value["protected_files_modified"] = True
        self.assertIn("waiver does not affirm protected_files_modified=false", validate_waiver_payload(value))

    def test_17_preflight_statuses_are_distinct(self):
        value = spec()
        self.assertEqual(classify(value, copy.deepcopy(value))["status"], "exact_hash_pass")
        drift = copy.deepcopy(value); drift["scale"]["makespan"] += 1e-13
        self.assertEqual(classify(value, drift)["status"], "legacy_float_compatibility_pass")
        bad = copy.deepcopy(value); bad["scale"]["makespan"] += 2e-12
        self.assertEqual(classify(value, bad)["status"], "failure")

    def test_18_compatibility_check_does_not_mutate_inputs(self):
        left, right = spec(), spec(); right["scale"]["makespan"] += 1e-13
        before_left, before_right = copy.deepcopy(left), copy.deepcopy(right)
        classify(left, right)
        self.assertEqual((left, right), (before_left, before_right))

    def test_19_repeat_comparison_is_deterministic(self):
        left, right = spec(), spec(); right["scale"]["makespan"] += 1e-13
        self.assertEqual(classify(left, right), classify(left, right))

    def test_20_newline_difference_is_not_exact_pass(self):
        left = spec(); right = copy.deepcopy(left)
        result = classify(left, right, exact_hash_match=False)
        self.assertEqual(result["status"], "legacy_float_compatibility_pass")
        self.assertFalse(result["exact_match"])

    def test_21_derived_baseline_hash_is_audited_separately(self):
        left, right = spec(), spec(); right["scale"]["makespan"] += 1e-13
        right["baseline"]["baseline_hash"] = "d" * 64
        result = classify(left, right)
        self.assertEqual(result["status"], "legacy_float_compatibility_pass")
        self.assertEqual(result["derived_integrity_differences"][0]["path"], "$/baseline/baseline_hash")


if __name__ == "__main__":
    unittest.main()
