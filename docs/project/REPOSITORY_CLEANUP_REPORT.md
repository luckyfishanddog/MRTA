# Repository Cleanup Report

## Outcome

The repository cleanup was intentionally conservative. Twenty regenerable Python bytecode files were deleted after each absolute target was verified to be inside `D:/pybullet_test/MRTA_GA`. No scientific source, frozen instance, profile, raw result, or historical report was deleted.

The active DE+LKH adapter and registration were removed from `run_unified_experiments.py`. The historical sources `MRTA_DE_LKH.py` and `route_local_search.py` remain in place for reproducibility and cannot be selected by the unified runner.

## Inventory comparison

| Item | Before | After |
|---|---:|---:|
| Files represented by the inventories | 793 | 931 |
| Deleted cache files | 0 | 20 removed |
| Active numbered copies detected | 0 | 0 |
| Historical experiment-result files retained in place | 474 | 474 |

The after inventory is the final stage snapshot and therefore also contains the newly generated validation, preflight, Pilot, protocol-validation and handoff evidence. `repository_inventory_before.json` is retained as evidence and was intentionally excluded from its own inventory.

## Archive policy

The requested archive directory structure was created. Historical result directories were not moved because immutable run records contain their existing relative paths. Moving them would weaken path-based traceability. One pre-edit thesis backup was copied to `archive/stage_reports/双滑轨四悬臂机器人任务分配及焊缝排序_before_protocol_2.5.docx` before the current thesis text was updated.

## Documents and protocol

The current Word thesis now defines the formal ABMA method only as `ABMA-Legacy-Exact-Fast-v1` / `legacy_exact_fast`, with SHADE outer search, ALNS/VND `80/3`, exact direction DP, and structural-equivalence acceleration. Rejected incremental/multi-fidelity variants are explicitly historical. The duplicated formal-algorithm paragraph was reduced to one copy, and DE+LKH is described only as excluded history.

Detailed file-level actions are recorded in `repository_cleanup_manifest.json`; the complete states are in `repository_inventory_before.json` and `repository_inventory_after.json`.
