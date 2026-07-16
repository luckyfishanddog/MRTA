"""Sequential, fail-closed development runner for EPRK-MA and atomic HGA."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import statistics
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence

from atomic_problem_core import load_atomic_instance
from atomic_route_evaluator import AtomicRouteEvaluator, normalization_hash
from MRTA_EPRK_MA import EPRKConfig, EPRKMASolver
from MRTA_HGA_ATOMIC_CONTROL import HGAAtomicSolver

try:
    import psutil
except ImportError:  # pragma: no cover
    psutil = None


ROOT = Path(__file__).resolve().parent
OUT = ROOT / "eprk_development_outputs"
PROTOCOL = ROOT / "EPRK_MA_DEVELOPMENT_PROTOCOL.json"
DEFAULTS = ROOT / "eprk_ma_defaults.json"
CANDIDATE = ROOT / "eprk_ma_candidate_profile.json"
PROTECTED = [
    "MRTA_ABMA.py", "MRTA_HGA_PAPER_ALIGNED_CONTROL.py", "MRTA_GA_ACO.py",
    "mrta_problem_core.py", "objective_normalization.py", "abma_final_profile.json",
    "abma_final_profile.sha256", "UNIFIED_EXPERIMENT_PROTOCOL.json", "formal_experiment_manifest.json",
    "data/instances/selected/instance_w30.xlsx", "data/instances/selected/instance_w30.json", "data/instances/selected/instance_w30.png",
    "data/instances/selected/instance_w45.xlsx", "data/instances/selected/instance_w45.json", "data/instances/selected/instance_w45.png",
    "data/instances/selected/instance_w60.xlsx", "data/instances/selected/instance_w60.json", "data/instances/selected/instance_w60.png",
]


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def protected_hashes() -> Dict[str, str]:
    return {name: sha(ROOT / name) for name in PROTECTED}


def write_csv(path: Path, rows: Sequence[Mapping[str, Any]], fields: Sequence[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    names = list(fields or (list(rows[0]) if rows else []))
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=names, extrasaction="ignore"); writer.writeheader()
        if rows: writer.writerows(rows)


def load_context(name: str):
    instance = load_atomic_instance(ROOT / f"data/instances/atomic_l5_l1/instance_{name}_atomic.json")
    normalization = json.loads((ROOT / "atomic_normalization_spec.json").read_text(encoding="utf-8"))["instances"][name]
    return instance, normalization


def rss_mb() -> float:
    return psutil.Process().memory_info().rss / 1024 ** 2 if psutil is not None else float("nan")


def cross_validate(name: str, metrics: Mapping[str, Any]) -> bool:
    instance, spec = load_context(name); evaluator = AtomicRouteEvaluator(instance, spec)
    result = evaluator.evaluate_system(metrics["routes"])
    return abs(result.fitness - float(metrics["fitness"])) <= 1e-10 and abs(result.makespan - float(metrics["makespan"])) <= 1e-8


def run_one(solver_name: str, instance_name: str, seed: int, *, budget: int | None = None,
            time_limit: float | None = None, config: EPRKConfig | None = None, phase: str) -> Dict[str, Any]:
    target = OUT / phase / instance_name / solver_name / f"seed_{seed}.json"
    if target.exists():
        saved = json.loads(target.read_text(encoding="utf-8"))
        budget_match = budget is not None and saved.get("requested_primary_budget") == budget and saved.get("primary_evaluations") == budget
        time_match = time_limit is not None and saved.get("stop_reason") == "time_limit"
        if saved.get("status") == "success" and (budget_match or time_match) and cross_validate(instance_name, saved):
            saved["resumed_existing_result"] = True
            return saved
    instance, spec = load_context(instance_name); before_rss = rss_mb()
    if solver_name == "eprk":
        solver = EPRKMASolver(instance, spec, seed, config, budget, time_limit)
    elif solver_name == "hga_atomic":
        solver = HGAAtomicSolver(instance, spec, seed, budget, time_limit)
    else: raise ValueError(solver_name)
    best = solver.run(); metrics = solver.metrics(best); peak_rss = max(before_rss, rss_mb())
    metrics.update({"phase": phase, "instance": instance_name, "solver_id": solver_name,
                    "cross_validation_passed": cross_validate(instance_name, metrics), "rss_mb": peak_rss})
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(metrics, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return metrics


RACING_CONFIGS = [
    (40, .15, .05, 8), (40, .15, .10, 12), (40, .20, .05, 12), (40, .20, .10, 8),
    (60, .15, .05, 12), (60, .15, .10, 8), (60, .20, .05, 8), (60, .20, .10, 12),
]


def race() -> Dict[str, Any]:
    baseline = json.loads(DEFAULTS.read_text(encoding="utf-8")); rows: List[Dict[str, Any]] = []
    hga = {seed: run_one("hga_atomic", "w30", seed, budget=2000, phase="parameter_racing_hga") for seed in (101, 102, 103)}
    config_results = []
    for config_id, (population, elite, ls_elite, candidates) in enumerate(RACING_CONFIGS, 1):
        value = dict(baseline); value.update({"population_size": population, "elite_fraction": elite,
                                             "local_search_elite_fraction": ls_elite, "candidate_list_size": candidates})
        config = EPRKConfig.from_mapping(value); run_rows = []
        for seed in (101, 102, 103):
            result = run_one("eprk", "w30", seed, budget=2000, config=config, phase=f"parameter_racing/config_{config_id:02d}")
            row = {"config_id": config_id, "population_size": population, "elite_fraction": elite,
                   "local_search_elite_fraction": ls_elite, "candidate_list_size": candidates,
                   "seed": seed, "fitness": result["fitness"], "runtime_s": result["algorithm_time_s"],
                   "hga_runtime_s": hga[seed]["algorithm_time_s"], "primary_evaluations": result["primary_evaluations"],
                   "cross_validation_passed": result["cross_validation_passed"]}
            rows.append(row); run_rows.append(row)
        median_fitness = statistics.median(x["fitness"] for x in run_rows)
        median_log_ratio = statistics.median(math.log(max(1e-12, x["runtime_s"] / x["hga_runtime_s"])) for x in run_rows)
        config_results.append({"config_id": config_id, "score": median_fitness + .05 * median_log_ratio,
                               "median_fitness": median_fitness, "median_log_runtime_ratio": median_log_ratio, "config": value})
    selected = min(config_results, key=lambda x: (x["score"], x["config_id"]))
    profile = {"profile_name": "EPRK-MA-development-candidate", "status": "development_candidate_not_final",
               "solver_version": "EPRK-MA-v0.1-development", "selected_config_id": selected["config_id"],
               "selection_score": selected["score"], "configuration": selected["config"],
               "racing_instance": "w30", "racing_seeds": [101, 102, 103], "racing_primary_budget": 2000,
               "holdout_viewed": False, "formal_approved": False}
    CANDIDATE.write_text(json.dumps(profile, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    CANDIDATE.with_suffix(CANDIDATE.suffix + ".sha256").write_text(sha(CANDIDATE) + "\n", encoding="utf-8")
    write_csv(ROOT / "eprk_parameter_racing_results.csv", rows)
    (OUT / "parameter_racing_summary.json").write_text(json.dumps({"configurations": config_results, "selected": selected}, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    update_protocol({"parameter_racing_completed": True, "candidate_profile_frozen_before_gate": True}, "parameter_racing_completed")
    return profile


def candidate_config() -> EPRKConfig:
    return EPRKConfig.from_mapping(json.loads(CANDIDATE.read_text(encoding="utf-8"))["configuration"])


def equal_primary() -> List[Dict[str, Any]]:
    config = candidate_config(); rows = []
    for name in ("w30", "w45", "w60"):
        for seed in (104, 105):
            for solver in ("hga_atomic", "eprk"):
                rows.append(run_one(solver, name, seed, budget=5000, config=config if solver == "eprk" else None, phase="development_equal_primary"))
    write_csv(ROOT / "eprk_development_runs.csv", rows)
    work = [{key: row.get(key) for key in ("phase", "instance", "solver_id", "seed", "primary_evaluations", "algorithm_time_s", "rss_mb",
                                                    "route_evaluation_requests", "direction_dp_calls", "route_evaluations_per_primary", "direction_dp_per_primary",
                                                    "phenotype_cache_hits")} for row in rows]
    write_csv(ROOT / "eprk_work_amplification.csv", work)
    return rows


def equal_time(primary_rows: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    config = candidate_config(); rows = []
    for name in ("w30", "w45", "w60"):
        hga_times = [float(r["algorithm_time_s"]) for r in primary_rows if r["instance"] == name and r["solver_id"] == "hga_atomic"]
        limit = max(30.0, 10.0 * statistics.median(hga_times))
        for seed in (104, 105):
            for solver in ("hga_atomic", "eprk"):
                result = run_one(solver, name, seed, time_limit=limit, config=config if solver == "eprk" else None, phase="development_equal_time")
                result["common_time_limit_s"] = limit; rows.append(result)
    write_csv(ROOT / "eprk_equal_time_runs.csv", rows)
    return rows


def _median(rows, name, solver, field):
    return statistics.median(float(r[field]) for r in rows if r["instance"] == name and r["solver_id"] == solver)


def assess_gate(primary_rows: Sequence[Mapping[str, Any]], time_rows: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    comparisons = []
    for name in ("w30", "w45", "w60"):
        hp, ep = _median(primary_rows, name, "hga_atomic", "fitness"), _median(primary_rows, name, "eprk", "fitness")
        ht, et = _median(time_rows, name, "hga_atomic", "fitness"), _median(time_rows, name, "eprk", "fitness")
        comparisons.append({"instance": name, "hga_equal_primary_median": hp, "eprk_equal_primary_median": ep,
                            "equal_primary_difference_percent": 100 * (ep - hp) / hp,
                            "hga_equal_time_median": ht, "eprk_equal_time_median": et,
                            "equal_time_difference_percent": 100 * (et - ht) / ht})
    all_rows = list(primary_rows) + list(time_rows)
    criterion_a = all(r.get("status") == "success" and r.get("cross_validation_passed") and r.get("atomic_instance_hash") and r.get("normalization_hash") for r in all_rows)
    primary_diffs = [x["equal_primary_difference_percent"] for x in comparisons]
    time_diffs = [x["equal_time_difference_percent"] for x in comparisons]
    criterion_b = max(primary_diffs) <= 1.0 and sum(x <= -2.0 for x in primary_diffs) >= 2
    criterion_c = max(time_diffs) <= 1.0 and sum(x <= -3.0 for x in time_diffs) >= 2
    ratios = [_median(primary_rows, name, "eprk", "algorithm_time_s") / max(1e-12, _median(primary_rows, name, "hga_atomic", "algorithm_time_s")) for name in ("w30", "w45", "w60")]
    eprk_primary = [r for r in primary_rows if r["solver_id"] == "eprk"]
    criterion_d = max(ratios) <= 10 and max(float(r["route_evaluations_per_primary"]) for r in eprk_primary) < 100 and max(float(r["direction_dp_per_primary"]) for r in eprk_primary) < 20 and max(float(r["rss_mb"]) for r in eprk_primary if r["instance"] == "w60") < 2048
    criterion_e = all(int(r["primary_evaluations"]) == 5000 and r["stop_reason"] == "objective_budget" for r in primary_rows)
    passed = all((criterion_a, criterion_b, criterion_c, criterion_d, criterion_e))
    result = {"criteria": {"A_correctness": criterion_a, "B_equal_primary": criterion_b, "C_equal_time": criterion_c,
                           "D_efficiency": criterion_d, "E_two_seed_stability": criterion_e},
              "passed": passed, "comparisons": comparisons, "unit_primary_runtime_ratios": dict(zip(("w30", "w45", "w60"), ratios))}
    (OUT / "development_gate_summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    update_protocol({"development_gate_passed": passed, "holdout_authorized": passed}, "development_gate_passed" if passed else "development_gate_failed")
    return result


def create_unrun_outputs() -> None:
    for path in (ROOT / "eprk_holdout_runs.csv",):
        if not path.exists(): write_csv(path, [], ["status", "reason", "instance", "solver_id", "seed", "fitness"])


def holdout() -> Dict[str, Any]:
    protocol = json.loads(PROTOCOL.read_text(encoding="utf-8"))
    if not protocol["approval_gates"].get("holdout_authorized") or not protocol["approval_gates"].get("development_gate_passed"):
        create_unrun_outputs(); raise RuntimeError("holdout is fail-closed until the development gate passes")
    profile_hash_before = sha(CANDIDATE); config = candidate_config(); primary_rows = []
    for name in ("w30", "w45", "w60"):
        for seed in (106, 107, 108, 109, 110):
            for solver in ("hga_atomic", "eprk"):
                row = run_one(solver, name, seed, budget=5000, config=config if solver == "eprk" else None, phase="holdout_equal_primary")
                row["comparison_mode"] = "equal_primary"; primary_rows.append(row)
    development_primary = list(csv.DictReader((ROOT / "eprk_development_runs.csv").open(encoding="utf-8-sig")))
    time_rows = []
    for name in ("w30", "w45", "w60"):
        hga_times = [float(r["algorithm_time_s"]) for r in development_primary if r["instance"] == name and r["solver_id"] == "hga_atomic"]
        limit = max(30.0, 10.0 * statistics.median(hga_times))
        for seed in (106, 107, 108, 109, 110):
            for solver in ("hga_atomic", "eprk"):
                row = run_one(solver, name, seed, time_limit=limit, config=config if solver == "eprk" else None, phase="holdout_equal_time")
                row["comparison_mode"] = "equal_time"; row["common_time_limit_s"] = limit; time_rows.append(row)
    if sha(CANDIDATE) != profile_hash_before: raise RuntimeError("candidate profile changed after holdout began")
    write_csv(ROOT / "eprk_holdout_runs.csv", primary_rows + time_rows)
    outcome = assess_gate(primary_rows, time_rows)
    outcome["phase"] = "conditional_holdout"; outcome["candidate_profile_sha256"] = profile_hash_before
    (OUT / "holdout_summary.json").write_text(json.dumps(outcome, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    update_protocol({"holdout_viewed": True, "candidate_ready": bool(outcome["passed"]), "holdout_authorized": True},
                    "holdout_passed_candidate_ready" if outcome["passed"] else "holdout_failed_no_candidate")
    return outcome


def ablation() -> List[Dict[str, Any]]:
    protocol = json.loads(PROTOCOL.read_text(encoding="utf-8"))
    if not protocol["approval_gates"].get("development_gate_passed"):
        raise RuntimeError("ablation is allowed only after development gate pass")
    base = json.loads(CANDIDATE.read_text(encoding="utf-8"))["configuration"]
    variants = {
        "full": {}, "no_event_local_search": {"enable_event_local_search": False},
        "no_route_local_search": {"enable_route_local_search": False},
        "no_zero_continuity_initialization": {"enable_zero_continuity_initialization": False},
        "random_only_initialization": {"enable_heuristic_initialization": False},
        "no_migration": {"enable_migration": False}, "no_restart": {"enable_restart": False},
    }
    rows = []
    for variant, overrides in variants.items():
        value = dict(base); value.update(overrides); config = EPRKConfig.from_mapping(value)
        for seed in (104, 105):
            result = run_one("eprk", "w30", seed, budget=2000, config=config, phase=f"ablation/{variant}")
            rows.append({"variant": variant, "seed": seed, "fitness": result["fitness"], "algorithm_time_s": result["algorithm_time_s"],
                         "primary_evaluations": result["primary_evaluations"], "cross_validation_passed": result["cross_validation_passed"]})
    write_csv(ROOT / "eprk_ablation_runs.csv", rows)
    return rows


def update_protocol(gates: Mapping[str, bool], status: str) -> None:
    value = json.loads(PROTOCOL.read_text(encoding="utf-8")); value["status"] = status
    value["approval_gates"].update(gates)
    # Formal gates are immutable false in this development protocol.
    value["approval_gates"]["user_approved_formal_execution"] = False; value["approval_gates"]["formal_run_approved"] = False
    PROTOCOL.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def run_gate() -> Dict[str, Any]:
    before = protected_hashes(); (OUT / "protected_hashes_before.json").parent.mkdir(parents=True, exist_ok=True)
    (OUT / "protected_hashes_before.json").write_text(json.dumps(before, indent=2, sort_keys=True), encoding="utf-8")
    if not CANDIDATE.exists(): race()
    primary = equal_primary(); timed = equal_time(primary); gate = assess_gate(primary, timed)
    after = protected_hashes(); (OUT / "protected_hashes_after.json").write_text(json.dumps(after, indent=2, sort_keys=True), encoding="utf-8")
    if before != after: raise RuntimeError("protected legacy evidence changed")
    create_unrun_outputs(); return gate


def main() -> None:
    parser = argparse.ArgumentParser(); parser.add_argument("command", choices=("race", "gate", "holdout", "ablation", "all")); args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    if args.command == "race": result = race()
    elif args.command == "gate": result = run_gate()
    elif args.command == "holdout": result = holdout()
    elif args.command == "ablation": result = ablation()
    else:
        race(); gate = run_gate(); result = holdout() if gate["passed"] else gate
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__": main()
