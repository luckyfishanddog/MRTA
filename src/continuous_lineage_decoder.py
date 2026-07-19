"""Deterministic fixed-dimension CPLR phase-1 chromosome decoder."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence, Tuple

from generate_welds import PLATFORM_W_M, Weld
from lineage_presplit import GEOMETRY_EPS, Lineage, Point3D
from mrta_problem_core import DEFAULT_MOTION_MODEL, MotionModel, evaluate_order


@dataclass(frozen=True, order=True)
class LineageSideId:
    lineage_id: int
    side: str

    def __post_init__(self) -> None:
        if self.lineage_id < 0 or self.side not in {"L", "R"}:
            raise ValueError("invalid lineage-side id")


@dataclass(frozen=True)
class ActiveTask:
    lineage_side_id: LineageSideId
    parent_weld_id: str
    half_region: str
    side: str
    robot_id: int
    parent_s_start: float
    parent_s_end: float
    start_point: Point3D
    end_point: Point3D
    length: float
    weld_time: float

    @property
    def stable_id(self) -> str:
        key = self.lineage_side_id
        return f"cplr:l{key.lineage_id}:{key.side}"

    def to_weld(self) -> Weld:
        return Weld(
            self.stable_id,
            *self.start_point,
            *self.end_point,
            parent_id=self.parent_weld_id,
            segment_id=(self.lineage_side_id.lineage_id, self.side),
            is_split_segment=True,
            original_start=self.start_point,
            original_end=self.end_point,
            source_weld_id=self.parent_weld_id,
        )


@dataclass(frozen=True)
class CPLRChromosome:
    upper_boundary_gene: float
    lower_boundary_gene: float
    side_keys: Tuple[Tuple[LineageSideId, float], ...]

    def __post_init__(self) -> None:
        for name, value in (
            ("upper_boundary_gene", self.upper_boundary_gene),
            ("lower_boundary_gene", self.lower_boundary_gene),
        ):
            if not math.isfinite(value) or not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be finite and in [0, 1]")
        ids = [item[0] for item in self.side_keys]
        if len(ids) != len(set(ids)):
            raise ValueError("chromosome contains duplicate lineage-side keys")
        if tuple(sorted(ids)) != tuple(ids):
            raise ValueError("chromosome side_keys must be sorted by lineage-side id")
        if any(not math.isfinite(value) or not 0.0 <= value <= 1.0 for _, value in self.side_keys):
            raise ValueError("random keys must be finite and in [0, 1]")

    @classmethod
    def from_mapping(
        cls,
        upper_boundary_gene: float,
        lower_boundary_gene: float,
        keys: Mapping[LineageSideId, float],
    ) -> "CPLRChromosome":
        return cls(
            float(upper_boundary_gene),
            float(lower_boundary_gene),
            tuple(sorted((key, float(value)) for key, value in keys.items())),
        )

    def key_mapping(self) -> Mapping[LineageSideId, float]:
        return dict(self.side_keys)


@dataclass(frozen=True)
class CPLRDecoderConfig:
    upper_boundary_min: float = 0.0
    upper_boundary_max: float = float(PLATFORM_W_M)
    lower_boundary_min: float = 0.0
    lower_boundary_max: float = float(PLATFORM_W_M)
    eps: float = GEOMETRY_EPS
    min_subweld_length: float = 1.0e-6
    motion_model: MotionModel = DEFAULT_MOTION_MODEL

    def __post_init__(self) -> None:
        if self.eps <= 0.0 or self.min_subweld_length < 0.0:
            raise ValueError("invalid decoder tolerances")
        if self.upper_boundary_min > self.upper_boundary_max:
            raise ValueError("invalid upper boundary domain")
        if self.lower_boundary_min > self.lower_boundary_max:
            raise ValueError("invalid lower boundary domain")
        self.motion_model.validate()


@dataclass(frozen=True)
class RouteStatistics:
    robot_id: int
    total_time: float
    weld_time: float
    travel_time: float
    idle_distance: float
    direction_flags: Tuple[bool, ...]


@dataclass(frozen=True)
class SystemMetrics:
    makespan: float
    load_imbalance: float
    total_weld_time: float
    total_travel_time: float
    total_idle_distance: float
    sum_robot_total_time: float
    fitness: float


@dataclass(frozen=True)
class StructuralValidation:
    unique_assignment: bool
    no_duplicate_side_ids: bool
    no_missing_lineage_length: bool
    route_coverage_complete: bool
    length_error: float

    @property
    def passed(self) -> bool:
        return all(
            (
                self.unique_assignment,
                self.no_duplicate_side_ids,
                self.no_missing_lineage_length,
                self.route_coverage_complete,
            )
        )


@dataclass(frozen=True)
class DecodedCPLRState:
    x_up: float
    x_low: float
    active_tasks: Tuple[ActiveTask, ...]
    route_signatures: Tuple[Tuple[LineageSideId, ...], ...]
    route_statistics: Tuple[RouteStatistics, ...]
    direction_flags: Tuple[Tuple[bool, ...], ...]
    system_metrics: SystemMetrics
    structural_validation: StructuralValidation


def all_side_ids(lineages: Iterable[Lineage]) -> Tuple[LineageSideId, ...]:
    return tuple(
        LineageSideId(lineage.lineage_id, side)
        for lineage in lineages
        for side, reachable in (
            ("L", lineage.reachable_left),
            ("R", lineage.reachable_right),
        )
        if reachable
    )


def decode_boundaries(
    chromosome: CPLRChromosome, config: CPLRDecoderConfig
) -> Tuple[float, float]:
    x_up = config.upper_boundary_min + chromosome.upper_boundary_gene * (
        config.upper_boundary_max - config.upper_boundary_min
    )
    x_low = config.lower_boundary_min + chromosome.lower_boundary_gene * (
        config.lower_boundary_max - config.lower_boundary_min
    )
    return x_up, x_low


def _point_between(a: Point3D, b: Point3D, t: float) -> Point3D:
    return tuple(a[i] + (b[i] - a[i]) * t for i in range(3))  # type: ignore[return-value]


def _active_task(
    lineage: Lineage,
    side: str,
    local_start: float,
    local_end: float,
    model: MotionModel,
    eps: float,
) -> ActiveTask | None:
    start = _point_between(lineage.start_point, lineage.end_point, local_start)
    end = _point_between(lineage.start_point, lineage.end_point, local_end)
    length = math.dist(start, end)
    if length <= eps:
        return None
    parent_span = lineage.parent_s_end - lineage.parent_s_start
    parent_start = lineage.parent_s_start + parent_span * local_start
    parent_end = lineage.parent_s_start + parent_span * local_end
    robot = (0 if lineage.half_region == "upper" else 2) + int(side == "R")
    return ActiveTask(
        lineage_side_id=LineageSideId(lineage.lineage_id, side),
        parent_weld_id=lineage.parent_weld_id,
        half_region=lineage.half_region,
        side=side,
        robot_id=robot,
        parent_s_start=parent_start,
        parent_s_end=parent_end,
        start_point=start,
        end_point=end,
        length=length,
        weld_time=length / model.weld_speed,
    )


def split_lineage_at_boundary(
    lineage: Lineage,
    boundary: float,
    *,
    model: MotionModel = DEFAULT_MOTION_MODEL,
    eps: float = GEOMETRY_EPS,
) -> Tuple[ActiveTask, ...]:
    """Apply the standard splitter's x-boundary and half-open tie semantics."""
    x1, x2 = lineage.start_point[0], lineage.end_point[0]
    product = (x1 - boundary) * (x2 - boundary)
    if abs(x2 - x1) > eps and product < -(eps * eps):
        cut = (boundary - x1) / (x2 - x1)
        # The standard splitter de-duplicates its *parameter* list with the
        # same eps before constructing geometry.  An intersection within eps
        # of either endpoint is therefore absorbed and the complete segment is
        # assigned by the half-open ownership rule below.
        if eps < cut < 1.0 - eps:
            # A strict interior intersection has one endpoint on each side;
            # the original first endpoint determines the first child's side.
            # Do not apply eps again here: doing so flips a valid ~eps child.
            first_side = "L" if x1 < boundary else "R"
            second_side = "R" if first_side == "L" else "L"
            tasks = (
                _active_task(lineage, first_side, 0.0, cut, model, eps),
                _active_task(lineage, second_side, cut, 1.0, model, eps),
            )
            return tuple(task for task in tasks if task is not None)
        if cut > eps:
            # _unique_parameters keeps the near-one cut (because it is far
            # from zero) and then absorbs the terminal 1.0.  Preserve that
            # frozen, asymmetric endpoint rule exactly, including its tiny
            # reported length loss.
            side = "L" if x1 < boundary else "R"
            task = _active_task(lineage, side, 0.0, cut, model, eps)
            return () if task is None else (task,)

    left = x1 <= boundary + eps and x2 <= boundary + eps
    right = x1 >= boundary - eps and x2 >= boundary - eps
    if not left and not right:
        raise ValueError(
            f"lineage {lineage.lineage_id} is unassigned at boundary {boundary}"
        )
    # Shared-boundary segments belong to the left, matching min(region).
    side = "L" if left else "R"
    task = _active_task(lineage, side, 0.0, 1.0, model, eps)
    return () if task is None else (task,)


