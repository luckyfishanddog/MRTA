"""Generate and validate atomic_benchmark_suite_v1.

The generator is deterministic by construction.  Solver seeds are never used
for geometry.  Rejected geometries are regenerated only with seed + 10000 and
the rejection reason is retained.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import random
import shutil
from collections import Counter, deque
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from atomic_problem_core import (
    ATOMIC_MODEL_ID,
    AtomicWeld,
    atomic_payload_hash,
    canonical_json,
    generate_boundary_events,
    load_atomic_instance,
    sha256_file,
    sha256_json,
    validate_event_constancy,
)
from atomic_route_evaluator import AtomicRouteEvaluator, normalization_hash
from atomic_weld_preprocessor import preprocess_welds
from build_atomic_normalization import build_spec
from generate_welds import Weld, load_frozen_weld_instance
from MRTA_EPRK_MA import EPRKConfig, EPRKMASolver
from MRTA_HGA_ATOMIC_CONTROL import HGAAtomicSolver


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "formal_atomic"
INSTANCE_ROOT = OUT / "instances"
RAW_DIR = INSTANCE_ROOT / "raw"
ATOMIC_DIR = INSTANCE_ROOT / "atomic"
NORMALIZATION_DIR = INSTANCE_ROOT / "normalization"
SUITE_ID = "atomic_benchmark_suite_v1"
FAMILIES = (
    "uniform",
    "clustered",
    "boundary_dense",
    "long_weld_rich",
    "zero_travel_chain",
    "load_skewed",
)
SIZES = (30, 60, 100)
REPLICATES = (1, 2)
Z = 0.1
PLATFORM_W = 20.0
PLATFORM_H = 12.0
ENDPOINT_TOL = 1e-9


def _round(value: float) -> float:
    return round(float(value), 12)


def _weld_payload(weld: Weld) -> dict[str, Any]:
    return {
        "weld_id": str(weld.id),
        "start": [_round(x) for x in weld.start_point()],
        "end": [_round(x) for x in weld.end_point()],
        "euclidean_length_m": math.dist(weld.start_point(), weld.end_point()),
    }


def _make_weld(instance_id: str, index: int, start: Sequence[float], end: Sequence[float]) -> Weld:
    start = tuple(_round(x) for x in start)
    end = tuple(_round(x) for x in end)
    if not all(math.isfinite(x) for x in (*start, *end)):
        raise ValueError("non-finite coordinate")
    if not all(0.0 <= x <= PLATFORM_W for x in (start[0], end[0])):
        raise ValueError("x outside platform")
    if not all(0.0 <= y <= PLATFORM_H for y in (start[1], end[1])):
        raise ValueError("y outside platform")
    if math.dist(start, end) <= 1e-12:
        raise ValueError("zero-length weld")
    return Weld(f"{instance_id}__weld_{index:04d}", *start, *end)


def _bounded_segment(
    instance_id: str,
    index: int,
    rng: random.Random,
    length: float,
    angle: float,
    *,
    center: tuple[float, float] | None = None,
    y_half: str | None = None,
) -> Weld:
    dx = 0.5 * length * math.cos(angle)
    dy = 0.5 * length * math.sin(angle)
    if abs(dx) >= PLATFORM_W / 2 or abs(dy) >= PLATFORM_H / 2:
        raise ValueError("segment cannot fit platform")
    x_lo, x_hi = abs(dx) + 0.05, PLATFORM_W - abs(dx) - 0.05
    if y_half == "upper":
        y_lo, y_hi = 6.15 + abs(dy), PLATFORM_H - abs(dy) - 0.05
    elif y_half == "lower":
        y_lo, y_hi = 0.05 + abs(dy), 5.85 - abs(dy)
    else:
        y_lo, y_hi = abs(dy) + 0.05, PLATFORM_H - abs(dy) - 0.05
    if x_lo >= x_hi or y_lo >= y_hi:
        raise ValueError("segment margins leave no feasible center")
    if center is None:
        x, y = rng.uniform(x_lo, x_hi), rng.uniform(y_lo, y_hi)
    else:
        x = min(x_hi, max(x_lo, center[0]))
        y = min(y_hi, max(y_lo, center[1]))
    return _make_weld(instance_id, index, (x - dx, y - dy, Z), (x + dx, y + dy, Z))


def _anchor_welds(instance_id: str) -> list[Weld]:
    anchors: list[Weld] = []
    for y in (3.0, 9.0):
        for x in (2.0, 9.0, 16.0):
            i = len(anchors)
            anchors.append(_make_weld(instance_id, i, (x, y, Z), (x + 0.8, y + 0.15, Z)))
    return anchors


def _uniform(instance_id: str, n: int, rng: random.Random) -> list[Weld]:
    welds = _anchor_welds(instance_id)
    bins = 8
    while len(welds) < n:
        i = len(welds)
        angle = ((i % bins) + rng.random()) * math.pi / bins
        length = rng.uniform(0.6, 3.2)
        welds.append(_bounded_segment(instance_id, i, rng, length, angle))
    return welds


CLUSTER_CENTERS = ((3.0, 2.5), (6.0, 9.0), (13.5, 3.0), (16.5, 9.5))


def _clustered(instance_id: str, n: int, rng: random.Random) -> list[Weld]:
    welds: list[Weld] = []
    clustered_count = math.ceil(0.8 * n)
    while len(welds) < clustered_count:
        i = len(welds)
        cx, cy = CLUSTER_CENTERS[i % len(CLUSTER_CENTERS)]
        center = (cx + rng.gauss(0, 0.65), cy + rng.gauss(0, 0.55))
        half = "upper" if cy > 6 else "lower"
        welds.append(_bounded_segment(instance_id, i, rng, rng.uniform(0.5, 2.0), rng.random() * math.pi,
                                      center=center, y_half=half))
    while len(welds) < n:
        i = len(welds)
        welds.append(_bounded_segment(instance_id, i, rng, rng.uniform(0.6, 2.8), rng.random() * math.pi))
    return welds


BOUNDARY_X = (5.0, 10.0, 15.0)


def _boundary_dense(instance_id: str, n: int, rng: random.Random) -> list[Weld]:
    welds: list[Weld] = []
    dense_count = math.ceil(0.5 * n)
    while len(welds) < dense_count:
        i = len(welds)
        boundary = BOUNDARY_X[i % len(BOUNDARY_X)]
        half = "upper" if i % 2 else "lower"
        y = rng.uniform(6.5, 11.5) if half == "upper" else rng.uniform(0.5, 5.5)
        span = rng.uniform(0.5, 2.2)
        side = -1 if (i // len(BOUNDARY_X)) % 2 == 0 else 1
        near = boundary + rng.uniform(-0.08, 0.08)
        far = near + side * span
        if not 0.1 <= far <= 19.9:
            far = near - side * span
        dy = rng.uniform(-0.35, 0.35)
        welds.append(_make_weld(instance_id, i, (near, y, Z), (far, min(11.8, max(0.2, y + dy)), Z)))
    while len(welds) < n:
        i = len(welds)
        welds.append(_bounded_segment(instance_id, i, rng, rng.uniform(0.6, 2.8), rng.random() * math.pi,
                                      y_half="upper" if i % 2 else "lower"))
    return welds


def _long_weld_rich(instance_id: str, n: int, rng: random.Random) -> list[Weld]:
    welds: list[Weld] = []
    long_count = math.ceil(0.36 * n)
    # Long segments live inside three deterministic x bands.  The gaps between
    # bands remain legal boundary-event cells instead of being covered by a
    # random collection of long segments.
    bands = ((0.20, 6.35), (6.85, 13.15), (13.65, 19.80))
    while len(welds) < long_count:
        i = len(welds)
        left, right = bands[(i // 2) % len(bands)]
        horizontal = rng.uniform(5.15, min(5.80, right - left - 0.10))
        half = "upper" if i % 2 else "lower"
        start_x = rng.uniform(left + 0.03, right - horizontal - 0.03)
        y = rng.uniform(6.7, 11.3) if half == "upper" else rng.uniform(0.7, 5.3)
        dy = rng.uniform(-0.35, 0.35)
        welds.append(_make_weld(instance_id, i, (start_x, y, Z),
                                (start_x + horizontal, y + dy, Z)))
    while len(welds) < n:
        i = len(welds)
        left, right = bands[(i // 2) % len(bands)]
        half = "upper" if i % 2 else "lower"
        center_y = rng.uniform(6.7, 11.3) if half == "upper" else rng.uniform(0.7, 5.3)
        welds.append(_bounded_segment(
            instance_id, i, rng, rng.uniform(0.6, 2.2), rng.random() * math.pi,
            center=(rng.uniform(left + 1.1, right - 1.1), center_y), y_half=half,
        ))
    return welds


def _zero_travel_chain(instance_id: str, n: int, rng: random.Random) -> list[Weld]:
    welds: list[Weld] = []
    chain_lengths = (3, 4, 5, 8)
    starts = ((1.0, 8.4), (11.0, 8.8), (1.0, 2.4), (11.0, 2.8))
    for chain_index, (count, start) in enumerate(zip(chain_lengths, starts)):
        point = (start[0], start[1], Z)
        for step in range(count):
            i = len(welds)
            dx = 0.82 + 0.05 * ((step + chain_index) % 3)
            dy = 0.18 * (1 if (step + chain_index) % 2 == 0 else -1)
            nxt = (_round(point[0] + dx), _round(point[1] + dy), Z)
            welds.append(_make_weld(instance_id, i, point, nxt))
            point = nxt
    while len(welds) < n:
        i = len(welds)
        welds.append(_bounded_segment(instance_id, i, rng, rng.uniform(0.7, 2.5), rng.random() * math.pi,
                                      y_half="upper" if i % 2 else "lower"))
    return welds


def _load_skewed(instance_id: str, n: int, rng: random.Random) -> list[Weld]:
    welds: list[Weld] = []
    upper_count = round(0.60 * n)
    for i in range(n):
        upper = i < upper_count
        half = "upper" if upper else "lower"
        length = rng.uniform(1.8, 2.5) if upper else rng.uniform(1.25, 1.75)
        angle = rng.uniform(-0.65, 0.65)
        if upper:
            center_x = rng.uniform(1.5, 8.5) if i % 5 else rng.uniform(12.5, 18.0)
            center_y = rng.uniform(7.0, 11.0)
        else:
            center_x = rng.uniform(11.5, 18.5) if i % 5 else rng.uniform(2.0, 7.5)
            center_y = rng.uniform(1.0, 5.0)
        welds.append(_bounded_segment(instance_id, i, rng, length, angle, center=(center_x, center_y), y_half=half))
    return welds


GENERATORS = {
    "uniform": _uniform,
    "clustered": _clustered,
    "boundary_dense": _boundary_dense,
    "long_weld_rich": _long_weld_rich,
    "zero_travel_chain": _zero_travel_chain,
    "load_skewed": _load_skewed,
}


def _source_hash(instance_id: str, welds: Sequence[Weld]) -> str:
    return sha256_json({
        "suite_id": SUITE_ID,
        "instance_id": instance_id,
        "welds": [_weld_payload(weld) for weld in sorted(welds, key=lambda item: str(item.id))],
    })


def _raw_payload(instance_id: str, family: str, n: int, replicate: int, seed: int | None,
                 welds: Sequence[Weld], source_hash: str, source_type: str) -> dict[str, Any]:
    return {
        "schema_version": "1.0.0",
        "suite_id": SUITE_ID,
        "instance_id": instance_id,
        "family": family,
        "nominal_weld_count": n,
        "replicate": replicate,
        "generation_seed": seed,
        "source_type": source_type,
        "source_hash": source_hash,
        "platform": {"width_m": PLATFORM_W, "height_m": PLATFORM_H},
        "welds": [_weld_payload(weld) for weld in sorted(welds, key=lambda item: str(item.id))],
    }


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _atomic_payload(instance_id: str, family: str, raw_path: Path, raw_hash: str,
                    welds: Sequence[Weld]) -> dict[str, Any]:
    atoms = preprocess_welds(welds, raw_hash)
    upper = generate_boundary_events(atoms, "upper")
    lower = generate_boundary_events(atoms, "lower")
    metadata = {
        "model_id": ATOMIC_MODEL_ID,
        "model_version": "1.0.0",
        "benchmark_suite_id": SUITE_ID,
        "family": family,
        "source_instance_path": raw_path.relative_to(ROOT).as_posix(),
        "source_instance_file_sha256": sha256_file(raw_path),
        "source_instance_hash": raw_hash,
        "source_weld_count": len(welds),
        "atomic_weld_count": len(atoms),
        "fixed_y_cut_m": 6.0,
        "lmax_m": 5.0,
        "lmin_m": 1.0,
        "search_time_splitting": False,
        "setup_time_s": 0.0,
        "post_time_s": 0.0,
    }
    return {
        "name": instance_id,
        "metadata": metadata,
        "atomic_instance_hash": atomic_payload_hash(atoms, metadata),
        "atomic_welds": [atom.to_dict() for atom in atoms],
        "boundary_events": {
            "upper": [event.to_dict() for event in upper],
            "lower": [event.to_dict() for event in lower],
        },
    }


def _shared_endpoint_graph(welds: Sequence[Weld]) -> tuple[int, int, int]:
    adjacency = {str(w.id): set() for w in welds}
    connections = 0
    for i, first in enumerate(welds):
        for second in welds[i + 1:]:
            if min(math.dist(a, b) for a in first.endpoints() for b in second.endpoints()) <= ENDPOINT_TOL:
                adjacency[str(first.id)].add(str(second.id))
                adjacency[str(second.id)].add(str(first.id))
                connections += 1
    visited: set[str] = set()
    components: list[int] = []
    for node in sorted(adjacency):
        if node in visited:
            continue
        queue = deque([node]); visited.add(node); size = 0
        while queue:
            current = queue.popleft(); size += 1
            for nxt in adjacency[current]:
                if nxt not in visited:
                    visited.add(nxt); queue.append(nxt)
        components.append(size)
    return connections, max(components, default=0), sum(size >= 3 for size in components)


def _instance_statistics(instance_id: str, family: str, n: int, replicate: int,
                         generation_seed: int | None, welds: Sequence[Weld], atomic_path: Path,
                         raw_hash: str, normalization_sha: str) -> dict[str, Any]:
    instance = load_atomic_instance(atomic_path)
    atoms = instance.atomic_welds
    orientations = Counter()
    for weld in welds:
        dx = abs(weld.x2 - weld.x1); dy = abs(weld.y2 - weld.y1)
        orientations["horizontal" if dy <= 1e-9 else "vertical" if dx <= 1e-9 else "diagonal"] += 1
    connections, max_chain, chain_components = _shared_endpoint_graph(welds)
    quadrants = Counter()
    for weld in welds:
        x, y, _ = weld.midpoint()
        quadrants[("left" if x < 10 else "right") + "_" + ("lower" if y < 6 else "upper")] += 1
    upper_time = sum(atom.weld_time_s for atom in atoms if atom.half_region == "upper")
    lower_time = sum(atom.weld_time_s for atom in atoms if atom.half_region == "lower")
    left_length = sum(weld.length for weld in welds if weld.midpoint()[0] < 10)
    right_length = sum(weld.length for weld in welds if weld.midpoint()[0] >= 10)
    y6_parents = {atom.parent_original_id for atom in atoms if atom.is_y6_split}
    long_parents = {atom.parent_original_id for atom in atoms if atom.is_long_weld_split}
    return {
        "instance_id": instance_id,
        "family": family,
        "nominal_weld_count": n,
        "replicate": replicate,
        "generation_seed": generation_seed,
        "raw_weld_count": len(welds),
        "atomic_weld_count": len(atoms),
        "y6_split_weld_count": len(y6_parents),
        "y6_split_atomic_count": sum(atom.is_y6_split for atom in atoms),
        "long_weld_split_count": len(long_parents),
        "long_weld_split_atomic_count": sum(atom.is_long_weld_split for atom in atoms),
        "atomic_count_increase_ratio": (len(atoms) - len(welds)) / max(1, len(welds)),
        "upper_event_count": len(instance.upper_events),
        "lower_event_count": len(instance.lower_events),
        "total_weld_length_m": sum(weld.length for weld in welds),
        "total_weld_time_s": sum(atom.weld_time_s for atom in atoms),
        "horizontal_ratio": orientations["horizontal"] / len(welds),
        "vertical_ratio": orientations["vertical"] / len(welds),
        "diagonal_ratio": orientations["diagonal"] / len(welds),
        "long_weld_ratio": sum(abs(weld.x2 - weld.x1) > 5.0 for weld in welds) / len(welds),
        "zero_travel_connection_count": connections,
        "max_zero_travel_chain_length": max_chain,
        "zero_travel_chain_component_count": chain_components,
        "quadrant_left_lower": quadrants["left_lower"],
        "quadrant_right_lower": quadrants["right_lower"],
        "quadrant_left_upper": quadrants["left_upper"],
        "quadrant_right_upper": quadrants["right_upper"],
        "upper_lower_workload_ratio": upper_time / max(1e-12, lower_time),
        "left_right_workload_ratio": left_length / max(1e-12, right_length),
        "source_hash": raw_hash,
        "atomic_hash": instance.atomic_instance_hash,
        "normalization_hash": normalization_sha,
    }


def _family_validation(family: str, welds: Sequence[Weld], stats: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if family == "uniform":
        quadrant_values = [int(stats[key]) for key in (
            "quadrant_left_lower", "quadrant_right_lower", "quadrant_left_upper", "quadrant_right_upper")]
        if min(quadrant_values) < max(2, len(welds) // 10):
            errors.append("uniform quadrant coverage below threshold")
        angle_bins = {int((math.atan2(w.y2 - w.y1, w.x2 - w.x1) % math.pi) / math.pi * 8) % 8 for w in welds}
        if len(angle_bins) < 6:
            errors.append("uniform orientation coverage below six bins")
    elif family == "clustered":
        close = sum(min(math.dist(w.midpoint()[:2], center) for center in CLUSTER_CENTERS) <= 2.5 for w in welds)
        if close / len(welds) < 0.70:
            errors.append("cluster membership below 70 percent")
    elif family == "boundary_dense":
        close = sum(min(abs(min(w.x1, w.x2) - x), abs(max(w.x1, w.x2) - x)) <= 0.20
                    for w in welds for x in BOUNDARY_X)
        # Count welds, not weld-boundary pairs.
        close = sum(any(min(abs(min(w.x1, w.x2) - x), abs(max(w.x1, w.x2) - x)) <= 0.20 for x in BOUNDARY_X)
                    for w in welds)
        if close / len(welds) < 0.40:
            errors.append("boundary-dense ratio below 40 percent")
    elif family == "long_weld_rich":
        if float(stats["long_weld_ratio"]) < 0.30:
            errors.append("long-weld ratio below 30 percent")
    elif family == "zero_travel_chain":
        if int(stats["zero_travel_chain_component_count"]) < 4:
            errors.append("fewer than four zero-travel chains")
        if int(stats["max_zero_travel_chain_length"]) < 8:
            errors.append("zero-travel chains do not reach length eight")
    elif family == "load_skewed":
        ratio = float(stats["upper_lower_workload_ratio"])
        ratio = max(ratio, 1.0 / max(1e-12, ratio))
        if not 1.5 <= ratio <= 2.5:
            errors.append("upper/lower workload ratio outside [1.5,2.5]")
        lr = float(stats["left_right_workload_ratio"])
        if 0.80 <= lr <= 1.25:
            errors.append("left/right workload is not visibly skewed")
    return errors


def _general_validation(atomic_path: Path, stats: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    instance = load_atomic_instance(atomic_path)
    if len(instance.upper_events) < 3 or len(instance.lower_events) < 3:
        errors.append("fewer than three events in a half")
    if not any(all(route for route in (
        upper.left_atomic_ids, upper.right_atomic_ids, lower.left_atomic_ids, lower.right_atomic_ids
    )) for upper in instance.upper_events for lower in instance.lower_events):
        errors.append("no event pair gives all four robots tasks")
    ids = [atom.id for atom in instance.atomic_welds]
    if len(ids) != len(set(ids)):
        errors.append("duplicate atomic IDs")
    if any(math.dist(atom.start, atom.end) <= 1e-12 for atom in instance.atomic_welds):
        errors.append("zero-length atom")
    if any(min(atom.start[0], atom.end[0]) < event.representative_x < max(atom.start[0], atom.end[0])
           for event in (*instance.upper_events, *instance.lower_events)
           for atom in instance.atomic_welds if atom.half_region == event.half_region):
        errors.append("event boundary crosses an atom")
    if not all(validate_event_constancy(event, instance.atomic_welds)
               for event in (*instance.upper_events, *instance.lower_events)):
        errors.append("event interval is not constant")
    if any(atom.is_long_weld_split and atom.horizontal_length_m < 1.0 - 1e-9 for atom in instance.atomic_welds):
        errors.append("long split segment below lmin")
    return errors


def _smoke(instance_path: Path, normalization_spec: Mapping[str, Any]) -> dict[str, bool]:
    instance = load_atomic_instance(instance_path)
    profile = json.loads((OUT / "eprk_ma_final_profile.json").read_text(encoding="utf-8"))
    config = EPRKConfig.from_mapping(profile["configuration"])
    eprk = EPRKMASolver(instance, normalization_spec, 190001, config, primary_budget=120)
    eprk_best = eprk.run()
    hga = HGAAtomicSolver(instance, normalization_spec, 190001, primary_budget=120)
    hga_best = hga.run()
    evaluator = AtomicRouteEvaluator(instance, normalization_spec)
    return {
        "eprk": eprk.primary_evaluations == 120 and abs(evaluator.evaluate_system(eprk_best.routes).fitness - eprk_best.fitness) <= 1e-10,
        "hga": hga.primary_evaluations == 120 and abs(evaluator.evaluate_system(hga_best.routes).fitness - hga_best.fitness) <= 1e-10,
    }


def _create_synthetic(family: str, n: int, replicate: int, seed: int,
                      rejections: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
    instance_id = f"{family}_n{n}_r{replicate}"
    attempted_seed = seed
    for attempt in range(2):
        rng = random.Random(attempted_seed)
        welds = GENERATORS[family](instance_id, n, rng)
        if len(welds) != n:
            raise AssertionError("generator returned wrong weld count")
        source_hash = _source_hash(instance_id, welds)
        raw_path = RAW_DIR / f"{instance_id}.json"
        raw = _raw_payload(instance_id, family, n, replicate, attempted_seed, welds, source_hash, "synthetic")
        _write_json(raw_path, raw)
        # Byte-level determinism check before accepting the geometry.
        repeat = GENERATORS[family](instance_id, n, random.Random(attempted_seed))
        repeat_raw = _raw_payload(instance_id, family, n, replicate, attempted_seed, repeat,
                                  _source_hash(instance_id, repeat), "synthetic")
        if json.dumps(raw, ensure_ascii=False, indent=2, sort_keys=True) != json.dumps(repeat_raw, ensure_ascii=False, indent=2, sort_keys=True):
            errors = ["same seed did not reproduce byte-identical raw payload"]
        else:
            atomic_path = ATOMIC_DIR / f"{instance_id}_atomic.json"
            _write_json(atomic_path, _atomic_payload(instance_id, family, raw_path, source_hash, welds))
            spec, _ = build_spec(atomic_path)
            normalization_path = NORMALIZATION_DIR / f"{instance_id}.json"
            _write_json(normalization_path, spec)
            stats = _instance_statistics(instance_id, family, n, replicate, attempted_seed, welds,
                                         atomic_path, source_hash, normalization_hash(spec))
            errors = _general_validation(atomic_path, stats) + _family_validation(family, welds, stats)
            smoke = {"eprk": False, "hga": False}
            if not errors:
                smoke = _smoke(atomic_path, spec)
                if not all(smoke.values()):
                    errors.append("solver smoke failed")
        if not errors:
            entry = {
                "instance_id": instance_id,
                "family": family,
                "nominal_weld_count": n,
                "replicate": replicate,
                "generation_seed": attempted_seed,
                "original_generation_seed": seed,
                "regenerated_after_rejection": attempt > 0,
                "raw_path": raw_path.relative_to(ROOT).as_posix(),
                "raw_file_sha256": sha256_file(raw_path),
                "source_hash": source_hash,
                "atomic_path": atomic_path.relative_to(ROOT).as_posix(),
                "atomic_file_sha256": sha256_file(atomic_path),
                "atomic_hash": stats["atomic_hash"],
                "normalization_path": normalization_path.relative_to(ROOT).as_posix(),
                "normalization_file_sha256": sha256_file(normalization_path),
                "normalization_hash": stats["normalization_hash"],
                "smoke_eprk_passed": smoke["eprk"],
                "smoke_hga_passed": smoke["hga"],
            }
            return entry, stats
        rejections.append({
            "instance_id": instance_id,
            "family": family,
            "nominal_weld_count": n,
            "replicate": replicate,
            "rejected_seed": attempted_seed,
            "next_seed": attempted_seed + 10000,
            "rejection_reason": "; ".join(errors),
        })
        attempted_seed += 10000
    raise RuntimeError(f"{instance_id} failed generation twice: {errors}")


def _create_real(name: str) -> tuple[dict[str, Any], dict[str, Any]]:
    instance_id = f"real_{name}"
    n = int(name[1:])
    xlsx = ROOT / f"data/instances/selected/instance_{name}.xlsx"
    welds, metadata = load_frozen_weld_instance(str(xlsx))
    source_hash = str(metadata["instance_hash"])
    raw_path = RAW_DIR / f"{instance_id}.json"
    _write_json(raw_path, _raw_payload(instance_id, "real", n, 1, None, welds, source_hash, "frozen_project_xlsx"))
    old_atomic = ROOT / f"data/instances/atomic_l5_l1/instance_{name}_atomic.json"
    atomic_path = ATOMIC_DIR / f"{instance_id}_atomic.json"
    atomic_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(old_atomic, atomic_path)
    from project_paths import resolve_project_path
    all_normalization = json.loads(resolve_project_path("atomic_normalization_spec.json").read_text(encoding="utf-8"))
    spec = all_normalization["instances"][name]
    normalization_path = NORMALIZATION_DIR / f"{instance_id}.json"
    _write_json(normalization_path, spec)
    stats = _instance_statistics(instance_id, "real", n, 1, None, welds, atomic_path,
                                 source_hash, normalization_hash(spec))
    errors = _general_validation(atomic_path, stats)
    smoke = _smoke(atomic_path, spec)
    if errors or not all(smoke.values()):
        raise RuntimeError(f"real instance validation failed: {instance_id}: {errors}, smoke={smoke}")
    entry = {
        "instance_id": instance_id,
        "family": "real",
        "nominal_weld_count": n,
        "replicate": 1,
        "generation_seed": None,
        "source_selected_xlsx": xlsx.relative_to(ROOT).as_posix(),
        "source_selected_xlsx_sha256": sha256_file(xlsx),
        "raw_path": raw_path.relative_to(ROOT).as_posix(),
        "raw_file_sha256": sha256_file(raw_path),
        "source_hash": source_hash,
        "atomic_path": atomic_path.relative_to(ROOT).as_posix(),
        "atomic_file_sha256": sha256_file(atomic_path),
        "atomic_hash": stats["atomic_hash"],
        "normalization_path": normalization_path.relative_to(ROOT).as_posix(),
        "normalization_file_sha256": sha256_file(normalization_path),
        "normalization_hash": stats["normalization_hash"],
        "smoke_eprk_passed": smoke["eprk"],
        "smoke_hga_passed": smoke["hga"],
    }
    return entry, stats


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]], fields: Sequence[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    names = list(fields or (list(rows[0]) if rows else []))
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=names, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def generate() -> dict[str, Any]:
    for directory in (RAW_DIR, ATOMIC_DIR, NORMALIZATION_DIR):
        directory.mkdir(parents=True, exist_ok=True)
    entries: list[dict[str, Any]] = []
    statistics_rows: list[dict[str, Any]] = []
    rejections: list[dict[str, Any]] = []
    for name in ("w30", "w45", "w60"):
        entry, stats = _create_real(name)
        entries.append(entry); statistics_rows.append(stats)
    seed = 3001
    for family in FAMILIES:
        for n in SIZES:
            for replicate in REPLICATES:
                entry, stats = _create_synthetic(family, n, replicate, seed, rejections)
                entries.append(entry); statistics_rows.append(stats)
                seed += 1
    if len(entries) != 39 or seed != 3037:
        raise AssertionError("benchmark suite must contain 39 instances and seeds 3001-3036")
    manifest = {
        "suite_id": SUITE_ID,
        "version": "1.0.0",
        "scientific_model": ATOMIC_MODEL_ID,
        "instance_count": len(entries),
        "real_instance_count": 3,
        "synthetic_instance_count": 36,
        "synthetic_families": list(FAMILIES),
        "synthetic_sizes": list(SIZES),
        "replicates_per_family_size": 2,
        "generation_seed_range": [3001, 3036],
        "instance_order": [entry["instance_id"] for entry in entries],
        "instances": entries,
    }
    manifest_path = OUT / "atomic_benchmark_manifest.json"
    _write_json(manifest_path, manifest)
    manifest_sha = sha256_file(manifest_path)
    (OUT / "atomic_benchmark_manifest.sha256").write_text(manifest_sha + "\n", encoding="utf-8")
    _write_csv(OUT / "atomic_benchmark_manifest.csv", entries)
    _write_csv(OUT / "atomic_instance_statistics.csv", statistics_rows)
    rejection_fields = [
        "instance_id", "family", "nominal_weld_count", "replicate",
        "rejected_seed", "next_seed", "rejection_reason",
    ]
    _write_csv(OUT / "atomic_instance_generation_rejections.csv", rejections, rejection_fields)
    result = {
        "suite_id": SUITE_ID,
        "benchmark_suite_sha256": manifest_sha,
        "instance_count": len(entries),
        "rejection_count": len(rejections),
        "all_smoke_passed": all(entry["smoke_eprk_passed"] and entry["smoke_hga_passed"] for entry in entries),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--generate", action="store_true")
    args = parser.parse_args()
    if not args.generate:
        parser.error("use --generate")
    generate()


if __name__ == "__main__":
    main()
