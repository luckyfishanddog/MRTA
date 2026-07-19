from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

import formal_execution_authorization as auth


class FormalExecutionAuthorizationTests(unittest.TestCase):
    def copy_authorization(self) -> tuple[tempfile.TemporaryDirectory, Path]:
        temporary = tempfile.TemporaryDirectory()
        path = Path(temporary.name) / "FORMAL_EXECUTION_AUTHORIZATION.json"
        shutil.copy2(auth.AUTHORIZATION_PATH, path)
        shutil.copy2(auth.AUTHORIZATION_HASH_PATH, path.with_suffix(".sha256"))
        return temporary, path

    def rewrite(self, path: Path, mutate) -> None:
        value = json.loads(path.read_text(encoding="utf-8"))
        mutate(value)
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        path.with_suffix(".sha256").write_text(auth.sha256_file(path) + "\n", encoding="ascii")

    def test_01_current_authorization_is_valid(self):
        payload, errors = auth.verify_authorization()
        self.assertEqual([], errors)
        self.assertEqual("approved", payload["authorization_status"])

    def test_02_companion_hash_tampering_fails(self):
        temporary, path = self.copy_authorization()
        with temporary:
            path.with_suffix(".sha256").write_text("0" * 64 + "\n", encoding="ascii")
            self.assertIn("authorization companion hash mismatch", auth.verify_authorization(path)[1])

    def test_03_seed_scope_tampering_fails(self):
        temporary, path = self.copy_authorization()
        with temporary:
            self.rewrite(path, lambda value: value.__setitem__("authorized_solver_seeds", [201]))
            self.assertIn("authorized solver seeds mismatch", auth.verify_authorization(path)[1])

    def test_04_mode_scope_tampering_fails(self):
        temporary, path = self.copy_authorization()
        with temporary:
            self.rewrite(path, lambda value: value.__setitem__("authorized_modes", ["equal_time"]))
            self.assertIn("authorized modes mismatch", auth.verify_authorization(path)[1])

    def test_05_run_count_tampering_fails(self):
        temporary, path = self.copy_authorization()
        with temporary:
            self.rewrite(path, lambda value: value.__setitem__("authorized_run_count", 16))
            self.assertIn("authorization field mismatch: authorized_run_count", auth.verify_authorization(path)[1])

    def test_06_pr_head_tampering_fails(self):
        temporary, path = self.copy_authorization()
        with temporary:
            self.rewrite(path, lambda value: value.__setitem__("pr_head_sha", "0" * 40))
            self.assertIn("authorization field mismatch: pr_head_sha", auth.verify_authorization(path)[1])

    def test_07_scientific_change_permission_fails(self):
        temporary, path = self.copy_authorization()
        with temporary:
            self.rewrite(path, lambda value: value.__setitem__("no_scientific_model_changes", False))
            self.assertIn("authorization field mismatch: no_scientific_model_changes", auth.verify_authorization(path)[1])

    def test_08_frozen_protocol_remains_fail_closed(self):
        protocol = auth.load_json(auth.PROTOCOL_PATH)
        self.assertIs(protocol["approval_gates"]["formal_run_approved"], False)

    def test_09_frozen_manifest_remains_fail_closed(self):
        manifest = auth.load_json(auth.MANIFEST_PATH)
        self.assertIs(manifest["formal_execution_authorized"], False)
        self.assertEqual(4680, len(manifest["entries"]))


if __name__ == "__main__":
    unittest.main()
