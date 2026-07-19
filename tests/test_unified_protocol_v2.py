import copy
import tempfile
import json
import unittest
from pathlib import Path

import run_unified_experiments as runner
from objective_normalization import LEGACY_MODE, OFFICIAL_MODE
from project_paths import resolve_project_path


ROOT = Path(__file__).resolve().parents[1]


class UnifiedProtocolV2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.path = resolve_project_path("UNIFIED_EXPERIMENT_PROTOCOL.json")
        cls.protocol = runner.read_json(cls.path)

    def test_protocol_exact_validator_reports_frozen_float_drift(self):
        with tempfile.TemporaryDirectory() as folder:
            errors = runner.validate_protocol(self.protocol, self.path, Path(folder) / "out")
        self.assertEqual(errors, [
            "w30: normalization spec hash mismatch",
            "w30: normalization values or baseline evidence mismatch",
            "w45: normalization spec hash mismatch",
            "w45: normalization values or baseline evidence mismatch",
            "w60: normalization spec hash mismatch",
            "w60: normalization values or baseline evidence mismatch",
        ])

    def test_official_commands_have_ranges_and_no_refs(self):
        instance = self.protocol["instances"]["w30"]
        for solver in self.protocol["official_solvers"]:
            spec = runner.RunSpec("smoke", solver, "w30", instance, 42,
                                  self.protocol["budget"]["smoke_solver_controls"][solver]["max_objective_evaluations"],
                                  self.protocol["budget"]["smoke_solver_controls"][solver])
            command = runner.ADAPTERS[solver].build(self.protocol, spec, "python", ROOT / "unused")
            self.assertIn(OFFICIAL_MODE, command)
            for flag in ("--ideal-makespan", "--baseline-makespan", "--scale-makespan"):
                self.assertIn(flag, command)
            for flag in ("--ref-makespan", "--ref-load", "--ref-distance", "--reference-makespan"):
                self.assertNotIn(flag, command)

    def test_tampered_scale_and_hash_rejected(self):
        bad = copy.deepcopy(self.protocol)
        bad["instances"]["w30"]["normalization"]["scale"]["makespan"] *= 1.01
        with tempfile.TemporaryDirectory() as folder:
            errors = runner.validate_protocol(bad, self.path, Path(folder) / "out")
        self.assertTrue(any("normalization" in error and "mismatch" in error for error in errors))

    def test_old_protocol_version_excluded(self):
        bad = copy.deepcopy(self.protocol); bad["protocol_version"] = "1.0.0"
        with tempfile.TemporaryDirectory() as folder:
            errors = runner.validate_protocol(bad, self.path, Path(folder) / "out")
        self.assertIn("protocol_version must be 2.6.0", errors)

    def test_historical_stage_states_are_snapshots_not_current_authority(self):
        snapshots = self.protocol["historical_stage_snapshots"]
        self.assertEqual(set(snapshots), {"development", "independent_validation", "w30_budget_pilot"})
        self.assertTrue(all(item.get("recorded_at_stage_end") for item in snapshots.values()))
        for old_key in ("abma_development_outcome", "abma_profile_profiling_evidence",
                        "abma_development_gate_evidence", "abma_final_profile_status",
                        "abma_independent_validation", "three_solver_w30_preflight"):
            self.assertNotIn(old_key, self.protocol)
        self.assertEqual(
            self.protocol["previous_protocol"]["sha256"],
            "f6a1d11957e19f4f55eb61f8e006313ce04bca0d64852b29b25417af1e7ed4f8",
        )

    def test_official_solver_set_excludes_de_lkh(self):
        self.assertEqual(self.protocol["official_solvers"], ["ga_aco", "paper_aligned_hga", "abma"])
        self.assertEqual(set(runner.ADAPTERS), {"ga_aco", "paper_aligned_hga", "abma"})
        excluded = self.protocol["historical_excluded_solvers"]["de_lkh"]
        self.assertFalse(excluded["eligible_for_official_summary"])
        self.assertTrue(excluded["source_retained"])
        self.assertNotIn("de_lkh", self.protocol["budget"]["formal_solver_controls"])

    def test_frozen_abma_profile_and_official_command(self):
        expected = "631b2d675e7531d04a3c019cc8013158223944074da3e42d489a5745addee1d3"
        self.assertEqual(runner.validate_frozen_abma_profile(self.protocol), [])
        self.assertEqual(self.protocol["abma_candidate_freeze"]["algorithm_name"],
                         "ABMA-Legacy-Exact-Fast-v1")
        self.assertEqual(self.protocol["abma_candidate_freeze"]["profile_hash"], expected)
        instance = self.protocol["instances"]["w30"]
        controls = self.protocol["budget"]["formal_solver_controls"]["abma"]
        spec = runner.RunSpec("budget_pilot", "abma", "w30", instance, 42, 16000, controls)
        command = runner.ADAPTERS["abma"].build(self.protocol, spec, "python", ROOT / "unused")
        self.assertEqual(command[command.index("--abma-variant") + 1], "legacy_exact_fast")
        for rejected_flag in ("--low-alns-iterations", "--mid-alns-iterations", "--high-alns-iterations"):
            self.assertNotIn(rejected_flag, command)

    def test_three_solver_pilot_and_formal_matrices_never_emit_de(self):
        namespace = type("Args", (), {
            "instances": ["w30"], "solvers": None, "seeds": [42],
            "benchmark_budget": 2000, "abma_benchmark_variants": None,
            "ablation_pop_size": None, "ablation_max_gen": None,
        })()
        pilot = runner.build_matrix(self.protocol, "budget_pilot_preflight", namespace)
        self.assertEqual([item.solver for item in pilot], self.protocol["official_solvers"])
        self.assertNotIn("de_lkh", {item.solver for item in pilot})
        with self.assertRaises(ValueError):
            bad = type("Args", (), {
                "instances": ["w30"], "solvers": ["de_lkh"], "seeds": [42],
                "benchmark_budget": 2000, "abma_benchmark_variants": None,
                "ablation_pop_size": None, "ablation_max_gen": None,
            })()
            runner.build_matrix(self.protocol, "formal", bad)

    def test_formal_still_fail_closed(self):
        self.assertFalse(self.protocol["formal_run_approved"])
        self.assertTrue(self.protocol["approval_gates"]["abma_validation_completed"])
        self.assertTrue(self.protocol["approval_gates"]["three_solver_cross_validation_completed"])
        self.assertTrue(self.protocol["approval_gates"]["w30_budget_pilot_completed"])
        self.assertTrue(self.protocol["approval_gates"]["scale_calibration_completed"])
        self.assertTrue(self.protocol["approval_gates"]["formal_experiment_manifest_ready"])
        self.assertFalse(self.protocol["approval_gates"]["user_approved_formal_execution"])

    def test_scale_preflight_is_three_solver_seed42_budget2000_only(self):
        namespace = type("Args", (), {
            "instances": ["w45"], "solvers": None, "seeds": [42],
            "scale_budget": None, "selected_evaluation_budget": None,
            "benchmark_budget": 2000, "abma_benchmark_variants": None,
            "ablation_pop_size": None, "ablation_max_gen": None,
        })()
        specs = runner.build_matrix(self.protocol, "scale_preflight", namespace)
        self.assertEqual([item.solver for item in specs], self.protocol["official_solvers"])
        self.assertTrue(all(item.seed == 42 and item.budget_value == 2000 for item in specs))
        self.assertEqual(specs[-1].controls["abma_variant"], "legacy_exact_fast")
        namespace.instances = ["w30"]
        with self.assertRaises(ValueError):
            runner.build_matrix(self.protocol, "scale_preflight", namespace)

    def test_ablation_remains_legacy(self):
        namespace = type("Args", (), {"instances": ["w30"], "solvers": None, "seeds": [42],
                                       "ablation_pop_size": 4, "ablation_max_gen": 1})()
        specs = runner.build_matrix(self.protocol, "gaaco_ablation", namespace)
        self.assertEqual(len(specs), 6)
        for spec in specs:
            command = runner.ADAPTERS[spec.solver].build(self.protocol, spec, "python", ROOT / "unused")
            self.assertNotIn("--normalization-mode", command)

    def test_resume_requires_matching_protocol_source_and_command(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder); attempt = base / "attempt_001"; attempt.mkdir()
            source = {"a.py": "abc"}
            runner.atomic_write_json(attempt / "run_status.json", {
                "run_status": "success", "protocol_hash": "p", "source_hashes": source,
                "command_hash": "c",
            })
            runner.atomic_write_json(attempt / "unified_metrics.json", {"ok": True})
            self.assertEqual(runner.matching_success(base, "p", source, "c"), attempt)
            self.assertIsNone(runner.matching_success(base, "old", source, "c"))

    def test_formal_summary_excludes_old_mode_and_protocol(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); target = root / "formal/x"; target.mkdir(parents=True)
            base = {"run_status": "success", "run_class": "formal", "instance_hash": "i",
                    "solver_name": "s", "solver_seed": 42, "fitness": 1.0,
                    "makespan": 1.0, "load_imbalance": 0.0, "total_idle_distance": 0.0,
                    "algorithm_time": 1.0}
            (target / "old_mode").mkdir(); (target / "old_protocol").mkdir(); (target / "current").mkdir()
            runner.atomic_write_json(target / "old_mode/unified_metrics.json", {**base,
                "normalization_mode": LEGACY_MODE, "protocol_hash": "current"})
            runner.atomic_write_json(target / "old_protocol/unified_metrics.json", {**base,
                "normalization_mode": OFFICIAL_MODE, "protocol_hash": "old"})
            runner.atomic_write_json(target / "current/unified_metrics.json", {**base,
                "normalization_mode": OFFICIAL_MODE, "protocol_hash": "current"})
            report = runner.summarize_formal(root, "current")
            self.assertEqual(report["formal_valid_run_count"], 1)


if __name__ == "__main__":
    unittest.main()
