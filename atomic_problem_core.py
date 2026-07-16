"""Fixed atomic-weld model and event-boundary utilities.

This module is intentionally independent of the legacy split-at-evaluation model.
Atomic welds are immutable search inputs: all y=6 and long-weld splitting happens
before an optimizer starts.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple

import generate_welds
from mrta_problem_core import DEFAULT_MOTION_MODEL, MotionModel


ATOMIC_MODEL_ID = "atomic_weld_partition_routing_v1"
PLATFORM_W_M = float(generate_welds.PLATFORM_W_M)
PLATFORM_H_M = float(generate_welds.PLATFORM_H_M)
FIXED_Y_CUT_M = PLATFORM_H_M / 2.0
ZERO_ENDPOINT_TOL_M = 1e-9
Point3D = Tuple[float, float, float]


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


@dataclass(frozen=True)
class AtomicWeld:
    atomic_weld_id: str
    parent_weld_id: str
    parent_original_id: str
    half_region: str
    segment_index: int
    segment_count: int
    start: Point3D
    end: Point3D
    euclidean_length_m: float
    horizontal_length_m: float
    weld_time_s: float
    is_y6_split: bool
    is_long_weld_split: bool
    source_instance_hash: str

    @property
    def id(self) -> str:
        return self.atomic_weld_id

    @property
    def length(self) -> float:
        return self.euclidean_length_m

    def start_point(self) -> Point3D:
        return tuple(self.start)

    def end_point(self) -> Point3D:
        return tuple(self.end)

    def midpoint(self) -> Point3D:
        return tuple((a + b) / 2.0 for a, b in zip(self.start, self.end))  # type: ignore[return-value]

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "AtomicWeld":
        data = dict(value)
        data["start"] = tuple(float(x) for x in data["start"])
        data["end"] = tuple(float(x) for x in data["end"])
        return cls(**data)

    def to_dict(self) -> Dict[str, Any]:
        result = asdict(self)
        result["start"] = list(self.start)
        result["end"] = list(self.end)
        return result


@dataclass(frozen=True)
class BoundaryEvent:
    event_id: str
    half_region: str
    event_index: int
    representative_x: float
    interval_left: float
    interval_right: float
    interval_kind: str
    left_atomic_ids: Tuple[str, ...]
    right_atomic_ids: Tuple[str, ...]
    assignment_hash: str

    def to_dict(self) -> Dict[str, Any]:
        value = asdict(self)
        value["left_atomic_ids"] = list(self.left_atomic_ids)
        value["right_atomic_ids"] = list(self.right_atomic_ids)
        return value

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "BoundaryEvent":
        data = dict(value)
        data["left_atomic_ids"] = tuple(data["left_atomic_ids"])
        data["right_atomic_ids"] = tuple(data["right_atomic_ids"])
        return cls(**data)


@dataclass(frozen=True)
class AtomicInstance:
    name: str
    atomic_welds: Tuple[AtomicWeld, ...]
    upper_events: Tuple[BoundaryEvent, ...]
    lower_events: Tuple[BoundaryEvent, ...]
    metadata: Dict[str, Any]
    atomic_instance_hash: str

    @property
    def by_id(self) -> Dict[str, AtomicWeld]:
        return {w.id: w for w in self.atomic_welds}


def atomic_payload_hash(welds: Sequence[AtomicWeld], metadata: Mapping[str, Any]) -> str:
    payload = {
        "model_id": ATOMIC_MODEL_ID,
        "source_instance_hash": metadata["source_instance_hash"],
        "welds": [w.to_dict() for w in sorted(welds, key=lambda item: item.id)],
    }
    return sha256_json(payload)


def _assignment(welds: Sequence[AtomicWeld], x: float) -> Tuple[Tuple[str, ...], Tuple[str, ...]] | None:
    left: List[str] = []
    right: List[str] = []
    for weld in welds:
        xmin = min(weld.start[0], weld.end[0])
        xmax = max(weld.start[0], weld.end[0])
        if xmin < x < xmax:
            return None
        if xmax <= x:
            left.append(weld.id)
        else:
            right.append(weld.id)
    return tuple(sorted(left)), tuple(sorted(right))


def generate_boundary_events(welds: Sequence[AtomicWeld], half_region: str) -> List[BoundaryEvent]:
    """Enumerate unique feasible assignment cells induced by x endpoints.

    Open cells use their midpoint. Endpoint cells are retained only when they
    induce an assignment not represented by an adjacent cell. Every returned
    event therefore corresponds to one distinct task partition.
    """
    if half_region not in {"upper", "lower"}:
        raise ValueError("half_region must be upper or lower")
    half = sorted((w for w in welds if w.half_region == half_region), key=lambda w: w.id)
    points = sorted({0.0, PLATFORM_W_M, *(float(w.start[0]) for w in half), *(float(w.end[0]) for w in half)})
    cells: List[Tuple[float, float, float, str]] = []
    for point in points:
        cells.append((point, point, point, "point"))
    for left, right in zip(points[:-1], points[1:]):
        if right - left > 1e-12:
            cells.append((left, right, (left + right) / 2.0, "open_interval"))
    cells.sort(key=lambda item: (item[2], 0 if item[3] == "point" else 1))

    unique: Dict[str, Tuple[float, float, float, str, Tuple[str, ...], Tuple[str, ...]]] = {}
    for left_edge, right_edge, representative, kind in cells:
        assignment = _assignment(half, representative)
        if assignment is None:
            continue
        left_ids, right_ids = assignment
        ahash = sha256_json({"half": half_region, "left": left_ids, "right": right_ids})
        if ahash not in unique:
            unique[ahash] = (left_edge, right_edge, representative, kind, left_ids, right_ids)

    events: List[BoundaryEvent] = []
    for index, (ahash, cell) in enumerate(sorted(unique.items(), key=lambda item: item[1][2])):
        left_edge, right_edge, representative, kind, left_ids, right_ids = cell
        events.append(BoundaryEvent(
            event_id=f"{half_region}_event_{index:04d}", half_region=half_region,
            event_index=index, representative_x=representative,
            interval_left=left_edge, interval_right=right_edge, interval_kind=kind,
            left_atomic_ids=left_ids, right_atomic_ids=right_ids, assignment_hash=ahash,
        ))
    if not events:
        raise ValueError(f"no feasible {half_region} boundary event")
    return events


def validate_event_constancy(event: BoundaryEvent, welds: Sequence[AtomicWeld]) -> bool:
    half = [w for w in welds if w.half_region == event.half_region]
    expected = (event.left_atomic_ids, event.right_atomic_ids)
    samples = [event.representative_x]
    if event.interval_right - event.interval_left > 1e-12:
        span = event.interval_right - event.interval_left
        samples.extend([event.interval_left + 0.25 * span, event.interval_left + 0.75 * span])
    return all(_assignment(half, sample) == expected for sample in samples)


def event_pair_routes(upper: BoundaryEvent, lower: BoundaryEvent) -> Tuple[Tuple[str, ...], ...]:
    return (upper.left_atomic_ids, upper.right_atomic_ids, lower.left_atomic_ids, lower.right_atomic_ids)


def load_atomic_instance(path: str | Path) -> AtomicInstance:
    source = Path(path)
    value = json.loads(source.read_text(encoding="utf-8"))
    welds = tuple(AtomicWeld.from_dict(item) for item in value["atomic_welds"])
    upper = tuple(BoundaryEvent.from_dict(item) for item in value["boundary_events"]["upper"])
    lower = tuple(BoundaryEvent.from_dict(item) for item in value["boundary_events"]["lower"])
    metadata = dict(value["metadata"])
    actual_hash = atomic_payload_hash(welds, metadata)
    expected_hash = str(value["atomic_instance_hash"])
    if actual_hash != expected_hash:
        raise ValueError(f"atomic instance hash mismatch: {actual_hash} != {expected_hash}")
    if len({w.id for w in welds}) != len(welds):
        raise ValueError("duplicate atomic weld ID")
    if not all(validate_event_constancy(event, welds) for event in (*upper, *lower)):
        raise ValueError("boundary event constancy validation failed")
    return AtomicInstance(str(value["name"]), welds, upper, lower, metadata, expected_hash)


def scientific_model_payload(model: MotionModel = DEFAULT_MOTION_MODEL) -> Dict[str, Any]:
    return {
        "model_id": ATOMIC_MODEL_ID, "weld_speed": model.weld_speed,
        "travel_speed": model.travel_speed, "acceleration": model.acceleration,
        "safe_z": model.safe_z, "platform_width_m": PLATFORM_W_M,
        "platform_height_m": PLATFORM_H_M, "fixed_y_cut_m": FIXED_Y_CUT_M,
        "long_weld_lmax_m": 5.0, "long_weld_lmin_m": 1.0,
        "setup_time_s": 0.0, "post_time_s": 0.0, "route_type": "open",
        "direction_mode": "exact_two_state_dp", "zero_endpoint_tolerance_m": ZERO_ENDPOINT_TOL_M,
        "assignment_mode": "event_driven_fixed_atomic_read_only",
    }

