"""Problem-model utilities for four-robot weld scheduling.

The module implements the mathematical assumptions used in Chapter 2:

* one welding speed;
* one empty-travel maximum speed and one acceleration, shared by the lift,
  horizontal motion on the travel plane, and descent;
* open robot routes (initial positioning and final return are excluded);
* exact two-state, time-optimal orientation decoding for a fixed weld order;
* boundary-based weld splitting and complete-segment region assignment.

No population search or solver-specific logic is included here.
"""

from __future__ import annotations

import importlib.util
import math
import os
from dataclasses import dataclass
from types import ModuleType
from typing import Dict, List, Sequence, Tuple


def _load_generate_welds() -> ModuleType:
    """Load the project weld module without requiring uploaded files to be renamed."""
    try:
        import generate_welds as module  # type: ignore
        return module
    except ModuleNotFoundError:
        directory = os.path.dirname(os.path.abspath(__file__))
        for filename in ("generate_welds(25).py", "generate_welds(22).py"):
            path = os.path.join(directory, filename)
            if os.path.exists(path):
                spec = importlib.util.spec_from_file_location("generate_welds", path)
                if spec is None or spec.loader is None:
                    continue
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                return module
        raise


_generate_welds = _load_generate_welds()
PLATFORM_H_M = float(_generate_welds.PLATFORM_H_M)
Weld = _generate_welds.Weld
make_subweld = _generate_welds.make_subweld

Point3D = Tuple[float, float, float]


@dataclass(frozen=True)
class MotionModel:
    """Physical parameters used by welding and three-stage empty travel."""

    weld_speed: float = 0.0108
    travel_speed: float = 0.20
    acceleration: float = 0.50
    safe_z: float = 0.30

    def validate(self) -> None:
        if self.weld_speed <= 0.0:
            raise ValueError("weld_speed must be positive")
        if self.travel_speed <= 0.0:
            raise ValueError("travel_speed must be positive")
        if self.acceleration <= 0.0:
            raise ValueError("acceleration must be positive")


DEFAULT_MOTION_MODEL = MotionModel()


def trapezoidal_time(distance: float, v_max: float, acceleration: float) -> float:
    """Symmetric triangular/trapezoidal motion time for one movement segment."""
    if distance <= 0.0:
        return 0.0
    if v_max <= 0.0 or acceleration <= 0.0:
        raise ValueError("v_max and acceleration must be positive")
    critical_distance = v_max * v_max / acceleration
    if distance >= critical_distance:
        return distance / v_max + v_max / acceleration
    return 2.0 * math.sqrt(distance / acceleration)


def travel_time(
    p1: Point3D,
    p2: Point3D,
    model: MotionModel = DEFAULT_MOTION_MODEL,
) -> float:
    """Three-stage empty-travel time: lift, planar motion, and descent.

    The same empty-travel speed and acceleration are used for all three stages,
    consistently with the paper's motion model.
    """
    model.validate()
    horizontal = math.hypot(p2[0] - p1[0], p2[1] - p1[1])
    return (
        trapezoidal_time(
            abs(model.safe_z - p1[2]), model.travel_speed, model.acceleration
        )
        + trapezoidal_time(horizontal, model.travel_speed, model.acceleration)
        + trapezoidal_time(
            abs(model.safe_z - p2[2]), model.travel_speed, model.acceleration
        )
    )


def travel_distance(
    p1: Point3D,
    p2: Point3D,
    model: MotionModel = DEFAULT_MOTION_MODEL,
) -> float:
    """Lift distance + planar Euclidean distance + descent distance."""
    horizontal = math.hypot(p2[0] - p1[0], p2[1] - p1[1])
    vertical = abs(model.safe_z - p1[2]) + abs(model.safe_z - p2[2])
    return horizontal + vertical


def weld_time(weld: Weld, model: MotionModel = DEFAULT_MOTION_MODEL) -> float:
    return float(weld.length) / model.weld_speed