def decode_active_tasks(
    lineages: Sequence[Lineage],
    x_up: float,
    x_low: float,
    config: CPLRDecoderConfig,
) -> Tuple[ActiveTask, ...]:
    tasks = []
    for lineage in lineages:
        boundary = x_up if lineage.half_region == "upper" else x_low
        tasks.extend(
            split_lineage_at_boundary(
                lineage, boundary, model=config.motion_model, eps=config.eps
            )
        )
    return tuple(tasks)


def order_active_tasks(
    tasks: Sequence[ActiveTask], chromosome: CPLRChromosome
) -> Tuple[Tuple[ActiveTask, ...], ...]:
    key_map = chromosome.key_mapping()
    routes = []
    for robot_id in range(4):
        route = [task for task in tasks if task.robot_id == robot_id]
        missing = [task.lineage_side_id for task in route if task.lineage_side_id not in key_map]
        if missing:
            raise ValueError(f"active lineage-side keys missing from chromosome: {missing[:3]}")
        route.sort(key=lambda task: (key_map[task.lineage_side_id], task.lineage_side_id))
        routes.append(tuple(route))
    return tuple(routes)


def evaluate_routes(
    routes: Sequence[Sequence[ActiveTask]], model: MotionModel
) -> Tuple[RouteStatistics, ...]:
    results = []
    for robot_id, route in enumerate(routes):
        welds = [task.to_weld() for task in route]
        _, stats = evaluate_order(welds, tuple(range(len(welds))), model)
        results.append(
            RouteStatistics(
                robot_id=robot_id,
                total_time=float(stats["total_time"]),
                weld_time=float(stats["total_weld_time"]),
                travel_time=float(stats["total_travel_time"]),
                idle_distance=float(stats["total_idle_distance"]),
                direction_flags=tuple(bool(value) for value in stats["direction_flags"]),
            )
        )
    return tuple(results)


