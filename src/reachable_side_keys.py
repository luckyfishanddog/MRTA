"""Exact event-enumeration preprocessing for reachable CPLR side keys."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence, Tuple

from continuous_lineage_decoder import CPLRDecoderConfig, LineageSideId, split_lineage_at_boundary
from lineage_presplit import Lineage, with_reachability


@dataclass(frozen=True)
class ReachableSideIndex:
    keys: Tuple[LineageSideId, ...]
    total_unpruned_key_count: int
    pruned_key_count: int
    upper_domain: Tuple[float, float]
    lower_domain: Tuple[float, float]
    eps: float

    @property
    def pruning_ratio(self) -> float:
        if self.total_unpruned_key_count == 0:
            return 0.0
        return self.pruned_key_count / self.total_unpruned_key_count


def _candidate_boundaries(
    lineage: Lineage, lo: float, hi: float, eps: float
) -> Tuple[float, ...]:
    events = {float(lo), float(hi)}
    for x in (lineage.start_point[0], lineage.end_point[0]):
        for value in (x - 2.0 * eps, x - eps, x, x + eps, x + 2.0 * eps):
            if lo <= value <= hi:
                events.add(float(value))
    ordered = sorted(events)
    for left, right in zip(ordered[:-1], ordered[1:]):
        if right > left:
            events.add((left + right) * 0.5)
    return tuple(sorted(events))


def build_reachable_side_index(
    lineages: Sequence[Lineage],
    config: CPLRDecoderConfig = CPLRDecoderConfig(),
) -> ReachableSideIndex:
    """Keep a side iff at least one legal boundary makes it nonzero-active.

    Activity changes only at endpoint/tolerance events.  Evaluating every
    event, its tolerance neighbours, and each open interval therefore covers
    the complete one-dimensional legal boundary domain.
    """
    reachable = set()
    for lineage in lineages:
        domain = (
            (config.upper_boundary_min, config.upper_boundary_max)
            if lineage.half_region == "upper"
            else (config.lower_boundary_min, config.lower_boundary_max)
        )
        for boundary in _candidate_boundaries(lineage, *domain, config.eps):
            try:
                tasks = split_lineage_at_boundary(
                    lineage,
                    boundary,
                    model=config.motion_model,
                    eps=config.eps,
                )
            except ValueError:
                # The frozen standard splitter has microscopic tolerance gaps
                # near some endpoints.  Such a boundary produces no assigned
                # standard task and therefore cannot prove a side reachable.
                continue
            for task in tasks:
                reachable.add(task.lineage_side_id)
    keys = tuple(sorted(reachable))
    total = 2 * len(lineages)
    return ReachableSideIndex(
        keys=keys,
        total_unpruned_key_count=total,
        pruned_key_count=total - len(keys),
        upper_domain=(config.upper_boundary_min, config.upper_boundary_max),
        lower_domain=(config.lower_boundary_min, config.lower_boundary_max),
        eps=config.eps,
    )


def apply_reachable_side_index(
    lineages: Iterable[Lineage], index: ReachableSideIndex
) -> Tuple[Lineage, ...]:
    return with_reachability(
        lineages, ((key.lineage_id, key.side) for key in index.keys)
    )
