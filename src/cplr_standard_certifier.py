"""Independent standard-splitter certifier for CPLR phase 1."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Mapping, Sequence, Tuple

from continuous_lineage_decoder import (
    ActiveTask,
    CPLRChromosome,
    CPLRDecoderConfig,
    DecodedCPLRState,
    LineageSideId,
    RouteStatistics,
    aggregate_system_metrics,
    decode_chromosome,
)
from generate_welds import Weld
from lineage_presplit import Lineage, LineageBuildResult, build_lineages
from mrta_problem_core import assign_welds_to_robots_split, evaluate_order
from reachable_side_keys import apply_reachable_side_index, build_reachable_side_index


NUMERIC_TOL = 1.0e-8


@dataclass(frozen=True)
class CertificationResult:
    passed: bool
    decoded_state: DecodedCPLRState
    lineage_build: LineageBuildResult
    active_structure_equal: bool
    route_order_equal: bool
    direction_flags_equal: bool
    route_statistics_equal: bool
    system_metrics_equal: bool
    horizontal_length_conserved: bool
    vertical_length_conserved: bool
    unique_assignment: bool
    no_duplicates: bool
    no_omissions: bool
    failures: Tuple[str, ...]


def _project_parameter(parent: Weld, point: Tuple[float, float, float]) -> float:
    start = parent.start_point()
    end = parent.end_point()
    vector = tuple(end[i] - start[i] for i in range(3))
    denominator = sum(value * value for value in vector)
    if denominator == 0.0:
        return 0.0
    return sum((point[i] - start[i]) * vector[i] for i in range(3)) / denominator


def _standard_interval(parent: Weld, task: Weld) -> Tuple[float, float]:
    values = (
        _project_parameter(parent, task.start_point()),
        _project_parameter(parent, task.end_point()),
    )
    return min(values), max(values)


def _match_standard_task(
    task: Weld,
    robot_id: int,
    parents: Mapping[str, Weld],
    candidates: Mapping[Tuple[str, int], Sequence[ActiveTask]],
    tolerance: float,
) -> ActiveTask:
    parent_id = str(getattr(task, "parent_id", task.id))
    if parent_id not in parents:
        raise ValueError(f"standard task has unknown parent {parent_id}")
    s0, s1 = _standard_interval(parents[parent_id], task)
    matches = [
        item
        for item in candidates.get((parent_id, robot_id), ())
        if item.parent_weld_id == parent_id
        and item.robot_id == robot_id
        and math.isclose(item.parent_s_start, s0, rel_tol=0.0, abs_tol=tolerance)
        and math.isclose(item.parent_s_end, s1, rel_tol=0.0, abs_tol=tolerance)
        and math.isclose(item.length, float(task.length), rel_tol=1.0e-12, abs_tol=tolerance)
    ]
    if len(matches) != 1:
        raise ValueError(
            f"standard task match count={len(matches)} parent={parent_id} "
            f"robot={robot_id} interval=({s0}, {s1})"
        )
    return matches[0]


def _stats_equal(left: RouteStatistics, right: RouteStatistics) -> bool:
    return left.direction_flags == right.direction_flags and all(
        math.isclose(a, b, rel_tol=1.0e-11, abs_tol=NUMERIC_TOL)
        for a, b in (
            (left.total_time, right.total_time),
            (left.weld_time, right.weld_time),
            (left.travel_time, right.travel_time),
            (left.idle_distance, right.idle_distance),
        )
    )


def certify_candidate(
    welds: Sequence[Weld],
    chromosome: CPLRChromosome,
    config: CPLRDecoderConfig = CPLRDecoderConfig(),
    normalization_spec: Mapping[str, object] | None = None,
    *,
    prepared_lineage_build: LineageBuildResult | None = None,
    prepared_lineages: Sequence[Lineage] | None = None,
) -> CertificationResult:
    """Rebuild both paths from original welds and compare every frozen field."""
    if (prepared_lineage_build is None) != (prepared_lineages is None):
        raise ValueError("prepared lineage build and lineages must be provided together")
    if prepared_lineage_build is None:
        lineage_build = build_lineages(welds, eps=config.eps)
        reachable = build_reachable_side_index(lineage_build.lineages, config)
        lineages = apply_reachable_side_index(lineage_build.lineages, reachable)
    else:
        lineage_build = prepared_lineage_build
        lineages = tuple(prepared_lineages or ())
    decoded = decode_chromosome(lineages, chromosome, config, normalization_spec)

    standard_robots, standard_assignment = assign_welds_to_robots_split(
        welds,
        decoded.x_up,
        decoded.x_low,
        eps=config.eps,
        min_subweld_length=config.min_subweld_length,
    )
    parents = {str(weld.id): weld for weld in welds}
    key_map = chromosome.key_mapping()
    active_by_parent_robot = {}
    for active in decoded.active_tasks:
        active_by_parent_robot.setdefault(
            (active.parent_weld_id, active.robot_id), []
        ).append(active)
    standard_signatures = []
    standard_stats = []
    matched_ids = []
    match_failure = None
    try:
        for robot_id, tasks in enumerate(standard_robots):
            matched = [
                _match_standard_task(
                    task, robot_id, parents, active_by_parent_robot, config.eps * 10.0
                )
                for task in tasks
            ]
            matched.sort(key=lambda item: (key_map[item.lineage_side_id], item.lineage_side_id))
            standard_signatures.append(tuple(item.lineage_side_id for item in matched))
            matched_ids.extend(item.lineage_side_id for item in matched)
            route_welds = [item.to_weld() for item in matched]
            _, stats = evaluate_order(
                route_welds, tuple(range(len(route_welds))), config.motion_model
            )
            standard_stats.append(
                RouteStatistics(
                    robot_id=robot_id,
                    total_time=float(stats["total_time"]),
                    weld_time=float(stats["total_weld_time"]),
                    travel_time=float(stats["total_travel_time"]),
                    idle_distance=float(stats["total_idle_distance"]),
                    direction_flags=tuple(bool(value) for value in stats["direction_flags"]),
                )
            )
    except ValueError as exc:
        match_failure = str(exc)

    route_order_equal = match_failure is None and tuple(standard_signatures) == decoded.route_signatures
    direction_equal = match_failure is None and tuple(
        item.direction_flags for item in standard_stats
    ) == decoded.direction_flags
    route_stats_equal = match_failure is None and len(standard_stats) == 4 and all(
        _stats_equal(left, right)
        for left, right in zip(standard_stats, decoded.route_statistics)
    )
    standard_system = aggregate_system_metrics(standard_stats, normalization_spec)
    system_equal = match_failure is None and all(
        math.isclose(a, b, rel_tol=1.0e-11, abs_tol=NUMERIC_TOL)
        for a, b in zip(
            standard_system.__dict__.values(), decoded.system_metrics.__dict__.values()
        )
    )
    active_ids = [task.lineage_side_id for task in decoded.active_tasks]
    active_structure_equal = match_failure is None and set(matched_ids) == set(active_ids)
    unique = len(active_ids) == len(set(active_ids))
    no_duplicates = unique and len(matched_ids) == len(set(matched_ids))
    no_omissions = active_structure_equal and int(standard_assignment["unassigned_subweld_count"]) == 0
    horizontal_ok = lineage_build.length_error <= config.eps * max(
        1.0, lineage_build.original_total_length
    )
    vertical_ok = decoded.structural_validation.no_missing_lineage_length and float(
        standard_assignment["max_length_error"]
    ) <= config.eps * max(1.0, lineage_build.original_total_length)

    checks = {
        "active structure differs": active_structure_equal,
        "route order differs": route_order_equal,
        "direction flags differ": direction_equal,
        "route statistics differ": route_stats_equal,
        "system metrics differ": system_equal,
        "horizontal length conservation failed": horizontal_ok,
        "vertical length conservation failed": vertical_ok,
        "active task ownership is not unique": unique,
        "duplicate task detected": no_duplicates,
        "task omission detected": no_omissions,
        "decoder structural validation failed": decoded.structural_validation.passed,
    }
    failures = tuple(name for name, passed in checks.items() if not passed)
    if match_failure is not None:
        failures += (match_failure,)
    return CertificationResult(
        passed=not failures,
        decoded_state=decoded,
        lineage_build=lineage_build,
        active_structure_equal=active_structure_equal,
        route_order_equal=route_order_equal,
        direction_flags_equal=direction_equal,
        route_statistics_equal=route_stats_equal,
        system_metrics_equal=system_equal,
        horizontal_length_conserved=horizontal_ok,
        vertical_length_conserved=vertical_ok,
        unique_assignment=unique,
        no_duplicates=no_duplicates,
        no_omissions=no_omissions,
        failures=failures,
    )