def aggregate_system_metrics(
    route_stats: Sequence[RouteStatistics],
    normalization_spec: Mapping[str, object] | None = None,
) -> SystemMetrics:
    totals = [item.total_time for item in route_stats]
    makespan = max(totals, default=0.0)
    imbalance = max(totals, default=0.0) - min(totals, default=0.0)
    idle = sum(item.idle_distance for item in route_stats)
    if normalization_spec is None:
        fitness = 0.7 * makespan + 0.2 * imbalance + 0.1 * idle
    else:
        ideal = normalization_spec["ideal"]  # type: ignore[index]
        scale = normalization_spec["scale"]  # type: ignore[index]
        weights = normalization_spec.get("weights", (0.7, 0.2, 0.1))
        raw = (makespan, imbalance, idle)
        names = ("makespan", "load_imbalance", "idle_distance")
        components = [
            max(0.0, (value - float(ideal[name])) / float(scale[name]))  # type: ignore[index]
            for value, name in zip(raw, names)
        ]
        fitness = sum(float(weight) * value for weight, value in zip(weights, components))  # type: ignore[arg-type]
    return SystemMetrics(
        makespan=makespan,
        load_imbalance=imbalance,
        total_weld_time=sum(item.weld_time for item in route_stats),
        total_travel_time=sum(item.travel_time for item in route_stats),
        total_idle_distance=idle,
        sum_robot_total_time=sum(totals),
        fitness=fitness,
    )


def decode_chromosome(
    lineages: Sequence[Lineage],
    chromosome: CPLRChromosome,
    config: CPLRDecoderConfig = CPLRDecoderConfig(),
    normalization_spec: Mapping[str, object] | None = None,
) -> DecodedCPLRState:
    expected = set(all_side_ids(lineages))
    actual = {key for key, _ in chromosome.side_keys}
    if actual != expected:
        raise ValueError(
            f"chromosome key set mismatch: missing={sorted(expected-actual)[:3]}, "
            f"extra={sorted(actual-expected)[:3]}"
        )
    x_up, x_low = decode_boundaries(chromosome, config)
    tasks = decode_active_tasks(lineages, x_up, x_low, config)
    routes = order_active_tasks(tasks, chromosome)
    route_stats = evaluate_routes(routes, config.motion_model)
    signatures = tuple(
        tuple(task.lineage_side_id for task in route) for route in routes
    )
    flattened = [key for route in signatures for key in route]
    task_length = sum(task.length for task in tasks)
    lineage_length = sum(lineage.length for lineage in lineages)
    length_error = abs(task_length - lineage_length)
    tolerance = max(config.eps, config.eps * max(1.0, lineage_length))
    validation = StructuralValidation(
        unique_assignment=len(tasks) == len(flattened),
        no_duplicate_side_ids=len(flattened) == len(set(flattened)),
        no_missing_lineage_length=length_error <= tolerance,
        route_coverage_complete=set(flattened) == {task.lineage_side_id for task in tasks},
        length_error=length_error,
    )
    return DecodedCPLRState(
        x_up=x_up,
        x_low=x_low,
        active_tasks=tasks,
        route_signatures=signatures,
        route_statistics=route_stats,
        direction_flags=tuple(item.direction_flags for item in route_stats),
        system_metrics=aggregate_system_metrics(route_stats, normalization_spec),
        structural_validation=validation,
    )
