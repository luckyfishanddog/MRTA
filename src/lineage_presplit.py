"""Fixed-horizontal lineage construction for the CPLR phase-1 decoder.

Only the scientific model's fixed horizontal boundary is materialized here.
The continuous vertical boundaries remain candidate variables and are decoded
later by :mod:`continuous_lineage_decoder`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Iterable, Sequence, Tuple

from generate_welds import Weld
from mrta_problem_core import partition_world_y


Point3D = Tuple[float, float, float]
GEOMETRY_EPS = 1.0e-9


@dataclass(frozen=True)
class Lineage:
    lineage_id: int
    parent_weld_id: str
    half_region: str
    parent_s_start: float
    parent_s_end: float
    start_point: Point3D
    end_point: Point3D
    x_min: float
    x_max: float
    length: float
    reachable_left: bool = True
    reachable_right: bool = True

    def __post_init__(self) -> None:
        if self.lineage_id < 0:
            raise ValueError("lineage_id must be nonnegative")
        if self.half_region not in {"upper", "lower"}:
            raise ValueError("half_region must be upper or lower")
        if not (0.0 <= self.parent_s_start < self.parent_s_end <= 1.0):
            raise ValueError("invalid parent parameter interval")
        if self.length <= 0.0:
            raise ValueError("lineage length must be positive")


@dataclass(frozen=True)
class LineageBuildResult:
    lineages: Tuple[Lineage, ...]
    fixed_y: float
    eps: float
    original_weld_count: int
    original_total_length: float
    lineage_total_length: float
    horizontal_split_parent_count: int
    length_error: float


def _point_at(weld: Weld, parameter: float) -> Point3D:
    start = weld.start_point()
    end = weld.end_point()
    return tuple(
        float(start[i]) + (float(end[i]) - float(start[i])) * parameter
        for i in range(3)
    )  # type: ignore[return-value]


def _stable_weld_key(weld: Weld) -> Tuple[object, ...]:
    return (
        str(weld.id),
        *(float(value) for value in (*weld.start_point(), *weld.end_point())),
    )


def _horizontal_parts(
    weld: Weld, fixed_y: float, eps: float
) -> Tuple[Tuple[str, float, float, Point3D, Point3D], ...]:
    start = weld.start_point()
    end = weld.end_point()
    y1, y2 = float(start[1]), float(end[1])
    dy = y2 - y1
    parameters = [0.0, 1.0]
    if abs(dy) > eps and (y1 - fixed_y) * (y2 - fixed_y) < -(eps * eps):
        parameters.insert(1, (fixed_y - y1) / dy)

    parts = []
    for s0, s1 in zip(parameters[:-1], parameters[1:]):
        p0, p1 = _point_at(weld, s0), _point_at(weld, s1)
        length = math.dist(p0, p1)
        if length <= eps:
            continue
        # This is the same upper-first half-open ownership used by the standard
        # splitter when a segment lies on (or within eps of) the y boundary.
        half = "upper" if p0[1] >= fixed_y - eps and p1[1] >= fixed_y - eps else "lower"
        parts.append((half, s0, s1, p0, p1))
    return tuple(parts)


def build_lineages(
    welds: Sequence[Weld],
    *,
    fixed_y: float | None = None,
    eps: float = GEOMETRY_EPS,
) -> LineageBuildResult:
    """Build deterministic lineages independent of input sequence order."""
    if eps <= 0.0:
        raise ValueError("eps must be positive")
    y_cut = partition_world_y() if fixed_y is None else float(fixed_y)
    identifiers = [str(weld.id) for weld in welds]
    if len(set(identifiers)) != len(identifiers):
        raise ValueError("original weld ids must be unique")

    raw = []
    split_parents = 0
    for weld in sorted(welds, key=_stable_weld_key):
        parts = _horizontal_parts(weld, y_cut, eps)
        split_parents += int(len(parts) > 1)
        for half, s0, s1, p0, p1 in parts:
            raw.append((str(weld.id), half, s0, s1, p0, p1))

    lineages = []
    for lineage_id, (parent_id, half, s0, s1, p0, p1) in enumerate(raw):
        lineages.append(
            Lineage(
                lineage_id=lineage_id,
                parent_weld_id=parent_id,
                half_region=half,
                parent_s_start=float(s0),
                parent_s_end=float(s1),
                start_point=p0,
                end_point=p1,
                x_min=min(p0[0], p1[0]),
                x_max=max(p0[0], p1[0]),
                length=math.dist(p0, p1),
            )
        )

    original_total = sum(float(weld.length) for weld in welds)
    lineage_total = sum(item.length for item in lineages)
    error = abs(original_total - lineage_total)
    tolerance = max(eps, eps * max(1.0, original_total))
    if error > tolerance:
        raise ValueError(f"horizontal lineage length conservation failed: {error}")
    return LineageBuildResult(
        lineages=tuple(lineages),
        fixed_y=y_cut,
        eps=eps,
        original_weld_count=len(welds),
        original_total_length=original_total,
        lineage_total_length=lineage_total,
        horizontal_split_parent_count=split_parents,
        length_error=error,
    )


def with_reachability(
    lineages: Iterable[Lineage], reachable: Iterable[Tuple[int, str]]
) -> Tuple[Lineage, ...]:
    keys = set(reachable)
    return tuple(
        replace(
            lineage,
            reachable_left=(lineage.lineage_id, "L") in keys,
            reachable_right=(lineage.lineage_id, "R") in keys,
        )
        for lineage in lineages
    )
