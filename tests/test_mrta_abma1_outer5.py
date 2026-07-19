"""Exact-equivalence and identity tests for ABMA-Outer5-v1."""

from __future__ import annotations

import hashlib
import math
import subprocess
import sys
import unittest
from pathlib import Path

import MRTA_ABMA as source
import MRTA_ABMA1 as outer5


ROOT = Path(__file__).resolve().parents[1]
SOURCE_HASH = "7f6f09a030543f6e4e14d2bd837791c90896da3bb1e395f409c33d6eaef20df9"
HGA_HASH = "26701c4b24d722a539423f395b807d1128cf7d1d9a924e8b66bca2ee92f75f50"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def strip_runtime(value):
    if isinstance(value, dict):
        return {key: strip_runtime(item) for key, item in value.items()
                if key not in {"elapsed_time", "elapsed_time_s", "algorithm_time"}}
    if isinstance(value, list):
        return [strip_runtime(item) for item in value]
    return value


def tiny_welds():
    z = 0.1
    return [
        source.Weld("a", 1.0, 9.0, z, 2.0, 9.0, z),
        source.Weld("b", 4.0, 10.0, z, 5.0, 10.0, z),
        source.Weld("c", 7.0, 3.0, z, 8.0, 3.0, z),
        source.Weld("d", 12.0, 4.0, z, 13.0, 4.0, z),
        source.Weld("e", 16.0, 9.0, z, 17.0, 9.0, z),
        source.Weld("cross", 9.0, 8.0, z, 11.0, 8.0, z),
        source.Weld("cross_y", 3.0, 5.0, z, 4.0, 7.0, z),
    ]


class ABMA1Outer5Tests(unittest.TestCase):
    def configurations(self):
        common = dict(
            seed=78,
            population_size=4,
            generations=5,
            max_objective_evaluations=0,
            early_stop_patience=0,
            abma_variant="legacy_exact_fast",
            alns_iterations=2,
            vnd_iterations=1,
            max_move_checks_per_operator=8,
            verbose=False,
        )
        return source.ABMAConfig(**common), outer5.ABMA1Config(**common)

    def paired_results(self):
        source_config, wrapper_config = self.configurations()
        return (
            source.ABMASolver(tiny_welds(), source_config).solve(),
            outer5.ABMA1Solver(tiny_welds(), wrapper_config).solve(),
        )

    def test_01_default_identity_and_controlled_values(self):
        config = outer5.ABMA1Config()
        config.validate()
        self.assertEqual(config.generations, 5)
        self.assertEqual(config.population_size, 12)
        self.assertEqual(config.alns_iterations, 80)
        self.assertEqual(config.vnd_iterations, 3)
        self.assertEqual(config.max_objective_evaluations, 0)
        self.assertEqual(config.early_stop_patience, 0)
        self.assertEqual(config.abma_variant, "legacy_exact_fast")
        self.assertEqual(outer5.ALGORITHM_NAME, "ABMA-Outer5-v1")
        self.assertEqual(outer5.SOLVER_NAME, "ABMA1")
        self.assertEqual(outer5.VARIANT, "outer5_legacy_exact_fast")

    def test_02_nonfive_generation_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "generations=5"):
            outer5.ABMA1Config(generations=20).validate()
        completed = subprocess.run(
            [sys.executable, "src/MRTA_ABMA1.py", "--generations", "20"],
            cwd=ROOT, text=True, capture_output=True,
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("generations must equal 5", completed.stderr + completed.stdout)

    def test_03_caps_and_source_variant_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "max_objective_evaluations=0"):
            outer5.ABMA1Config(max_objective_evaluations=72).validate()
        with self.assertRaisesRegex(ValueError, "early_stop_patience=0"):
            outer5.ABMA1Config(early_stop_patience=1).validate()
        with self.assertRaisesRegex(ValueError, "legacy_exact_fast"):
            outer5.ABMA1Config(abma_variant="legacy").validate()

    def test_04_candidate_acceptance_and_rng_hashes_are_exact(self):
        original, wrapped = self.paired_results()
        for key in (
            "candidate_sequence_hash", "candidate_acceptance_hash",
            "outer_rng_final_state_hash", "inner_rng_final_state_hash",
        ):
            self.assertEqual(original.diagnostics[key], wrapped.diagnostics[key], key)

    def test_05_budget_generation_stop_history_and_trace_are_exact(self):
        original, wrapped = self.paired_results()
        self.assertEqual(original.diagnostics["objective_evaluation_count"], 24)
        self.assertEqual(wrapped.diagnostics["objective_evaluation_count"], 24)
        self.assertEqual(original.generations_completed, 5)
        self.assertEqual(wrapped.generations_completed, 5)
        self.assertEqual(original.diagnostics["stop_reason"], "generation_limit")
        self.assertEqual(wrapped.diagnostics["stop_reason"], "generation_limit")
        self.assertEqual(original.history, wrapped.history)
        strip = lambda rows: [{key: value for key, value in row.items() if key != "elapsed_time_s"} for row in rows]
        self.assertEqual(strip(original.diagnostics["checkpoint_trace"]),
                         strip(wrapped.diagnostics["checkpoint_trace"]))

    def test_06_solution_and_route_statistics_are_exact(self):
        original, wrapped = self.paired_results()
        left, right = original.best, wrapped.best
        self.assertEqual((left.x_up, left.x_low), (right.x_up, right.x_low))
        self.assertEqual(left.robot_order_ids, right.robot_order_ids)
        self.assertEqual(left.robot_direction_flags, right.robot_direction_flags)
        self.assertEqual(left.robot_orders, right.robot_orders)
        self.assertEqual(left.robots_stats, right.robots_stats)
        self.assertEqual(strip_runtime(left.alns_stats), strip_runtime(right.alns_stats))
        self.assertEqual(left.assignment_stats, right.assignment_stats)
        for field in ("fitness", "makespan", "load_imbalance", "total_idle_distance"):
            self.assertTrue(math.isclose(getattr(left, field), getattr(right, field),
                                         rel_tol=0.0, abs_tol=1e-12), field)

    def test_07_source_files_remain_frozen(self):
        self.assertEqual(sha256(ROOT / "src/MRTA_ABMA.py"), SOURCE_HASH)
        self.assertEqual(sha256(ROOT / "src/MRTA_HGA_PAPER_ALIGNED_CONTROL.py"), HGA_HASH)
        self.assertEqual(outer5.SOURCE_ABMA_SHA256, SOURCE_HASH)


if __name__ == "__main__":
    unittest.main(verbosity=2)