class DirectedWeld:
    def __init__(self, weld: Weld, reversed: bool = False):
        self.weld = weld
        self.reversed = reversed

    def start_point(self) -> Point3D:
        return self.weld.end_point() if self.reversed else self.weld.start_point()

    def end_point(self) -> Point3D:
        return self.weld.start_point() if self.reversed else self.weld.end_point()

    @property
    def length(self) -> float:
        return float(self.weld.length)

    def __getattr__(self, name):
        return getattr(self.weld, name)


def empty_route_stats() -> Dict[str, object]:
    return {
        "total_time": 0.0,
        "total_weld_time": 0.0,
        "total_travel_time": 0.0,
        "total_idle_distance": 0.0,
        "setup_post_saved_time": 0.0,
        "merged_setup_post_count": 0,
        "pure_weld_time": 0.0,
        "setup_post_time": 0.0,
        "reversed_weld_count": 0,
        "direction_mode": "bidirectional",
        "direction_decoder_objective": "minimum_route_time",
        "direction_flags": [],
    }


def compute_directed_route_stats(
    directed_sequence: Sequence[DirectedWeld],
    model: MotionModel = DEFAULT_MOTION_MODEL,
) -> Dict[str, object]:
    """Evaluate an open route; initial positioning and final return are excluded."""
    if not directed_sequence:
        return empty_route_stats()
    total_weld = sum(weld_time(task, model) for task in directed_sequence)
    total_travel = 0.0
    total_distance = 0.0
    for previous, current in zip(directed_sequence[:-1], directed_sequence[1:]):
        total_travel += travel_time(previous.end_point(), current.start_point(), model)
        total_distance += travel_distance(
            previous.end_point(), current.start_point(), model
        )
    return {
        "total_time": total_weld + total_travel,
        "total_weld_time": total_weld,
        "total_travel_time": total_travel,
        "total_idle_distance": total_distance,
        "setup_post_saved_time": 0.0,
        "merged_setup_post_count": 0,
        "pure_weld_time": total_weld,
        "setup_post_time": 0.0,
        "reversed_weld_count": sum(1 for task in directed_sequence if task.reversed),
        "direction_mode": "bidirectional",
        "direction_decoder_objective": "minimum_route_time",
        "direction_flags": [bool(task.reversed) for task in directed_sequence],
    }


def optimize_directions_for_order(
    welds: Sequence[Weld],
    order: Sequence[int],
    model: MotionModel = DEFAULT_MOTION_MODEL,
) -> Tuple[List[DirectedWeld], float]:
    """Find the minimum-time orientation combination for a fixed order in O(n).

    This decoder optimizes robot route completion time, not the system-level
    weighted objective.  System-level selection is performed by the ABMA.
    """
    if not order:
        return [], 0.0
    n = len(order)
    dp = [[math.inf, math.inf] for _ in range(n)]
    parent = [[0, 0] for _ in range(n)]
    first_cost = weld_time(welds[order[0]], model)
    dp[0] = [first_cost, first_cost]

    for position in range(1, n):
        current_weld = welds[order[position]]
        current_weld_time = weld_time(current_weld, model)
        for current_direction in (0, 1):
            current = DirectedWeld(current_weld, bool(current_direction))
            for previous_direction in (0, 1):
                previous = DirectedWeld(
                    welds[order[position - 1]], bool(previous_direction)
                )
                candidate = (
                    dp[position - 1][previous_direction]
                    + travel_time(previous.end_point(), current.start_point(), model)
                    + current_weld_time
                )
                if candidate < dp[position][current_direction]:
                    dp[position][current_direction] = candidate
                    parent[position][current_direction] = previous_direction

    direction = 0 if dp[-1][0] <= dp[-1][1] else 1
    directions = [False] * n
    for position in range(n - 1, -1, -1):
        directions[position] = bool(direction)
        if position > 0:
            direction = parent[position][direction]

    sequence = [
        DirectedWeld(welds[index], directions[position])
        for position, index in enumerate(order)
    ]
    return sequence, min(dp[-1])


