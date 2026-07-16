# Atomic formal preflight remediation report

- Passed: `True`
- Exact-hash pass checks: `21`
- Legacy-float compatibility pass checks: `2`
- Failed checks: `0`
- Legacy normalization is explicitly reported as compatibility, never exact.
- All 39 atomic normalization files remain subject to exact SHA-256 checks.
- Full formal execution remains unauthorized; seeds 201-230 and the 4,680-run matrix were not executed.

| check | status |
|---|---|
| 01_git_worktree_state | exact_hash_pass |
| 02_protected_hashes | exact_hash_pass |
| 03_eprk_frozen_profile_hash | exact_hash_pass |
| 04_eprk_source_hashes | exact_hash_pass |
| 05_atomic_model_freeze_manifest | exact_hash_pass |
| 06_atomic_model_source_hashes | exact_hash_pass |
| 07_benchmark_suite_manifest | exact_hash_pass |
| 08_39_raw_instance_hashes | exact_hash_pass |
| 09_39_atomic_instance_hashes | exact_hash_pass |
| 10_39_normalization_instance_hashes | exact_hash_pass |
| 11_legacy_normalization_compatibility_audit | legacy_float_compatibility_pass |
| 12_formal_protocol_hash | exact_hash_pass |
| 13_formal_manifest_hash | exact_hash_pass |
| 14_run_id_uniqueness | exact_hash_pass |
| 15_command_hash_uniqueness | exact_hash_pass |
| 16_seed_policy | exact_hash_pass |
| 17_solver_pairing | exact_hash_pass |
| 18_execution_order_alternation | exact_hash_pass |
| 19_collision_policy | exact_hash_pass |
| 20_resume_policy | exact_hash_pass |
| 20b_legacy_exact_validator_expected_drift | legacy_float_compatibility_pass |
| 21_unit_tests | exact_hash_pass |
| 22_minimum_solver_smoke | exact_hash_pass |
