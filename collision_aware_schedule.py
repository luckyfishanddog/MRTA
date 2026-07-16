# -*- coding: utf-8 -*-
"""Boundary-local x-time collision audit utilities for Phase 9A.

This module is intentionally independent from all three official solver
objective functions. It builds sampled x-t traces for already-directed robot
routes, audits only the two partition boundaries, and estimates waiting time
needed to reduce local boundary conflicts.
"""

import argparse
import math
from dataclasses import dataclass, replace
from typing import Dict, List, Optional, Sequence, Tuple

import MRTA_GA_ACO as gaaco


@dataclass
class XTraceSegment:
    start_t: float
    end_t: float
    start_x: float
    end_x: float
    phase: str
    robot_id: int
    weld_id: str = ""

    def x_at(self, t: float) -> float:
        if self.end_t <= self.start_t:
            return self.end_x
        if t <= self.start_t:
            return self.start_x
        if t >= self.end_t:
            return self.end_x
        ratio = (t - self.start_t) / (self.end_t - self.start_t)
        return self.start_x + ratio * (self.end_x - self.start_x)


def _point_x(point: Tuple[float, float, float]) -> float:
    return float(point[0])


def _trace_start_time(trace: Sequence[XTraceSegment]) -> Optional[float]:
    if not trace:
        return None
    return min(seg.start_t for seg in trace)


def _trace_completion_time(trace: Sequence[XTraceSegment]) -> float:
    if not trace:
        return 0.0
    return max(seg.end_t for seg in trace)


def _x_at_trace(trace: Sequence[XTraceSegment], t: float) -> Optional[float]:
    """Return x only while the robot has an explicit active trace segment."""
    if not trace:
        return None
    for seg in trace:
        if seg.start_t <= t <= seg.end_t:
            return seg.x_at(t)
    return None


def _sample_times(end_t: float, dt: float) -> List[float]:
    if dt <= 0:
        raise ValueError("dt must be positive")
    if end_t <= 0:
        return [0.0]
    sample_count = int(math.floor(end_t / dt))
    samples = [i * dt for i in range(sample_count + 1)]
    if not math.isclose(samples[-1], end_t, rel_tol=1e-9, abs_tol=1e-9):
        samples.append(end_t)
    return samples


def build_robot_x_trace(
    robot_id,
    directed_seq,
    start_time=0.0,
):
    """Convert a directed welding sequence into a piecewise-linear x-t trace."""
    if not directed_seq:
        return [], {
            "completion_time": float(start_time),
            "total_weld_time": 0.0,
            "total_travel_time": 0.0,
            "total_idle_distance": 0.0,
        }

    trace: List[XTraceSegment] = []
    t = float(start_time)
    total_weld_time = 0.0
    total_travel_time = 0.0
    total_idle_distance = 0.0
    prev_task = None

    for task in directed_seq:
        weld_id = str(getattr(task, "wid", getattr(task, "id", "")))
        start_point = task.start_point()
        end_point = task.end_point()
        start_x = _point_x(start_point)
        end_x = _point_x(end_point)

        if prev_task is not None:
            prev_end = prev_task.end_point()
            travel_duration = gaaco.travel_time(prev_end, start_point)
            travel_distance = gaaco.travel_distance(prev_end, start_point)
            if travel_duration > 0:
                trace.append(
                    XTraceSegment(
                        start_t=t,
                        end_t=t + travel_duration,
                        start_x=_point_x(prev_end),
                        end_x=start_x,
                        phase="travel",
                        robot_id=robot_id,
                        weld_id=weld_id,
                    )
                )
            t += travel_duration
            total_travel_time += travel_duration
            total_idle_distance += travel_distance

        setup_duration = gaaco.setup_time_value()
        if setup_duration > 0:
            trace.append(
                XTraceSegment(
                    start_t=t,
                    end_t=t + setup_duration,
                    start_x=start_x,
                    end_x=start_x,
                    phase="setup",
                    robot_id=robot_id,
                    weld_id=weld_id,
                )
            )
        t += setup_duration

        weld_duration = gaaco.weld_process_time(task)
        if weld_duration > 0:
            trace.append(
                XTraceSegment(
                    start_t=t,
                    end_t=t + weld_duration,
                    start_x=start_x,
                    end_x=end_x,
                    phase="weld",
                    robot_id=robot_id,
                    weld_id=weld_id,
                )
            )
        t += weld_duration

        post_duration = gaaco.post_time_value()
        if post_duration > 0:
            trace.append(
                XTraceSegment(
                    start_t=t,
                    end_t=t + post_duration,
                    start_x=end_x,
                    end_x=end_x,
                    phase="post",
                    robot_id=robot_id,
                    weld_id=weld_id,
                )
            )
        t += post_duration
        total_weld_time += setup_duration + weld_duration + post_duration
        prev_task = task

    return trace, {
        "completion_time": t,
        "total_weld_time": total_weld_time,
        "total_travel_time": total_travel_time,
        "total_idle_distance": total_idle_distance,
    }


