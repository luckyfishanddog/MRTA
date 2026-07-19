"""Independent randomized correctness gate for CPLR phase 1."""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
import time
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, Sequence

from continuous_lineage_decoder import CPLRChromosome, CPLRDecoderConfig
from cplr_standard_certifier import certify_candidate
from generate_welds import PLATFORM_W_M, load_frozen_weld_instance
from lineage_presplit import build_lineages
from reachable_side_keys import apply_reachable_side_index, build_reachable_side_index


ROOT = Path(__file__).resolve().parents[1]
INSTANCE_NAMES = ("w30", "w45", "w60")
DEFAULT_OUTPUT = ROOT / "results" / "cplr_phase1"
SEED = 20260719
CATEGORIES = (
    "interior_random",
    "endpoint_exact",
    "endpoint_left_perturb",
    "endpoint_right_perturb",
    "upper_only_change",
    "lower_only_change",
    "both_change",
    "all_keys_tied",
    "upper_extreme",
    "lower_extreme",
)


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _prepare() -> list[Dict[str, Any]]:
    contexts = []
    lineage_rows = []
    for name in INSTANCE_NAMES:
        path = ROOT / "data" / "instances" / "selected" / f"instance_{name}.xlsx"
        welds, metadata = load_frozen_weld_instance(str(path))
        config = CPLRDecoderConfig()
        built = build_lineages(welds, eps=config.eps)
        index = build_reachable_side_index(built.lineages, config)
        lineages = apply_reachable_side_index(built.lineages, index)
        x_events = sorted(
            {0.0, float(PLATFORM_W_M)}
            | {float(p[0]) for lineage in lineages for p in (lineage.start_point, lineage.end_point)}
        )
        contexts.append(
            {
                "name": name,
                "welds": welds,
                "metadata": metadata,
                "config": config,
                "built": built,
                "index": index,
                "lineages": lineages,
                "events": x_events,
            }
        )
        lineage_rows.append(
            {
                "instance": name,
                "instance_hash": metadata.get("instance_hash"),
                "original_weld_count": len(welds),
                "lineage_count": len(lineages),
                "horizontal_split_parent_count": built.horizontal_split_parent_count,
                "original_total_length": built.original_total_length,
                "lineage_total_length": built.lineage_total_length,
                "length_error": built.length_error,
                "unpruned_side_key_count": index.total_unpruned_key_count,
                "reachable_side_key_count": len(index.keys),
                "pruned_side_key_count": index.pruned_key_count,
                "pruning_ratio": index.pruning_ratio,
            }
        )
    _write_json(
        DEFAULT_OUTPUT / "lineage_summary.json",
        {"schema_version": 1, "instances": lineage_rows},
    )
    return contexts


