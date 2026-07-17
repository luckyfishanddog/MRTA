"""Deterministic fixed pre-splitting for the ABMA development model.

All geometry changes happen here, before an optimizer starts.  The generated
fixed welds are immutable and no x-boundary is allowed to cut their interior.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple

from generate_welds import Weld, load_frozen_weld_instance


SCIENTIFIC_MODEL_VERSION = "fixed_presplit_no_cross_region_zero_connected_travel_v1"
PREPROCESSOR_VERSION = "fixed-presplit-preprocessor-v1.0.0"
LMAX_M = 5.0
LMIN_M = 1.0
Y_SPLIT_M = 6.0
PLATFORM_W_M = 20.0
GEOMETRY_EPS = 1e-9
Point3D = Tuple[float, float, float]


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _point(value: Sequence[float]) -> Point3D:
    return tuple(round(float(x), 12) for x in value)  # type: ignore[return-value]


def canonical_endpoints(start: Sequence[float], end: Sequence[float]) -> Tuple[Point3D, Point3D]:
    a, b = _point(start), _point(end)
    return (a, b) if a <= b else (b, a)


@dataclass(frozen=True)
class FixedWeld:
    fixed_weld_id: str
    parent_weld_id: str
    original_weld_id: str
    half_id: str
    presplit_index: int
    presplit_count: int
    start: Point3D
    end: Point3D
    euclidean_length_m: float
    horizontal_span_m: float
    weld_time_s: float
    y6_split: bool
    long_weld_split: bool
    split_reasons: Tuple[str, ...]
    canonical_geometry_key: str
    source_instance_hash: str

    @property
    def id(self) -> str:
        return self.fixed_weld_id

    @property
    def length(self) -> float:
        return self.euclidean_length_m

    def start_point(self) -> Point3D:
        return self.start

    def end_point(self) -> Point3D:
        return self.end

    def to_weld(self) -> Weld:
        value = Weld(self.fixed_weld_id, *self.start, *self.end,
                     parent_id=self.parent_weld_id, source_weld_id=self.original_weld_id)
        value.fixed_weld_id = self.fixed_weld_id
        value.original_weld_id = self.original_weld_id
        value.half_id = self.half_id
        value.presplit_index = self.presplit_index
        value.presplit_count = self.presplit_count
        value.canonical_geometry_key = self.canonical_geometry_key
        value.y6_split = self.y6_split
        value.long_weld_split = self.long_weld_split
        return value

    def to_dict(self) -> Dict[str, Any]:
        value = asdict(self)
        value["start"] = list(self.start); value["end"] = list(self.end)
        value["split_reasons"] = list(self.split_reasons)
        return value

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "FixedWeld":
        data = dict(value); data["start"] = tuple(data["start"]); data["end"] = tuple(data["end"])
        data["split_reasons"] = tuple(data["split_reasons"])
        return cls(**data)


def _split_y(start: Point3D, end: Point3D) -> List[Tuple[str, Point3D, Point3D, bool]]:
    y1, y2 = start[1], end[1]
    if (y1 - Y_SPLIT_M) * (y2 - Y_SPLIT_M) < 0.0:
        t = (Y_SPLIT_M - y1) / (y2 - y1)
        middle = _point(tuple(start[i] + t * (end[i] - start[i]) for i in range(3)))
        pairs = ((start, middle), (middle, end))
        return [("upper" if (a[1] + b[1]) / 2.0 >= Y_SPLIT_M else "lower",
                 *canonical_endpoints(a, b), True) for a, b in pairs if math.dist(a, b) > GEOMETRY_EPS]
    if y1 >= Y_SPLIT_M and y2 >= Y_SPLIT_M:
        half = "upper"
    elif y1 <= Y_SPLIT_M and y2 <= Y_SPLIT_M:
        half = "upper" if abs(y1 - Y_SPLIT_M) <= GEOMETRY_EPS and abs(y2 - Y_SPLIT_M) <= GEOMETRY_EPS else "lower"
    else:
        raise ValueError("numerically inconsistent y=6 classification")
    a, b = canonical_endpoints(start, end)
    return [(half, a, b, False)]


def _split_long(start: Point3D, end: Point3D) -> List[Tuple[Point3D, Point3D, bool]]:
    horizontal = abs(end[0] - start[0])
    if horizontal <= LMAX_M:
        return [(start, end, False)]
    count = math.floor(horizontal / LMIN_M)
    if count < 2:
        raise ValueError("long-weld rule produced fewer than two pieces")
    pieces = []
    for index in range(count):
        a = _point(tuple(start[d] + (end[d] - start[d]) * index / count for d in range(3)))
        b = _point(tuple(start[d] + (end[d] - start[d]) * (index + 1) / count for d in range(3)))
        a, b = canonical_endpoints(a, b)
        if abs(b[0] - a[0]) + GEOMETRY_EPS < LMIN_M:
            raise ValueError("long-weld child horizontal span below lmin")
        pieces.append((a, b, True))
    return pieces


def presplit_welds(welds: Sequence[Weld], source_instance_hash: str,
                   weld_speed: float = 0.0108) -> List[FixedWeld]:
    result: List[FixedWeld] = []
    for weld in sorted(welds, key=lambda x: str(x.id)):
        original_start, original_end = canonical_endpoints(weld.start_point(), weld.end_point())
        raw: List[Tuple[str, Point3D, Point3D, bool, bool]] = []
        for half, ystart, yend, ysplit in _split_y(original_start, original_end):
            for start, end, long_split in _split_long(ystart, yend):
                raw.append((half, start, end, ysplit, long_split))
        raw.sort(key=lambda item: (item[0], item[1], item[2]))
        count = len(raw)
        for index, (half, start, end, ysplit, long_split) in enumerate(raw, 1):
            length = math.dist(start, end); horizontal = abs(end[0] - start[0])
            geometry_payload = {"parent": str(weld.id), "half": half, "start": start, "end": end}
            geometry_key = canonical_json(geometry_payload)
            digest = hashlib.blake2b(geometry_key.encode("utf-8"), digest_size=10).hexdigest()
            reasons = tuple(name for name, enabled in (("y6_split", ysplit), ("long_weld_split", long_split)) if enabled)
            if not reasons: reasons = ("unchanged",)
            result.append(FixedWeld(
                fixed_weld_id=f"{weld.id}|fixed={digest}", parent_weld_id=str(weld.id),
                original_weld_id=str(weld.id), half_id=half, presplit_index=index,
                presplit_count=count, start=start, end=end, euclidean_length_m=length,
                horizontal_span_m=horizontal, weld_time_s=length / weld_speed,
                y6_split=ysplit, long_weld_split=long_split, split_reasons=reasons,
                canonical_geometry_key=geometry_key, source_instance_hash=source_instance_hash,
            ))
    if len({w.id for w in result}) != len(result):
        raise ValueError("duplicate fixed-weld ID")
    return sorted(result, key=lambda x: x.id)


def assign_fixed_welds(fixed: Sequence[FixedWeld], x_up: float, x_low: float,
                       eps: float = GEOMETRY_EPS) -> Tuple[List[List[FixedWeld]], Dict[str, Any]]:
    robots: List[List[FixedWeld]] = [[], [], [], []]
    for weld in fixed:
        boundary = x_up if weld.half_id == "upper" else x_low
        left_robot = 0 if weld.half_id == "upper" else 2
        xmin, xmax = sorted((weld.start[0], weld.end[0]))
        left = xmax <= boundary + eps
        right = xmin >= boundary - eps
        # A vertical/point weld exactly on the boundary belongs to the left.
        if left:
            robots[left_robot].append(weld)
        elif right:
            robots[left_robot + 1].append(weld)
        else:
            raise ValueError(f"boundary cuts fixed weld {weld.id}")
    flat = [w.id for robot in robots for w in robot]
    if len(flat) != len(fixed) or len(set(flat)) != len(flat):
        raise ValueError("fixed assignment is incomplete or duplicated")
    stats = {
        "fixed_weld_count": len(fixed), "subweld_count": len(fixed),
        "split_weld_count": sum(w.y6_split or w.long_weld_split for w in fixed),
        "unassigned_subweld_count": 0, "discarded_short_subweld_count": 0,
        "max_length_error": 0.0,
        **{f"robot_{i}_task_count": len(robot) for i, robot in enumerate(robots)},
    }
    return robots, stats


def build_boundary_candidates(fixed: Sequence[FixedWeld], half: str,
                              disallow_empty_regions: bool = True) -> Dict[str, Any]:
    tasks = sorted((w for w in fixed if w.half_id == half), key=lambda x: x.id)
    endpoint_sources: Dict[float, set[str]] = {0.0: {"platform_left"}, PLATFORM_W_M: {"platform_right"}}
    for weld in tasks:
        for x in (weld.start[0], weld.end[0]):
            endpoint_sources.setdefault(round(float(x), 12), set()).add("fixed_weld_endpoint")
    endpoints = sorted(endpoint_sources)
    raw = dict(endpoint_sources)
    for a, b in zip(endpoints[:-1], endpoints[1:]):
        if b - a > GEOMETRY_EPS:
            raw.setdefault(round((a + b) / 2.0, 12), set()).add("safe_interval_midpoint")
    candidates = []; excluded = []
    for x in sorted(raw):
        reasons = []
        if not -GEOMETRY_EPS <= x <= PLATFORM_W_M + GEOMETRY_EPS:
            reasons.append("outside_platform")
        crossing = [w.id for w in tasks if min(w.start[0], w.end[0]) + GEOMETRY_EPS < x < max(w.start[0], w.end[0]) - GEOMETRY_EPS]
        if crossing: reasons.append("cuts_fixed_weld")
        if reasons:
            excluded.append({"x": x, "sources": sorted(raw[x]), "reasons": reasons, "crossing_fixed_weld_ids": crossing})
            continue
        # Validate a boundary against its own half only.  Coupling this check
        # to an arbitrary opposite-half boundary can reject a legal candidate
        # merely because the unrelated boundary cuts a weld.
        left_count = right_count = 0
        valid = True
        for weld in tasks:
            xmin, xmax = sorted((weld.start[0], weld.end[0]))
            if xmin < x - GEOMETRY_EPS and xmax > x + GEOMETRY_EPS:
                valid = False
                break
            # Vertical/point welds exactly on the boundary go left.
            if xmax <= x + GEOMETRY_EPS:
                left_count += 1
            else:
                right_count += 1
        if not valid:
            excluded.append({"x": x, "sources": sorted(raw[x]), "reasons": ["assignment_infeasible"]})
            continue
        if disallow_empty_regions and (left_count == 0 or right_count == 0):
            excluded.append({"x": x, "sources": sorted(raw[x]), "reasons": ["empty_side_disallowed"],
                             "left_task_count": left_count, "right_task_count": right_count})
            continue
        candidates.append({"index": len(candidates), "x": x, "sources": sorted(raw[x]),
                           "left_task_count": left_count, "right_task_count": right_count})
    if not candidates:
        raise ValueError(f"no valid internal boundary for {half} half")
    return {"half": half, "empty_regions_allowed": not disallow_empty_regions,
            "vertical_on_boundary_assignment": "left", "candidates": candidates, "excluded": excluded}


def preprocessing_payload(name: str, xlsx: Path) -> Dict[str, Any]:
    original, metadata = load_frozen_weld_instance(str(xlsx))
    source_hash = str(metadata["instance_hash"]); fixed = presplit_welds(original, source_hash)
    upper = build_boundary_candidates(fixed, "upper"); lower = build_boundary_candidates(fixed, "lower")
    original_length = sum(float(w.length) for w in original); fixed_length = sum(w.length for w in fixed)
    parent_map = [{"fixed_weld_id": w.id, "parent_weld_id": w.parent_weld_id,
                   "presplit_index": w.presplit_index, "presplit_count": w.presplit_count} for w in fixed]
    body = {
        "name": name, "scientific_model_version": SCIENTIFIC_MODEL_VERSION,
        "preprocessor_version": PREPROCESSOR_VERSION, "source_instance_hash": source_hash,
        "source_instance_file_sha256": file_sha256(xlsx), "lmax_m": LMAX_M,
        "lmin_m": LMIN_M, "long_weld_metric": "horizontal_span_abs_dx",
        "y_split_m": Y_SPLIT_M, "geometry_epsilon_m": GEOMETRY_EPS,
        "setup_time_s": 0.0, "post_processing_time_s": 0.0,
        "dynamic_x_split": False, "connected_endpoint_transition": "zero",
        "original_weld_count": len(original),
        "y6_split_parent_count": len({w.parent_weld_id for w in fixed if w.y6_split}),
        "y6_split_fixed_weld_count": sum(w.y6_split for w in fixed),
        "long_split_parent_count": len({w.parent_weld_id for w in fixed if w.long_weld_split}),
        "long_split_fixed_weld_count": sum(w.long_weld_split for w in fixed),
        "fixed_weld_count": len(fixed), "original_total_length_m": original_length,
        "fixed_total_length_m": fixed_length, "length_error_m": fixed_length - original_length,
        "original_total_weld_time_s": original_length / 0.0108,
        "fixed_total_weld_time_s": fixed_length / 0.0108,
        "fixed_welds": [w.to_dict() for w in fixed], "parent_child_map": parent_map,
        "boundary_candidates": {"upper": upper, "lower": lower},
    }
    body["preprocessing_hash"] = sha256_json(body)
    return body


def load_fixed_instance(path: str | Path) -> Tuple[List[FixedWeld], Dict[str, Any]]:
    value = json.loads(Path(path).read_text(encoding="utf-8")); expected = value.pop("preprocessing_hash")
    actual = sha256_json(value); value["preprocessing_hash"] = expected
    if actual != expected: raise ValueError("fixed-presplit preprocessing hash mismatch")
    fixed = [FixedWeld.from_dict(item) for item in value["fixed_welds"]]
    return fixed, value


def run_all(output_dir: Path = Path("data/instances/fixed_presplit")) -> List[Dict[str, Any]]:
    output_dir.mkdir(parents=True, exist_ok=True); rows = []
    for name in ("w30", "w45", "w60"):
        payload = preprocessing_payload(name, Path(f"data/instances/selected/instance_{name}.xlsx"))
        path = output_dir / f"{name}_fixed_presplit.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        path.with_suffix(".sha256").write_text(file_sha256(path) + "\n", encoding="utf-8")
        rows.append({"instance": name, "original_weld_count": payload["original_weld_count"],
                     "fixed_weld_count": payload["fixed_weld_count"],
                     "y6_split_parent_count": payload["y6_split_parent_count"],
                     "long_split_parent_count": payload["long_split_parent_count"],
                     "long_split_fixed_weld_count": payload["long_split_fixed_weld_count"],
                     "upper_boundary_count": len(payload["boundary_candidates"]["upper"]["candidates"]),
                     "lower_boundary_count": len(payload["boundary_candidates"]["lower"]["candidates"]),
                     "preprocessing_hash": payload["preprocessing_hash"], "file": str(path)})
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(); parser.add_argument("--output-dir", default="data/instances/fixed_presplit")
    args = parser.parse_args(); print(json.dumps(run_all(Path(args.output_dir)), ensure_ascii=False, indent=2))


if __name__ == "__main__": main()
