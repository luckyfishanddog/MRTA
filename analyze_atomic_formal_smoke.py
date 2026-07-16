"""Executable statistical-tool smoke for the 16-run atomic smoke matrix."""

from __future__ import annotations

import csv
import itertools
import json
import random
import statistics
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


ROOT = Path(__file__).resolve().parent
OUT = ROOT / "formal_atomic" / "remediation"
RUNS = OUT / "smoke_runs.csv"
STATS_JSON = OUT / "smoke_statistics.json"
STATS_CSV = OUT / "smoke_statistics.csv"
PLOT = OUT / "smoke_fitness_pairs.png"


def holm_adjust(p_values: list[float]) -> list[float]:
    order = sorted(range(len(p_values)), key=p_values.__getitem__)
    adjusted = [0.0] * len(p_values)
    running = 0.0
    count = len(p_values)
    for rank, index in enumerate(order):
        running = max(running, min(1.0, (count - rank) * p_values[index]))
        adjusted[index] = running
    return adjusted


def exact_wilcoxon_two_sided(differences: list[float]) -> float:
    nonzero = [value for value in differences if value != 0.0]
    if not nonzero:
        return 1.0
    ordered = sorted(enumerate(nonzero), key=lambda item: abs(item[1]))
    ranks = [0.0] * len(nonzero)
    position = 0
    while position < len(ordered):
        end = position + 1
        while end < len(ordered) and abs(ordered[end][1]) == abs(ordered[position][1]):
            end += 1
        average_rank = ((position + 1) + end) / 2.0
        for original_index, _ in ordered[position:end]:
            ranks[original_index] = average_rank
        position = end
    total = sum(ranks)
    observed_positive = sum(rank for rank, value in zip(ranks, nonzero) if value > 0)
    observed = min(observed_positive, total - observed_positive)
    outcomes = []
    for signs in itertools.product((False, True), repeat=len(ranks)):
        positive = sum(rank for rank, sign in zip(ranks, signs) if sign)
        outcomes.append(min(positive, total - positive))
    return sum(value <= observed + 1e-15 for value in outcomes) / len(outcomes)


def quantile(sorted_values: list[float], probability: float) -> float:
    location = probability * (len(sorted_values) - 1)
    lower = int(location)
    upper = min(lower + 1, len(sorted_values) - 1)
    fraction = location - lower
    return sorted_values[lower] * (1.0 - fraction) + sorted_values[upper] * fraction


def main() -> dict:
    with RUNS.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    values = {
        (row["instance_id"], row["mode"], int(row["seed"]), row["solver"]): float(row["fitness"])
        for row in rows
    }
    groups = sorted({(row["instance_id"], row["mode"]) for row in rows})
    rng = random.Random(20260716)
    results = []
    p_values = []
    for instance, mode in groups:
        eprk = [values[(instance, mode, seed, "eprk")] for seed in (194, 195)]
        hga = [values[(instance, mode, seed, "hga_atomic")] for seed in (194, 195)]
        delta = [left - right for left, right in zip(eprk, hga)]
        samples = sorted(statistics.mean(rng.choices(delta, k=len(delta))) for _ in range(10000))
        p_value = exact_wilcoxon_two_sided(delta)
        p_values.append(p_value)
        results.append({
            "instance_id": instance,
            "mode": mode,
            "paired_seed_count": 2,
            "mean_fitness_delta_eprk_minus_hga": statistics.mean(delta),
            "eprk_win_count_lower_is_better": sum(value < 0 for value in delta),
            "bootstrap_resamples": 10000,
            "bootstrap_mean_delta_ci_low": quantile(samples, 0.025),
            "bootstrap_mean_delta_ci_high": quantile(samples, 0.975),
            "wilcoxon_p_raw": p_value,
        })
    adjusted = holm_adjust(p_values)
    for row, value in zip(results, adjusted):
        row["wilcoxon_p_holm"] = value
    with STATS_CSV.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(results[0]))
        writer.writeheader()
        writer.writerows(results)
    payload = {
        "status": "tool_smoke_passed",
        "qualification_use_forbidden": True,
        "paired_statistics_executed": True,
        "bootstrap_executed": True,
        "wilcoxon_executed": True,
        "holm_correction_executed": True,
        "plot_script_executed": True,
        "groups": results,
    }
    STATS_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    labels = [f"{row['instance_id']}\n{row['mode']}" for row in results]
    deltas = [row["mean_fitness_delta_eprk_minus_hga"] for row in results]
    fig, axis = plt.subplots(figsize=(10, 4.8))
    axis.axhline(0.0, color="black", linewidth=0.8)
    axis.bar(labels, deltas, color=["#4472C4" if value <= 0 else "#ED7D31" for value in deltas])
    axis.set_ylabel("Mean paired fitness delta (EPRK - HGA)")
    axis.set_title("Smoke-only paired fitness deltas (not qualification evidence)")
    axis.tick_params(axis="x", labelsize=8)
    fig.tight_layout()
    fig.savefig(PLOT, dpi=160)
    plt.close(fig)
    print(json.dumps({"status": payload["status"], "group_count": len(results), "plot": PLOT.name}))
    return payload


if __name__ == "__main__":
    main()