def evaluate_order(
    welds: Sequence[Weld],
    order: Sequence[int],
    model: MotionModel = DEFAULT_MOTION_MODEL,
) -> Tuple[float, Dict[str, object]]:
    directed, _ = optimize_directions_for_order(welds, order, model)
    stats = compute_directed_route_stats(directed, model)
    return float(stats["total_time"]), stats


def partition_world_y() -> float:
    return PLATFORM_H_M / 2.0


def classify_point_region(
    point: Point3D,
    x_up: float,
    x_low: float,
    eps: float = 1e-9,
) -> int:
    """Deterministic half-open ownership for a point on a shared boundary."""
    x, y, _ = point
    if y >= partition_world_y() - eps:
        return 0 if x <= x_up + eps else 1
    return 2 if x <= x_low + eps else 3


def _point_compatible(
    point: Point3D,
    region: int,
    x_up: float,
    x_low: float,
    eps: float,
) -> bool:
    x, y, _ = point
    boundary_y = partition_world_y()
    tests = {
        0: y >= boundary_y - eps and x <= x_up + eps,
        1: y >= boundary_y - eps and x >= x_up - eps,
        2: y <= boundary_y + eps and x <= x_low + eps,
        3: y <= boundary_y + eps and x >= x_low - eps,
    }
    return tests.get(region, False)


def classify_weld_region(
    weld: Weld,
    x_up: float,
    x_low: float,
    eps: float = 1e-9,
) -> int | None:
    """Assign only when the complete segment is contained in one region.

    The regions are convex rectangles; therefore, containment of both endpoints
    is sufficient for containment of the complete line segment.  A fixed
    boundary priority is used only for a segment coincident with a shared
    boundary.  No midpoint-based assignment is used.
    """
    start, end = weld.start_point(), weld.end_point()
    candidates = [
        region
        for region in range(4)
        if _point_compatible(start, region, x_up, x_low, eps)
        and _point_compatible(end, region, x_up, x_low, eps)
    ]
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]
    # Upper before lower, and left before right, matching the paper's
    # deterministic half-open boundary convention.
    return min(candidates)


def _point_at(weld: Weld, parameter: float) -> Point3D:
    start, end = weld.start_point(), weld.end_point()
    return tuple(
        start[index] + (end[index] - start[index]) * parameter
        for index in range(3)
    )  # type: ignore[return-value]


def _unique_parameters(values: Sequence[float], eps: float) -> List[float]:
    result: List[float] = []
    for value in sorted(min(1.0, max(0.0, item)) for item in values):
        if not result or abs(value - result[-1]) > eps:
            result.append(value)
    return result


def split_weld_by_partition_boundaries(
    weld: Weld,
    x_up: float,
    x_low: float,
    eps: float = 1e-9,
    min_subweld_length: float = 1e-6,
) -> Tuple[List[Weld], Dict[str, object]]:
    """Split a weld only at effective region-boundary intersections.

    Nonzero subsegments are never discarded merely for being short, because
    doing so would violate weld-length conservation.  ``min_subweld_length``
    is retained as a reporting threshold.
    """
    boundary_y = partition_world_y()
    x1, y1, _ = weld.start_point()
    x2, y2, _ = weld.end_point()
    dx, dy = x2 - x1, y2 - y1
    parameters = [0.0, 1.0]
    overlap_count = 0

    if abs(dy) <= eps:
        overlap_count += int(
            abs(y1 - boundary_y) <= eps and abs(y2 - boundary_y) <= eps
        )
    elif (y1 - boundary_y) * (y2 - boundary_y) < -(eps * eps):
        parameters.append((boundary_y - y1) / dy)

    for boundary_x, upper in ((x_up, True), (x_low, False)):
        if abs(dx) <= eps:
            in_half = (
                max(y1, y2) >= boundary_y - eps
                if upper
                else min(y1, y2) <= boundary_y + eps
            )
            overlap_count += int(
                abs(x1 - boundary_x) <= eps
                and abs(x2 - boundary_x) <= eps
                and in_half
            )
        elif (x1 - boundary_x) * (x2 - boundary_x) < -(eps * eps):
            parameter = (boundary_x - x1) / dx
            y_at = _point_at(weld, parameter)[1]
            if (upper and y_at >= boundary_y - eps) or (
                not upper and y_at < boundary_y + eps
            ):
                parameters.append(parameter)

    parameters = _unique_parameters(parameters, eps)
    if (
        len(parameters) <= 2
        and classify_weld_region(weld, x_up, x_low, eps) is not None
    ):
        return [weld], {
            "was_split": False,
            "cross_region": False,
            "boundary_overlap_count": overlap_count,
            "zero_length_subweld_count": 0,
            "short_subweld_count": int(float(weld.length) < min_subweld_length),
            "discarded_short_subweld_count": 0,
            "length_error": 0.0,
        }

    subwelds: List[Weld] = []
    zero_length_count = 0
    short_count = 0
    for lower, upper in zip(parameters[:-1], parameters[1:]):
        start, end = _point_at(weld, lower), _point_at(weld, upper)
        segment_length = math.dist(start, end)
        if segment_length <= eps:
            zero_length_count += 1
            continue
        if segment_length < min_subweld_length:
            short_count += 1
        subwelds.append(make_subweld(weld, len(subwelds), start, end))

    length_error = abs(sum(float(item.length) for item in subwelds) - float(weld.length))
    return subwelds, {
        "was_split": len(subwelds) > 1,
        "cross_region": (
            classify_weld_region(weld, x_up, x_low, eps) is None
            or len(subwelds) > 1
        ),
        "boundary_overlap_count": overlap_count,
        "zero_length_subweld_count": zero_length_count,
        "short_subweld_count": short_count,
        "discarded_short_subweld_count": 0,
        "length_error": length_error,
    }