def build_all_robot_x_traces(robot_directed_sequences):
    if len(robot_directed_sequences) != 4:
        raise ValueError("robot_directed_sequences must contain exactly 4 robot sequences")
    traces = []
    base_completion_times = []
    for robot_id, directed_seq in enumerate(robot_directed_sequences):
        trace, stats = build_robot_x_trace(robot_id, directed_seq)
        traces.append(trace)
        base_completion_times.append(stats.get("completion_time", 0.0))
    return traces, base_completion_times


def is_in_boundary_band_pair(
    x_left,
    x_right,
    boundary_x,
    band_width,
):
    left_near = x_left >= boundary_x - band_width
    right_near = x_right <= boundary_x + band_width
    return left_near or right_near


def check_pair_collision_violations(
    left_trace,
    right_trace,
    boundary_x,
    safety_distance,
    band_width,
    dt=1.0,
):
    end_t = max(_trace_completion_time(left_trace), _trace_completion_time(right_trace))
    violation_count = 0
    min_separation = math.inf
    first_violation_time = None
    checked_sample_count = 0

    for t in _sample_times(end_t, dt):
        x_left = _x_at_trace(left_trace, t)
        x_right = _x_at_trace(right_trace, t)
        if x_left is None or x_right is None:
            continue
        if not is_in_boundary_band_pair(x_left, x_right, boundary_x, band_width):
            continue

        checked_sample_count += 1
        separation = x_right - x_left
        min_separation = min(min_separation, separation)
        if separation < safety_distance:
            violation_count += 1
            if first_violation_time is None:
                first_violation_time = t

    return {
        "violation_count": violation_count,
        "min_separation": min_separation,
        "first_violation_time": first_violation_time,
        "checked_sample_count": checked_sample_count,
    }


def _copy_trace(trace: Sequence[XTraceSegment]) -> List[XTraceSegment]:
    return [replace(seg) for seg in trace]


def _delay_trace_at_time(
    trace: Sequence[XTraceSegment],
    t: float,
    delay: float,
) -> List[XTraceSegment]:
    if delay <= 0:
        return _copy_trace(trace)
    adjusted = _copy_trace(trace)
    delay_index = None
    for idx, seg in enumerate(adjusted):
        if seg.start_t <= t <= seg.end_t:
            delay_index = idx
            break
        if t < seg.start_t:
            delay_index = idx
            break
    if delay_index is None:
        return adjusted
    for idx in range(delay_index, len(adjusted)):
        adjusted[idx].start_t += delay
        adjusted[idx].end_t += delay
    return adjusted


def apply_pairwise_waiting_schedule(
    left_trace,
    right_trace,
    boundary_x,
    safety_distance,
    band_width,
    dt=1.0,
    priority="left_first",
    max_wait_iterations=10000,
):
    if priority not in ("left_first", "right_first"):
        raise ValueError("priority must be 'left_first' or 'right_first'")
    if dt <= 0:
        raise ValueError("dt must be positive")

    adjusted_left = _copy_trace(left_trace)
    adjusted_right = _copy_trace(right_trace)
    added_wait_left = 0.0
    added_wait_right = 0.0
    iterations = 0

    while iterations < max_wait_iterations:
        before = check_pair_collision_violations(
            adjusted_left,
            adjusted_right,
            boundary_x,
            safety_distance,
            band_width,
            dt,
        )
        if before["violation_count"] == 0:
            break

        conflict_t = before["first_violation_time"]
        if conflict_t is None:
            break
        if priority == "left_first":
            adjusted_right = _delay_trace_at_time(adjusted_right, conflict_t, dt)
            added_wait_right += dt
        else:
            adjusted_left = _delay_trace_at_time(adjusted_left, conflict_t, dt)
            added_wait_left += dt
        iterations += 1

    after = check_pair_collision_violations(
        adjusted_left,
        adjusted_right,
        boundary_x,
        safety_distance,
        band_width,
        dt,
    )
    wait_stats = {
        "priority": priority,
        "added_wait_left": added_wait_left,
        "added_wait_right": added_wait_right,
        "total_added_wait": added_wait_left + added_wait_right,
        "violation_count_after": after["violation_count"],
        "min_separation_after": after["min_separation"],
        "checked_sample_count_after": after["checked_sample_count"],
        "max_wait_iterations_reached": iterations >= max_wait_iterations
        and after["violation_count"] > 0,
    }
    return adjusted_left, adjusted_right, wait_stats