def _event_gene(context: MappingLike, iteration: int, direction: int) -> float:
    events = context["events"]
    value = events[(iteration // len(CATEGORIES)) % len(events)]
    if direction:
        # Exercise the frozen splitter's actual tolerance neighbourhood, not
        # merely one IEEE-754 ULP around the event.
        value += direction * 2.0 * float(context["config"].eps)
    return min(1.0, max(0.0, value / float(PLATFORM_W_M)))


MappingLike = Dict[str, Any]


def _genes(
    context: MappingLike, category: str, iteration: int, rng: random.Random
) -> tuple[float, float]:
    random_upper, random_lower = rng.random(), rng.random()
    if category == "endpoint_exact":
        event = _event_gene(context, iteration, 0)
        return event, event
    if category == "endpoint_left_perturb":
        event = _event_gene(context, iteration, -1)
        return event, event
    if category == "endpoint_right_perturb":
        event = _event_gene(context, iteration, 1)
        return event, event
    if category == "upper_only_change":
        return random_upper, 0.5
    if category == "lower_only_change":
        return 0.5, random_lower
    if category == "upper_extreme":
        return float((iteration // len(CATEGORIES)) % 2), random_lower
    if category == "lower_extreme":
        return random_upper, float((iteration // len(CATEGORIES)) % 2)
    return random_upper, random_lower


def run_validation(candidate_count: int, output_dir: Path, seed: int = SEED) -> Dict[str, Any]:
    if candidate_count < 1:
        raise ValueError("candidate_count must be positive")
    global DEFAULT_OUTPUT
    DEFAULT_OUTPUT = output_dir
    contexts = _prepare()
    rng = random.Random(seed)
    categories: Counter[str] = Counter()
    per_instance: Counter[str] = Counter()
    failure_count = 0
    exception_count = 0
    length_failure_count = 0
    duplicate_or_omission_count = 0
    direction_difference_count = 0
    first_failure = None
    failure_records_written = 0
    failures_path = output_dir / "randomized_equivalence_failures.jsonl"
    failures_path.parent.mkdir(parents=True, exist_ok=True)
    start = time.perf_counter()
    with failures_path.open("w", encoding="utf-8") as failure_stream:
        for iteration in range(candidate_count):
            context = contexts[iteration % len(contexts)]
            category = CATEGORIES[iteration % len(CATEGORIES)]
            categories[category] += 1
            per_instance[context["name"]] += 1
            upper, lower = _genes(context, category, iteration, rng)
            if category == "all_keys_tied":
                key_values = {key: 0.5 for key in context["index"].keys}
            else:
                key_values = {key: rng.random() for key in context["index"].keys}
            chromosome = CPLRChromosome.from_mapping(upper, lower, key_values)
            certification = None
            caught = None
            try:
                certification = certify_candidate(
                    context["welds"],
                    chromosome,
                    context["config"],
                    prepared_lineage_build=context["built"],
                    prepared_lineages=context["lineages"],
                )
            except ValueError as exc:
                caught = str(exc)
            if caught is not None or not certification.passed:
                failure_count += 1
                exception_count += int(caught is not None)
                duplicate_or_omission_count += int(caught is not None)
                if certification is not None:
                    length_failure_count += int(
                        not certification.horizontal_length_conserved
                        or not certification.vertical_length_conserved
                    )
                    duplicate_or_omission_count += int(
                        not certification.no_duplicates or not certification.no_omissions
                    )
                    direction_difference_count += int(
                        not certification.direction_flags_equal
                    )
                record = {
                    "iteration": iteration,
                    "seed": seed,
                    "instance": context["name"],
                    "category": category,
                    "upper_boundary_gene": upper,
                    "lower_boundary_gene": lower,
                    "x_up": upper * float(PLATFORM_W_M),
                    "x_low": lower * float(PLATFORM_W_M),
                    "side_keys": [
                        [key.lineage_id, key.side, value]
                        for key, value in chromosome.side_keys
                    ],
                    "failures": [caught] if caught is not None else list(certification.failures),
                    "route_signatures": [] if certification is None else [
                        [[key.lineage_id, key.side] for key in route]
                        for route in certification.decoded_state.route_signatures
                    ],
                }
                if failure_records_written < 1000:
                    failure_stream.write(json.dumps(record, ensure_ascii=False) + "\n")
                    failure_records_written += 1
                if first_failure is None:
                    first_failure = record
            if (iteration + 1) % 10000 == 0:
                print(f"validated={iteration + 1} failures={failure_count}", flush=True)
    elapsed = time.perf_counter() - start
    summary = {
        "schema_version": 1,
        "seed": seed,
        "candidate_count": candidate_count,
        "structural_difference_count": failure_count,
        "decoder_exception_count": exception_count,
        "length_conservation_failure_count": length_failure_count,
        "duplicate_or_omission_failure_count": duplicate_or_omission_count,
        "direction_difference_count": direction_difference_count,
        "final_certification_failure_count": failure_count,
        "failure_records_written": failure_records_written,
        "failure_records_truncated": failure_count > failure_records_written,
        "first_failure": first_failure,
        "category_counts": dict(categories),
        "instance_counts": dict(per_instance),
        "elapsed_seconds": elapsed,
        "candidates_per_second": candidate_count / elapsed,
        "passed": failure_count == 0,
    }
    _write_json(output_dir / "randomized_equivalence_summary.json", summary)
    return summary


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidates", type=int, default=100000)
    parser.add_argument("--seed", type=int, default=SEED)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    summary = run_validation(args.candidates, args.output_dir, args.seed)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
