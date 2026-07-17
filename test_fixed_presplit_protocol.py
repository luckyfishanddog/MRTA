import hashlib
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent


class FixedProtocolTests(unittest.TestCase):
    def test_independent_protocol_and_closed_gates(self):
        new = json.loads((ROOT / "UNIFIED_EXPERIMENT_PROTOCOL_FIXED_PRESPLIT_DEV.json").read_text(encoding="utf-8"))
        old = json.loads((ROOT / "UNIFIED_EXPERIMENT_PROTOCOL.json").read_text(encoding="utf-8"))
        manifest = json.loads((ROOT / "formal_experiment_manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(new["scientific_model_version"], "fixed_presplit_no_cross_region_zero_connected_travel_v1")
        self.assertFalse(new["formal_run_approved"]); self.assertFalse(new["user_approved_formal_execution"])
        self.assertFalse(old["formal_run_approved"])
        self.assertEqual(manifest["budget_selection_status"], "pending_user_selection_between_A_and_B")
        self.assertTrue(new["forbidden_actions"]["run_DE_LKH"])
        self.assertEqual((new["search"]["alns_iterations"], new["search"]["vnd_iterations"]), (80, 3))

    def test_protected_and_instance_hashes(self):
        protocol = json.loads((ROOT / "UNIFIED_EXPERIMENT_PROTOCOL_FIXED_PRESPLIT_DEV.json").read_text(encoding="utf-8"))
        for group in ("protected_file_sha256", "fixed_instance_file_sha256",
                      "implementation_file_sha256", "result_file_sha256"):
            for relative, expected in protocol[group].items():
                self.assertEqual(hashlib.sha256((ROOT / relative).read_bytes()).hexdigest(), expected)


if __name__ == "__main__": unittest.main()