def generate_conflict_aware_pair_schedule(
    left_trace,
    right_trace,
    boundary_x,
    safety_distance,
    band_width,
    dt=1.0,
):
    candidates = []
    for priority in ("left_first", "right_first"):
        adj_left, adj_right, wait_stats = apply_pairwise_waiting_schedule(
            left_trace,
            right_trace,
            boundary_x,
            safety_distance,
            band_width,
            dt=dt,
            priority=priority,
        )
        pair_makespan = max(_trace_completion_time(adj_left), _trace_completion_time(adj_right))
        wait_stats = dict(wait_stats)
        wait_stats["pair_makespan"] = pair_makespan
        candidates.append((pair_makespan, wait_stats["total_added_wait"], adj_left, adj_right, wait_stats))

    candidates.sort(key=lambda item: (item[0], item[1]))
    _, _, best_left, best_right, best_stats = candidates[0]
    best_stats["selected_priority"] = best_stats["priority"]
    return best_left, best_right, best_stats


def audit_boundary_collisions_for_four_robots(
    robot_directed_sequences,
    x_up,
    x_low,
    safety_distance=0.5,
    band_width=None,
    dt=1.0,
):
    if band_width is None:
        band_width = safety_distance

    traces, base_completion_times = build_all_robot_x_traces(robot_directed_sequences)
    base_makespan = max(base_completion_times) if base_completion_times else 0.0

    upper_before = check_pair_collision_violations(
        traces[0], traces[1], x_up, safety_distance, band_width, dt
    )
    upper_left, upper_right, upper_wait = generate_conflict_aware_pair_schedule(
        traces[0], traces[1], x_up, safety_distance, band_width, dt
    )
    upper_after = check_pair_collision_violations(
        upper_left, upper_right, x_up, safety_distance, band_width, dt
    )

    lower_before = check_pair_collision_violations(
        traces[2], traces[3], x_low, safety_distance, band_width, dt
    )
    lower_left, lower_right, lower_wait = generate_conflict_aware_pair_schedule(
        traces[2], traces[3], x_low, safety_distance, band_width, dt
    )
    lower_after = check_pair_collision_violations(
        lower_left, lower_right, x_low, safety_distance, band_width, dt
    )

    adjusted_completion_times = [
        _trace_completion_time(upper_left),
        _trace_completion_time(upper_right),
        _trace_completion_time(lower_left),
        _trace_completion_time(lower_right),
    ]
    collision_adjusted_makespan = max(adjusted_completion_times) if adjusted_completion_times else 0.0
    upper_wait_time = upper_wait["total_added_wait"]
    lower_wait_time = lower_wait["total_added_wait"]

    return {
        "collision_check_mode": "boundary_x_time_pairwise",
        "safety_distance": safety_distance,
        "band_width": band_width,
        "dt": dt,
        "base_makespan": base_makespan,
        "collision_adjusted_makespan": collision_adjusted_makespan,
        "collision_wait_time_total": upper_wait_time + lower_wait_time,
        "added_waiting_time": upper_wait_time + lower_wait_time,
        "conflict_count": (
            upper_before["violation_count"] + lower_before["violation_count"]
        ),
        "unresolved_conflict_count": (
            upper_after["violation_count"] + lower_after["violation_count"]
        ),
        "upper_pair_wait_time": upper_wait_time,
        "lower_pair_wait_time": lower_wait_time,
        "upper_pair_violation_count_before": upper_before["violation_count"],
        "lower_pair_violation_count_before": lower_before["violation_count"],
        "upper_pair_violation_count_after": upper_after["violation_count"],
        "lower_pair_violation_count_after": lower_after["violation_count"],
        "upper_pair_min_separation_before": upper_before["min_separation"],
        "lower_pair_min_separation_before": lower_before["min_separation"],
        "upper_pair_min_separation_after": upper_after["min_separation"],
        "lower_pair_min_separation_after": lower_after["min_separation"],
        "upper_pair_selected_priority": upper_wait["selected_priority"],
        "lower_pair_selected_priority": lower_wait["selected_priority"],
        "upper_pair_checked_sample_count_before": upper_before["checked_sample_count"],
        "lower_pair_checked_sample_count_before": lower_before["checked_sample_count"],
        "upper_pair_checked_sample_count_after": upper_after["checked_sample_count"],
        "lower_pair_checked_sample_count_after": lower_after["checked_sample_count"],
    }