def assign_welds_to_robots_split(
    welds: Sequence[Weld],
    x_up: float,
    x_low: float,
    eps: float = 1e-9,
    min_subweld_length: float = 1e-6,
) -> Tuple[List[List[Weld]], Dict[str, object]]:
    robots: List[List[Weld]] = [[] for _ in range(4)]
    all_subwelds: List[Weld] = []
    per_weld_stats: List[Dict[str, object]] = []

    for weld in welds:
        subwelds, stats = split_weld_by_partition_boundaries(
            weld,
            x_up,
            x_low,
            eps=eps,
            min_subweld_length=min_subweld_length,
        )
        all_subwelds.extend(subwelds)
        per_weld_stats.append(stats)

    unassigned: List[str] = []
    for subweld in all_subwelds:
        region = classify_weld_region(subweld, x_up, x_low, eps)
        if region is None:
            unassigned.append(str(subweld.id))
        else:
            robots[region].append(subweld)

    length_errors = [float(stats["length_error"]) for stats in per_weld_stats]
    parent_ids = {
        getattr(weld, "parent_id", weld.id) for weld in all_subwelds
    }
    stats: Dict[str, object] = {
        "assignment_mode": "split_complete_segment",
        "original_weld_count": len(welds),
        "subweld_count": len(all_subwelds),
        "split_weld_count": sum(bool(item["was_split"]) for item in per_weld_stats),
        "cross_region_weld_count": sum(
            bool(item["cross_region"]) for item in per_weld_stats
        ),
        "boundary_overlap_count": sum(
            int(item["boundary_overlap_count"]) for item in per_weld_stats
        ),
        "zero_length_subweld_count": sum(
            int(item["zero_length_subweld_count"]) for item in per_weld_stats
        ),
        "short_subweld_count": sum(
            int(item["short_subweld_count"]) for item in per_weld_stats
        ),
        "discarded_short_subweld_count": 0,
        "max_length_error": max(length_errors, default=0.0),
        "sum_length_error": sum(length_errors),
        "assigned_subweld_count": sum(len(route) for route in robots),
        "unassigned_subweld_count": len(unassigned),
        "unassigned_subweld_ids": list(unassigned),
        "parent_id_count": len(parent_ids),
        "split_segment_count": sum(
            bool(getattr(weld, "is_split_segment", False))
            for weld in all_subwelds
        ),
    }
    stats.update(
        {f"robot_{index}_task_count": len(route) for index, route in enumerate(robots)}
    )
    return robots, stats
