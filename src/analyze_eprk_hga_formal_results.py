"""Rebuild formal summaries, paired statistics, qualification gates and figures."""

from __future__ import annotations

import csv
import hashlib
import json
import math
import random
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


ROOT = Path(__file__).resolve().parents[1]
FORMAL = ROOT / "formal_atomic"
OUT = FORMAL / "formal_execution"
RAW = OUT / "raw_runs.csv"
COLLISION = OUT / "collision_runs.csv"
PROTOCOL = FORMAL / "EPRK_ATOMIC_FORMAL_PROTOCOL.json"
BOOTSTRAP_SEED = 20260716
BOOTSTRAP_REPETITIONS = 10000
TIE_TOLERANCE = 1e-12
SOLVERS = ("eprk", "hga_atomic")
MODES = ("equal_primary", "equal_time")


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: Sequence[Mapping[str, Any]], fields: Sequence[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(fields or (rows[0].keys() if rows else []))
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def write_json(path: Path, value: Any) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return digest


def numeric(row: Mapping[str, str], key: str) -> float:
    value = row.get(key, "")
    return float(value) if value not in (None, "") else float("nan")


def median(values: Iterable[float]) -> float:
    finite = [value for value in values if math.isfinite(value)]
    return statistics.median(finite) if finite else float("nan")


def quantile(values: Sequence[float], probability: float) -> float:
    ordered = sorted(value for value in values if math.isfinite(value))
    if not ordered:
        return float("nan")
    position = probability * (len(ordered) - 1)
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    fraction = position - lower
    return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction


def iqr(values: Sequence[float]) -> float:
    return quantile(values, 0.75) - quantile(values, 0.25)


def relative_gap(eprk: float, hga: float) -> float:
    if hga == 0.0:
        return 0.0 if eprk == 0.0 else math.copysign(float("inf"), eprk)
    return (eprk - hga) / hga * 100.0


def average_ranks(values: Sequence[float]) -> list[float]:
    ordered = sorted(enumerate(values), key=lambda pair: pair[1])
    ranks = [0.0] * len(values)
    start = 0
    while start < len(ordered):
        end = start + 1
        while end < len(ordered) and ordered[end][1] == ordered[start][1]:
            end += 1
        rank = ((start + 1) + end) / 2.0
        for original, _ in ordered[start:end]:
            ranks[original] = rank
        start = end
    return ranks


def wilcoxon_signed_rank(differences: Sequence[float]) -> dict[str, float | int]:
    nonzero = [value for value in differences if abs(value) > TIE_TOLERANCE]
    zeros = len(differences) - len(nonzero)
    if not nonzero:
        return {"n_nonzero": 0, "zero_count": zeros, "w_plus": 0.0, "w_minus": 0.0,
                "statistic": 0.0, "z": 0.0, "p_two_sided": 1.0, "rank_biserial": 0.0}
    absolute = [abs(value) for value in nonzero]
    ranks = average_ranks(absolute)
    w_plus = sum(rank for rank, value in zip(ranks, nonzero) if value > 0)
    w_minus = sum(rank for rank, value in zip(ranks, nonzero) if value < 0)
    total = w_plus + w_minus
    n = len(nonzero)
    mean = n * (n + 1) / 4.0
    tie_counts: list[int] = []
    for value in sorted(set(absolute)):
        count = absolute.count(value)
        if count > 1:
            tie_counts.append(count)
    variance = n * (n + 1) * (2 * n + 1) / 24.0
    variance -= sum(count ** 3 - count for count in tie_counts) / 48.0
    z = (w_plus - mean) / math.sqrt(variance) if variance > 0 else 0.0
    p_value = math.erfc(abs(z) / math.sqrt(2.0))
    return {"n_nonzero": n, "zero_count": zeros, "w_plus": w_plus, "w_minus": w_minus,
            "statistic": min(w_plus, w_minus), "z": z, "p_two_sided": p_value,
            "rank_biserial": (w_plus - w_minus) / total if total else 0.0}


def bootstrap_median_ci(values: Sequence[float], seed: int) -> tuple[float, float]:
    rng = random.Random(seed)
    finite = [value for value in values if math.isfinite(value)]
    if not finite:
        return float("nan"), float("nan")
    samples = sorted(statistics.median(rng.choices(finite, k=len(finite))) for _ in range(BOOTSTRAP_REPETITIONS))
    return quantile(samples, 0.025), quantile(samples, 0.975)


def holm_adjust(p_values: Sequence[float]) -> list[float]:
    order = sorted(range(len(p_values)), key=lambda index: p_values[index])
    adjusted = [1.0] * len(p_values)
    running = 0.0
    for rank, index in enumerate(order):
        running = max(running, min(1.0, (len(p_values) - rank) * p_values[index]))
        adjusted[index] = running
    return adjusted


def classify_gap(gap: float) -> str:
    if gap < -TIE_TOLERANCE:
        return "win"
    if gap > TIE_TOLERANCE:
        return "loss"
    return "tie"


def validate_raw(rows: Sequence[Mapping[str, str]], collision_rows: Sequence[Mapping[str, str]]) -> None:
    if len(rows) != 4680 or len({row["run_id"] for row in rows}) != 4680:
        raise RuntimeError("formal raw table must contain 4680 unique successful runs")
    if len(collision_rows) != 4680 or len({row["run_id"] for row in collision_rows}) != 4680:
        raise RuntimeError("collision table must contain 4680 unique runs")
    if any(row["status"] != "success" or row["cross_validation_passed"] != "True" for row in rows):
        raise RuntimeError("formal raw table contains a failed or non-recomputable result")
    if any(row["raw_result_unchanged"] != "True" for row in collision_rows):
        raise RuntimeError("collision postprocess changed a raw result")


def paired_data(rows: Sequence[Mapping[str, str]]) -> tuple[dict[tuple[str, str, int, str], Mapping[str, str]], list[str]]:
    index = {(row["instance_id"], row["mode"], int(row["solver_seed"]), row["solver"]): row for row in rows}
    instances = sorted({row["instance_id"] for row in rows})
    expected = len(instances) * len(MODES) * 30 * len(SOLVERS)
    if len(index) != expected:
        raise RuntimeError("formal paired index is incomplete")
    return index, instances


def build_instance_statistics(rows: Sequence[Mapping[str, str]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    index, instances = paired_data(rows)
    meta = {row["instance_id"]: row for row in rows}
    tests: list[dict[str, Any]] = []
    effects: list[dict[str, Any]] = []
    bootstraps: list[dict[str, Any]] = []
    wtl: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []
    for mode in MODES:
        mode_test_indices = []
        for instance in instances:
            eprk = [numeric(index[(instance, mode, seed, "eprk")], "fitness") for seed in range(201, 231)]
            hga = [numeric(index[(instance, mode, seed, "hga_atomic")], "fitness") for seed in range(201, 231)]
            gaps = [relative_gap(left, right) for left, right in zip(eprk, hga)]
            improvements = [-gap for gap in gaps]
            test = wilcoxon_signed_rank(improvements)
            ci_low, ci_high = bootstrap_median_ci(gaps, BOOTSTRAP_SEED + len(tests))
            outcomes = [classify_gap(gap) for gap in gaps]
            common = {"instance_id": instance, "family": meta[instance]["family"],
                      "nominal_weld_count": int(meta[instance]["nominal_weld_count"]), "mode": mode}
            tests.append({**common, "metric": "normalized_fitness", "paired_seed_count": 30,
                          "wilcoxon_statistic": test["statistic"], "wilcoxon_z": test["z"],
                          "p_raw": test["p_two_sided"], "zero_method": "wilcox",
                          "tie_tolerance": TIE_TOLERANCE})
            mode_test_indices.append(len(tests) - 1)
            effects.append({**common, "rank_biserial_positive_favors_eprk": test["rank_biserial"],
                            "median_relative_gap_percent": median(gaps)})
            bootstraps.append({**common, "bootstrap_seed": BOOTSTRAP_SEED + len(tests) - 1,
                               "bootstrap_repetitions": BOOTSTRAP_REPETITIONS,
                               "median_relative_gap_percent": median(gaps),
                               "ci_95_low_percent": ci_low, "ci_95_high_percent": ci_high})
            wtl.append({**common, "wins_eprk": outcomes.count("win"), "ties": outcomes.count("tie"),
                        "losses_eprk": outcomes.count("loss")})
            for solver, values in (("eprk", eprk), ("hga_atomic", hga)):
                solver_rows = [index[(instance, mode, seed, solver)] for seed in range(201, 231)]
                summaries.append({
                    **common, "solver": solver, "seed_count": 30,
                    "median_fitness": median(values),
                    "median_makespan": median(numeric(row, "makespan") for row in solver_rows),
                    "median_load_imbalance": median(numeric(row, "load_imbalance") for row in solver_rows),
                    "median_idle_distance": median(numeric(row, "idle_distance") for row in solver_rows),
                    "median_solver_wall_time_s": median(numeric(row, "solver_wall_clock_time_s") for row in solver_rows),
                    "median_primary_evaluations": median(numeric(row, "primary_evaluations") for row in solver_rows),
                    "median_evaluations_per_second": median(numeric(row, "evaluations_per_second") for row in solver_rows),
                    "median_route_evaluations_per_primary": median(numeric(row, "route_evaluations_per_primary") for row in solver_rows),
                    "median_direction_dp_per_primary": median(numeric(row, "direction_dp_per_primary") for row in solver_rows),
                    "median_rss_mb": median(numeric(row, "rss_mb") for row in solver_rows),
                })
        adjusted = holm_adjust([float(tests[index]["p_raw"]) for index in mode_test_indices])
        for index_value, p_adjusted in zip(mode_test_indices, adjusted):
            tests[index_value]["p_holm"] = p_adjusted
            gap = next(row["median_relative_gap_percent"] for row in effects
                       if row["instance_id"] == tests[index_value]["instance_id"] and row["mode"] == mode)
            tests[index_value]["holm_significant_direction"] = (
                "eprk" if p_adjusted < 0.05 and gap < 0 else
                "hga" if p_adjusted < 0.05 and gap > 0 else "none"
            )
    return tests, effects, bootstraps, wtl, summaries


def group_gap_summary(effects: Sequence[Mapping[str, Any]], group_key: str) -> list[dict[str, Any]]:
    rows = []
    for mode in MODES:
        grouped: dict[str, list[float]] = defaultdict(list)
        for row in effects:
            if row["mode"] == mode:
                grouped[str(row[group_key])].append(float(row["median_relative_gap_percent"]))
        for group, values in sorted(grouped.items()):
            ci_low, ci_high = bootstrap_median_ci(values, BOOTSTRAP_SEED + len(rows) + 1000)
            outcomes = [classify_gap(value) for value in values]
            rows.append({group_key: group, "mode": mode, "instance_count": len(values),
                         "median_relative_gap_percent": median(values), "iqr_percent": iqr(values),
                         "bootstrap_ci_95_low_percent": ci_low, "bootstrap_ci_95_high_percent": ci_high,
                         "wins_eprk": outcomes.count("win"), "ties": outcomes.count("tie"),
                         "losses_eprk": outcomes.count("loss")})
    return rows


def overall_statistics(effects: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for mode in MODES:
        gaps = [float(row["median_relative_gap_percent"]) for row in effects if row["mode"] == mode]
        improvements = [-gap for gap in gaps]
        test = wilcoxon_signed_rank(improvements)
        ci_low, ci_high = bootstrap_median_ci(gaps, BOOTSTRAP_SEED + 5000 + len(rows))
        outcomes = [classify_gap(gap) for gap in gaps]
        rows.append({"mode": mode, "instance_count": len(gaps), "median_relative_gap_percent": median(gaps),
                     "iqr_percent": iqr(gaps), "wilcoxon_statistic": test["statistic"],
                     "wilcoxon_z": test["z"], "wilcoxon_p_two_sided": test["p_two_sided"],
                     "rank_biserial_positive_favors_eprk": test["rank_biserial"],
                     "bootstrap_ci_95_low_percent": ci_low, "bootstrap_ci_95_high_percent": ci_high,
                     "wins_eprk": outcomes.count("win"), "ties": outcomes.count("tie"),
                     "losses_eprk": outcomes.count("loss")})
    return rows


def endpoint_statistics(rows: Sequence[Mapping[str, str]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    index, instances = paired_data(rows)
    metrics = (
        "fitness", "makespan", "load_imbalance", "idle_distance",
        "solver_wall_clock_time_s", "evaluations_per_second", "primary_evaluations",
    )
    instance_rows: list[dict[str, Any]] = []
    overall_rows: list[dict[str, Any]] = []
    for mode in MODES:
        for metric in metrics:
            instance_gaps = []
            for instance in instances:
                gaps = [relative_gap(
                    numeric(index[(instance, mode, seed, "eprk")], metric),
                    numeric(index[(instance, mode, seed, "hga_atomic")], metric),
                ) for seed in range(201, 231)]
                value = median(gaps)
                instance_gaps.append(value)
                instance_rows.append({"instance_id": instance, "mode": mode, "metric": metric,
                                      "median_relative_gap_percent": value})
            # For throughput only, a positive raw gap favors EPRK. All other metrics are minimized.
            improvements = (instance_gaps if metric == "evaluations_per_second" else [-gap for gap in instance_gaps])
            test = wilcoxon_signed_rank(improvements)
            ci_low, ci_high = bootstrap_median_ci(instance_gaps, BOOTSTRAP_SEED + 8000 + len(overall_rows))
            outcomes = [
                "win" if (gap > TIE_TOLERANCE if metric == "evaluations_per_second" else gap < -TIE_TOLERANCE)
                else "loss" if (gap < -TIE_TOLERANCE if metric == "evaluations_per_second" else gap > TIE_TOLERANCE)
                else "tie"
                for gap in instance_gaps
            ]
            overall_rows.append({"mode": mode, "metric": metric, "instance_count": len(instance_gaps),
                                 "median_relative_gap_percent": median(instance_gaps), "iqr_percent": iqr(instance_gaps),
                                 "wilcoxon_p_two_sided": test["p_two_sided"],
                                 "rank_biserial_positive_favors_eprk": test["rank_biserial"],
                                 "bootstrap_ci_95_low_percent": ci_low, "bootstrap_ci_95_high_percent": ci_high,
                                 "wins_eprk": outcomes.count("win"), "ties": outcomes.count("tie"),
                                 "losses_eprk": outcomes.count("loss")})
    return instance_rows, overall_rows


def collision_summary(collision_rows: Sequence[Mapping[str, str]], raw_rows: Sequence[Mapping[str, str]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    raw_index = {row["run_id"]: row for row in raw_rows}
    groups: dict[tuple[str, str, int], dict[str, Mapping[str, str]]] = defaultdict(dict)
    for row in collision_rows:
        key = (row["instance_id"], row["mode"], int(row["solver_seed"]))
        groups[key][row["solver"]] = row
    gaps = []
    added_wait = {solver: [] for solver in SOLVERS}
    conflicts = {solver: [] for solver in SOLVERS}
    unresolved = 0
    for pair in groups.values():
        eprk = numeric(pair["eprk"], "collision_adjusted_makespan")
        hga = numeric(pair["hga_atomic"], "collision_adjusted_makespan")
        gaps.append(relative_gap(eprk, hga))
        for solver in SOLVERS:
            added_wait[solver].append(numeric(pair[solver], "added_waiting_time"))
            conflicts[solver].append(numeric(pair[solver], "conflict_count"))
            unresolved += int(float(pair[solver]["unresolved_conflict_count"]))
    by_instance_mode: list[dict[str, Any]] = []
    grouped_gaps: dict[tuple[str, str], list[float]] = defaultdict(list)
    for (instance, mode, _), pair in groups.items():
        grouped_gaps[(instance, mode)].append(relative_gap(
            numeric(pair["eprk"], "collision_adjusted_makespan"),
            numeric(pair["hga_atomic"], "collision_adjusted_makespan"),
        ))
    for (instance, mode), values in sorted(grouped_gaps.items()):
        by_instance_mode.append({"instance_id": instance, "mode": mode,
                                 "median_collision_adjusted_makespan_gap_percent": median(values)})
    overall = {"median_collision_adjusted_makespan_gap_percent": median(gaps),
               "eprk_median_added_waiting_time": median(added_wait["eprk"]),
               "hga_median_added_waiting_time": median(added_wait["hga_atomic"]),
               "eprk_median_conflict_count": median(conflicts["eprk"]),
               "hga_median_conflict_count": median(conflicts["hga_atomic"]),
               "total_unresolved_conflicts": unresolved,
               "raw_result_hash_unchanged_count": sum(row["raw_result_unchanged"] == "True" for row in collision_rows)}
    return by_instance_mode, overall


def work_amplification(rows: Sequence[Mapping[str, str]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[Mapping[str, str]]] = defaultdict(list)
    for row in rows:
        grouped[(row["mode"], row["solver"])].append(row)
    output = []
    for (mode, solver), values in sorted(grouped.items()):
        output.append({"mode": mode, "solver": solver,
                       "median_route_evaluations_per_primary": median(numeric(row, "route_evaluations_per_primary") for row in values),
                       "median_direction_dp_per_primary": median(numeric(row, "direction_dp_per_primary") for row in values),
                       "median_evaluations_per_second": median(numeric(row, "evaluations_per_second") for row in values),
                       "median_solver_wall_time_s": median(numeric(row, "solver_wall_clock_time_s") for row in values)})
    return output


def convergence_summary(rows: Sequence[Mapping[str, str]]) -> list[dict[str, Any]]:
    output = []
    for row in rows:
        trace = json.loads(row.get("best_trace_json") or "[]")
        final = numeric(row, "fitness")
        record: dict[str, Any] = {"run_id": row["run_id"], "mode": row["mode"], "instance_id": row["instance_id"],
                                  "solver": row["solver"], "solver_seed": row["solver_seed"],
                                  "improvement_count": len(trace),
                                  "improvements_per_second": len(trace) / max(numeric(row, "solver_wall_clock_time_s"), 1e-12)}
        for tolerance in (1, 2, 5):
            threshold = final * (1.0 + tolerance / 100.0)
            reached = next((point for point in trace if float(point["fitness"]) <= threshold), None)
            record[f"time_to_within_{tolerance}pct_s"] = reached.get("elapsed_s") if reached else None
            record[f"primary_to_within_{tolerance}pct"] = reached.get("primary_evaluations") if reached else None
        output.append(record)
    return output


def qualification(protocol: Mapping[str, Any], effects: Sequence[Mapping[str, Any]], tests: Sequence[Mapping[str, Any]],
                  overall: Sequence[Mapping[str, Any]], family: Sequence[Mapping[str, Any]], size: Sequence[Mapping[str, Any]],
                  raw_rows: Sequence[Mapping[str, str]], collision_rows: Sequence[Mapping[str, str]],
                  collision_overall: Mapping[str, Any], work: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    gates = protocol["qualification_gates"]
    by_mode = {row["mode"]: row for row in overall}
    equal_time = by_mode["equal_time"]
    equal_primary = by_mode["equal_primary"]
    et_family = [row for row in family if row["mode"] == "equal_time"]
    ep_tests = [row for row in tests if row["mode"] == "equal_primary"]
    et_tests = [row for row in tests if row["mode"] == "equal_time"]
    a = (len(raw_rows) == 4680 and all(row["cross_validation_passed"] == "True" for row in raw_rows)
         and collision_overall["total_unresolved_conflicts"] == 0
         and collision_overall["raw_result_hash_unchanged_count"] == 4680)
    b_rules = gates["B_equal_time_primary_endpoint"]
    b = (-float(equal_time["median_relative_gap_percent"]) >= b_rules["minimum_overall_median_improvement_percent"]
         and float(equal_time["wilcoxon_p_two_sided"]) < b_rules["wilcoxon_p_less_than"]
         and float(equal_time["rank_biserial_positive_favors_eprk"]) >= b_rules["minimum_rank_biserial_favoring_eprk"]
         and float(equal_time["bootstrap_ci_95_high_percent"]) < b_rules["bootstrap_95_ci_upper_less_than"]
         and float(equal_time["wins_eprk"]) / 39 >= b_rules["minimum_instance_win_fraction"]
         and max(float(row["median_relative_gap_percent"]) for row in et_family) <= b_rules["maximum_family_median_degradation_percent"])
    c_rules = gates["C_equal_primary"]
    c1 = (-float(equal_primary["median_relative_gap_percent"]) >= c_rules["option_1"]["minimum_overall_median_improvement_percent"]
          and float(equal_primary["wilcoxon_p_two_sided"]) < c_rules["option_1"]["wilcoxon_p_less_than"])
    better_or_tied = (float(equal_primary["wins_eprk"]) + float(equal_primary["ties"])) / 39
    significantly_worse = (float(equal_primary["median_relative_gap_percent"]) > 0
                           and float(equal_primary["wilcoxon_p_two_sided"]) < 0.05)
    c2 = (float(equal_primary["bootstrap_ci_95_high_percent"]) <= c_rules["option_2"]["bootstrap_95_ci_upper_at_most_percent"]
          and better_or_tied >= c_rules["option_2"]["minimum_better_or_tied_instance_fraction"]
          and not significantly_worse)
    c = c1 or c2
    d_rules = gates["D_significant_loss"]
    hga_losses = [row for row in et_tests if row["holm_significant_direction"] == "hga"]
    per_family = defaultdict(list)
    for row in et_tests:
        per_family[row["family"]].append(row)
    d = (len(hga_losses) / 39 <= d_rules["maximum_holm_significant_hga_instance_fraction"]
         and all(sum(row["holm_significant_direction"] == "hga" for row in values) / len(values)
                 <= d_rules["maximum_per_family_holm_significant_hga_instance_fraction"]
                 for values in per_family.values()))
    e_rules = gates["E_collision"]
    collision_instance, _ = collision_summary(collision_rows, raw_rows)
    collision_family_gaps: dict[str, list[float]] = defaultdict(list)
    family_lookup = {row["instance_id"]: row["family"] for row in raw_rows}
    for row in collision_instance:
        if row["mode"] == "equal_time":
            collision_family_gaps[family_lookup[row["instance_id"]]].append(float(row["median_collision_adjusted_makespan_gap_percent"]))
    e = (float(collision_overall["median_collision_adjusted_makespan_gap_percent"]) <= 0.0
         and all(median(values) <= e_rules["maximum_family_median_degradation_percent"] for values in collision_family_gaps.values())
         and collision_overall["total_unresolved_conflicts"] == 0)
    f_rules = gates["F_computational_efficiency"]
    ep_eprk = next(row for row in work if row["mode"] == "equal_primary" and row["solver"] == "eprk")
    ep_hga = next(row for row in work if row["mode"] == "equal_primary" and row["solver"] == "hga_atomic")
    wall_ratio = float(ep_eprk["median_solver_wall_time_s"]) / float(ep_hga["median_solver_wall_time_s"])
    f = (wall_ratio <= f_rules["maximum_overall_median_eprk_hga_wall_time_ratio"]
         and float(ep_eprk["median_route_evaluations_per_primary"]) <= f_rules["maximum_overall_median_route_evaluations_per_primary"]
         and float(ep_eprk["median_direction_dp_per_primary"]) <= f_rules["maximum_overall_median_direction_dp_per_primary"])
    et_sizes = [row for row in size if row["mode"] == "equal_time" and row["nominal_size_group"] != "real"]
    competitive_sizes = all(float(row["median_relative_gap_percent"]) <= 1.0 for row in et_sizes)
    winning_families = sum(float(row["median_relative_gap_percent"]) < -TIE_TOLERANCE for row in et_family)
    required_families = set(gates["G_stability"]["families_that_must_not_all_fail"])
    required_family_rows = [row for row in et_family if row["family"] in required_families]
    required_not_all_fail = any(float(row["median_relative_gap_percent"]) <= 1.0 for row in required_family_rows)
    g = competitive_sizes and winning_families >= 2 and required_not_all_fail
    details = {
        "A_correctness": {"passed": a, "success_count": len(raw_rows), "unresolved_conflicts": collision_overall["total_unresolved_conflicts"]},
        "B_equal_time_primary_endpoint": {"passed": b, "overall": equal_time, "family_rows": et_family},
        "C_equal_primary": {"passed": c, "option_1_passed": c1, "option_2_passed": c2, "overall": equal_primary},
        "D_significant_loss": {"passed": d, "holm_significant_hga_instance_count": len(hga_losses)},
        "E_collision": {"passed": e, "overall": dict(collision_overall)},
        "F_computational_efficiency": {"passed": f, "eprk_hga_wall_time_ratio": wall_ratio, "eprk_work": ep_eprk},
        "G_stability": {"passed": g, "competitive_sizes": competitive_sizes, "winning_family_count": winning_families,
                        "required_families_not_all_fail": required_not_all_fail},
    }
    qualified = all(value["passed"] for value in details.values())
    return {"qualification_rule": gates["qualification_rule"], "gates": details,
            "eprk_main_algorithm_qualified": qualified}


def plots(raw_rows: Sequence[Mapping[str, str]], effects: Sequence[Mapping[str, Any]], family: Sequence[Mapping[str, Any]],
          size: Sequence[Mapping[str, Any]], wtl: Sequence[Mapping[str, Any]], collision_rows: Sequence[Mapping[str, str]],
          convergence: Sequence[Mapping[str, Any]]) -> None:
    def save(name: str) -> None:
        plt.tight_layout(); plt.savefig(OUT / name, dpi=160); plt.close()
    for mode, name in (("equal_time", "overall_equal_time_fitness_boxplot.png"),
                       ("equal_primary", "overall_equal_primary_fitness_boxplot.png")):
        values = [[numeric(row, "fitness") for row in raw_rows if row["mode"] == mode and row["solver"] == solver] for solver in SOLVERS]
        plt.figure(figsize=(7, 4.5)); plt.boxplot(values, tick_labels=["EPRK-MA", "Paper-Aligned-HGA-Atomic-Control"], showfliers=False)
        plt.ylabel("Normalized fitness"); plt.title(mode.replace("_", " ")); save(name)
    for rows, key, name, title in ((family, "family", "relative_gap_by_family.png", "Instance-level median fitness gap by family"),
                                   (size, "nominal_size_group", "relative_gap_by_instance_size.png", "Instance-level median fitness gap by size")):
        labels = sorted({row[key] for row in rows if row["mode"] == "equal_time"})
        vals = [next(float(row["median_relative_gap_percent"]) for row in rows if row["mode"] == "equal_time" and row[key] == label) for label in labels]
        plt.figure(figsize=(10, 4.8)); plt.axhline(0, color="black", linewidth=.8); plt.bar(labels, vals); plt.xticks(rotation=35, ha="right")
        plt.ylabel("Relative gap % (negative favors EPRK)"); plt.title(title); save(name)
    ep = [row for row in raw_rows if row["mode"] == "equal_primary"]
    pair_index = {(row["instance_id"], row["solver_seed"], row["solver"]): row for row in ep}
    pairs = sorted({(row["instance_id"], row["solver_seed"]) for row in ep})
    ratios = [numeric(pair_index[(i, s, "eprk")], "solver_wall_clock_time_s") / numeric(pair_index[(i, s, "hga_atomic")], "solver_wall_clock_time_s") for i, s in pairs]
    plt.figure(figsize=(7, 4.5)); plt.boxplot(ratios, tick_labels=["EPRK/HGA"]); plt.axhline(1, color="black", linewidth=.8); plt.ylabel("Wall-time ratio"); save("equal_primary_wall_time_ratio.png")
    for metric, name, ylabel in (("evaluations_per_second", "evaluations_per_second.png", "Primary evaluations/s"),
                                 ("route_evaluations_per_primary", "route_eval_per_primary.png", "Route eval / Primary"),
                                 ("direction_dp_per_primary", "direction_dp_per_primary.png", "Direction DP / Primary"),
                                 ("rss_mb", "memory_by_instance_size.png", "RSS MB")):
        vals = [[numeric(row, metric) for row in raw_rows if row["solver"] == solver and math.isfinite(numeric(row, metric))] for solver in SOLVERS]
        plt.figure(figsize=(7, 4.5))
        if any(vals):
            plt.boxplot(vals, tick_labels=["EPRK", "HGA"], showfliers=False)
        else:
            plt.text(.5, .5, f"{ylabel} unavailable", ha="center", va="center", transform=plt.gca().transAxes)
            plt.xticks([])
        plt.ylabel(ylabel); save(name)
    totals = {label: sum(int(row[label]) for row in wtl if row["mode"] == "equal_time") for label in ("wins_eprk", "ties", "losses_eprk")}
    plt.figure(figsize=(7, 4.5)); plt.bar(["EPRK wins", "Ties", "EPRK losses"], list(totals.values())); plt.ylabel("Seed-level pairs"); save("win_tie_loss_summary.png")
    for mode, name in (("equal_primary", "convergence_profiles_equal_primary.png"), ("equal_time", "convergence_profiles_equal_time.png")):
        plt.figure(figsize=(8, 4.8))
        for solver in SOLVERS:
            vals = [float(row["time_to_within_2pct_s"]) for row in convergence if row["mode"] == mode and row["solver"] == solver and row["time_to_within_2pct_s"] not in (None, "")]
            vals.sort(); y = [(index + 1) / len(vals) for index in range(len(vals))]
            plt.plot(vals, y, label=solver)
        plt.xlabel("Time to within 2% of final best (s)"); plt.ylabel("Empirical cumulative fraction"); plt.legend(); save(name)
    coll_index = {(row["instance_id"], row["mode"], row["solver_seed"], row["solver"]): row for row in collision_rows}
    coll_pairs = sorted({(row["instance_id"], row["mode"], row["solver_seed"]) for row in collision_rows})
    coll_gaps = [relative_gap(numeric(coll_index[(*key, "eprk")], "collision_adjusted_makespan"), numeric(coll_index[(*key, "hga_atomic")], "collision_adjusted_makespan")) for key in coll_pairs]
    plt.figure(figsize=(7, 4.5)); plt.boxplot(coll_gaps, tick_labels=["Collision-adjusted gap"]); plt.axhline(0, color="black", linewidth=.8); plt.ylabel("Relative gap %"); save("collision_adjusted_makespan_gap.png")
    def focused_plot(predicate, name, title):
        subset = [row for row in effects if row["mode"] == "equal_time" and predicate(row)]
        plt.figure(figsize=(9, 4.8)); plt.axhline(0, color="black", linewidth=.8)
        plt.bar([row["instance_id"] for row in subset], [float(row["median_relative_gap_percent"]) for row in subset])
        plt.xticks(rotation=40, ha="right"); plt.ylabel("Median relative gap %"); plt.title(title); save(name)
    focused_plot(lambda row: row["family"] == "real", "real_instance_comparison.png", "Real instances")
    for family_name, file_name in (("zero_travel_chain", "zero_travel_family_comparison.png"),
                                   ("long_weld_rich", "long_weld_family_comparison.png"),
                                   ("boundary_dense", "boundary_dense_family_comparison.png"),
                                   ("load_skewed", "load_skewed_family_comparison.png")):
        focused_plot(lambda row, target=family_name: row["family"] == target, file_name, family_name)


def main() -> dict[str, Any]:
    raw_rows = read_csv(RAW)
    collision_rows = read_csv(COLLISION)
    validate_raw(raw_rows, collision_rows)
    protocol = json.loads(PROTOCOL.read_text(encoding="utf-8"))
    tests, effects, bootstraps, wtl, instance = build_instance_statistics(raw_rows)
    holm_rows = [{key: row[key] for key in ("instance_id", "family", "nominal_weld_count", "mode", "p_raw", "p_holm", "holm_significant_direction")} for row in tests]
    family = group_gap_summary(effects, "family")
    size_effects = []
    for row in effects:
        size_effects.append({**row, "nominal_size_group": "real" if row["family"] == "real" else str(row["nominal_weld_count"])})
    size = group_gap_summary(size_effects, "nominal_size_group")
    overall = overall_statistics(effects)
    instance_metric_gaps, endpoint_summary = endpoint_statistics(raw_rows)
    collision_instance, collision_overall = collision_summary(collision_rows, raw_rows)
    work = work_amplification(raw_rows)
    convergence = convergence_summary(raw_rows)
    qualification_result = qualification(protocol, effects, tests, overall, family, size, raw_rows,
                                         collision_rows, collision_overall, work)
    outputs = {
        "paired_tests_by_instance.csv": tests,
        "holm_adjusted_tests.csv": holm_rows,
        "effect_sizes.csv": effects,
        "bootstrap_confidence_intervals.csv": bootstraps,
        "win_tie_loss.csv": wtl,
        "instance_summary.csv": instance,
        "family_summary.csv": family,
        "size_summary.csv": size,
        "overall_summary.csv": overall,
        "instance_metric_gaps.csv": instance_metric_gaps,
        "endpoint_summary.csv": endpoint_summary,
        "work_amplification.csv": work,
        "convergence_summary.csv": convergence,
        "collision_instance_summary.csv": collision_instance,
    }
    for name, values in outputs.items():
        write_csv(OUT / name, values)
    digest = write_json(OUT / "qualification_gates.json", qualification_result)
    (OUT / "qualification_gates.sha256").write_text(digest + "\n", encoding="ascii")
    write_json(OUT / "collision_overall_summary.json", collision_overall)
    plots(raw_rows, effects, family, size, wtl, collision_rows, convergence)
    print(json.dumps({"raw_run_count": len(raw_rows), "qualified": qualification_result["eprk_main_algorithm_qualified"],
                      "qualification_sha256": digest}, ensure_ascii=False, indent=2))
    return qualification_result


if __name__ == "__main__":
    main()