def make_directed_sequences_from_robot_orders(
    robots,
    robot_orders,
    split_timing_mode="parent_aware",
):
    if len(robots) != len(robot_orders):
        raise ValueError("robots and robot_orders must have the same length")
    robot_directed_sequences = []
    for robot_welds, order in zip(robots, robot_orders):
        directed_seq, _ = gaaco.optimize_directions_for_order(
            robot_welds,
            order,
            split_timing_mode,
        )
        robot_directed_sequences.append(directed_seq)
    return robot_directed_sequences


def _make_hold_trace(robot_id: int, x: float, start_t: float, end_t: float) -> List[XTraceSegment]:
    return [
        XTraceSegment(
            start_t=start_t,
            end_t=end_t,
            start_x=x,
            end_x=x,
            phase="weld",
            robot_id=robot_id,
            weld_id=f"hold_{robot_id}",
        )
    ]


def _self_check_x_trace_generation():
    z = getattr(gaaco, "DEFAULT_WELD_Z_M", 0.1)
    welds = [
        gaaco.Weld("sc_a", 0.0, 0.0, z, 1.0, 0.0, z),
        gaaco.Weld("sc_b", 1.5, 0.0, z, 2.5, 0.0, z),
    ]
    directed_seq, _ = gaaco.optimize_directions_for_order(welds, [0, 1], "conservative")
    trace, stats = build_robot_x_trace(0, directed_seq)
    assert trace, "trace should not be empty"
    assert stats["completion_time"] > 0, "completion_time should be positive"
    mid_t = (trace[0].start_t + trace[0].end_t) / 2.0
    assert isinstance(trace[0].x_at(mid_t), float), "x_at should return float"


def _self_check_pair_collision_detection():
    left = _make_hold_trace(0, 9.8, 0.0, 5.0)
    right = _make_hold_trace(1, 10.1, 0.0, 5.0)
    stats = check_pair_collision_violations(
        left,
        right,
        boundary_x=10.0,
        safety_distance=0.5,
        band_width=0.5,
        dt=0.5,
    )
    assert stats["violation_count"] > 0, "expected a boundary distance violation"
    assert stats["min_separation"] < 0.5, "expected separation below safety distance"


def _self_check_pair_waiting_schedule():
    left = _make_hold_trace(0, 9.8, 0.0, 5.0)
    right = _make_hold_trace(1, 10.1, 0.0, 5.0)
    before = check_pair_collision_violations(
        left,
        right,
        boundary_x=10.0,
        safety_distance=0.5,
        band_width=0.5,
        dt=0.5,
    )
    _, _, wait_stats = apply_pairwise_waiting_schedule(
        left,
        right,
        boundary_x=10.0,
        safety_distance=0.5,
        band_width=0.5,
        dt=0.5,
        priority="left_first",
    )
    assert wait_stats["total_added_wait"] > 0, "waiting time should be added"
    assert (
        wait_stats["violation_count_after"] == 0
        or wait_stats["violation_count_after"] < before["violation_count"]
    ), "waiting should remove or reduce violations"


def _self_check_four_robot_boundary_audit():
    z = getattr(gaaco, "DEFAULT_WELD_Z_M", 0.1)
    seq0 = [gaaco.DirectedWeld(gaaco.Weld("r0_near", 9.8, 7.0, z, 9.9, 7.0, z))]
    seq1 = [gaaco.DirectedWeld(gaaco.Weld("r1_near", 10.1, 7.0, z, 10.2, 7.0, z))]
    seq2 = [gaaco.DirectedWeld(gaaco.Weld("r2_safe", 1.0, 2.0, z, 1.2, 2.0, z))]
    seq3 = [gaaco.DirectedWeld(gaaco.Weld("r3_safe", 14.0, 2.0, z, 14.2, 2.0, z))]
    stats = audit_boundary_collisions_for_four_robots(
        [seq0, seq1, seq2, seq3],
        x_up=10.0,
        x_low=7.0,
        safety_distance=0.5,
        band_width=0.5,
        dt=0.5,
    )
    assert stats["upper_pair_violation_count_before"] > 0, "upper pair should conflict"
    assert stats["lower_pair_violation_count_before"] == 0, "lower pair should be safe"
    assert (
        stats["collision_adjusted_makespan"] >= stats["base_makespan"]
    ), "adjusted makespan should not be smaller than base makespan"


def run_self_checks():
    _self_check_x_trace_generation()
    _self_check_pair_collision_detection()
    _self_check_pair_waiting_schedule()
    _self_check_four_robot_boundary_audit()


def main():
    parser = argparse.ArgumentParser(description="Phase 9A boundary collision audit helpers")
    parser.add_argument("--self-check", action="store_true", help="run local self-checks")
    args = parser.parse_args()
    if args.self_check:
        run_self_checks()
        print("Phase 9A boundary collision self-checks passed")


if __name__ == "__main__":
    main()
