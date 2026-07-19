# Repository Cleanup Plan

Date: 2026-07-15  
Inventory source: `repository_inventory_before.json`

## Safety rules

1. Preserve frozen instances, the current protocol, frozen ABMA profile/checksum, active three-solver source, public model/normalization/output modules, all current tests, current reports, exact-fast evidence, Word thesis, and evidence of rejected historical candidates.
2. Do not run or delete DE+LKH. Remove it only from the active runner registry; retain its two source files as explicitly historical excluded-solver material.
3. Preserve historical experiment directories in place when their recorded commands, reports, or protocol snapshots embed the original path. Adding an archive marker is safer than moving those immutable records.
4. Do not delete exact duplicates when both locations are referenced evidence or a selected frozen instance and its selection candidate.
5. Unknown binary files and orphan files remain untouched unless a current import/reference/content comparison proves them disposable.

## Inventory findings

- 793 files were inventoried (excluding `.git` and the inventory output itself).
- The only unconditional deletion set is 20 generated `.pyc` files under `__pycache__`.
- No numbered `MRTA_ABMA(n).py`, `UNIFIED_EXPERIMENT_PROTOCOL(n).json`, or `run_unified_experiments(n).py` copies exist.
- 43 duplicate SHA-256 groups exist. Most are immutable protocol snapshots/logs, selected-instance copies, or deliberately copied exact-fast summary evidence; they will be retained.
- Historical experiment paths contain embedded original directories. Moving them would damage path-level reproducibility, so they remain in place and are recorded as retained historical evidence.
- `MRTA_DE_LKH.py` and `route_local_search.py` are retained at their original paths because the current historical exclusion object names them and old evidence records those paths. The active adapter and matrix registration will be removed.
- 原 `新建 DOCX 文档.docx` 已确认是有效的问题定义草稿，整理为 `docs/thesis/drafts/问题定义草稿.docx`；参考论文归入 `references/papers/`，测试工作簿归入 `tests/fixtures/`。旧 CSV 汇总和辅助脚本仍需结合正式协议审查。

## Planned actions

| Scope | Action | Reason |
|---|---|---|
| `__pycache__/`, `*.pyc` | Delete | Regenerable cache; no scientific evidence value |
| Active protocol ABMA entries | Consolidate | One frozen `ABMA-Legacy-Exact-Fast-v1` entry only |
| Rejected ABMA variants | Move logically into `historical_rejected_abma_variants` | Preserve evidence without presenting them as official profiles |
| Active runner `DELKHAdapter` and registration | Remove | DE+LKH is permanently excluded from active matrices |
| Historical DE source/results | Retain in place with historical classification | Preserve reproducibility and embedded paths |
| Profile validation | Strengthen | Recompute JSON SHA-256, verify `.sha256`, source hashes, 80/3, model, and adapter variant |
| Word thesis section 3.2 | Replace | Describe frozen exact-fast algorithm; demote rejected multifidelity mechanism |
| Word experiment section | De-duplicate and restrict | Only GA+ACO, Paper-Aligned-HGA-XCut-Control, and ABMA-Legacy-Exact-Fast-v1 |
| Old reports/results | Retain in place | Current/history references and immutable evidence paths |

## Gate before validation

After applying the plan, regenerate `repository_inventory_after.json` and the cleanup manifest/report, then require all unit tests, protocol validation, runner self-check, three-command dry-run, profile-hash checks, import checks, and broken-link checks to pass. Any failure stops validation and all later solver experiments.
