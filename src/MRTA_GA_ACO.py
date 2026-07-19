# -*- coding: utf-8 -*-
import sys
import os
import math
import random
import time
import argparse
import csv
import json
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d import Axes3D
from typing import List, Tuple, Dict
from generate_welds import (
    PLATFORM_W_M, PLATFORM_H_M, Weld, make_subweld,
    DEFAULT_WELD_Z_M, DEFAULT_MIN_WELD_LENGTH_M,
    instance_metrics, load_solver_weld_input, resolve_solver_seed,
)
from objective_normalization import (
    BASELINE_ALGORITHM, LEGACY_MODE, OFFICIAL_MODE,
    normalized_components, normalized_objective, validate_normalization_spec,
)
from solver_output_metrics import (
    IncumbentTrace, build_robot_metrics, build_standard_result,
    build_system_metrics, flatten_robot_metrics,
)

# ---------- 参数配置 ----------
DEFAULT_GROUP_COUNT = 8
DEFAULT_SEED = 42
DEFAULT_OUTPUT_IMAGE = "partition_result.png"
SOLVER_NAME = "GA+ACO"
FIXED_Y_H = 0.0

LEGACY_EMPTY_PLANE_Z = 1.0
LEGACY_WELD_SPEED = 0.03
LEGACY_TRAVEL_SPEED = 0.05
LEGACY_ACC = 2.0

CORRECTED_WELD_SPEED = 0.0108
CORRECTED_TRAVEL_SPEED = 0.20
CORRECTED_ACC = 0.50
CORRECTED_SAFE_Z = 0.30
CORRECTED_SETUP_TIME = 0.0
CORRECTED_POST_TIME = 0.0

TIME_MODEL = "corrected"
ASSIGNMENT_MODE = "split"
SPLIT_TIMING_MODE = "parent_aware"
DIRECTION_MODE = "bidirectional"

# Backward-compatible aliases. Time calculations should use get_active_time_params().
EMPTY_PLANE_Z = LEGACY_EMPTY_PLANE_Z
WELD_SPEED = LEGACY_WELD_SPEED
TRAVEL_SPEED = LEGACY_TRAVEL_SPEED
ACC = LEGACY_ACC

# 遗传算法参数
# 40-50 welds call ACO inside every GA evaluation, so defaults favor stable runtime.
POP_SIZE = 20
MAX_GEN = 30
ELITE_SIZE = 2
PC_MIN, PC_MAX = 0.6, 0.9
PM_MIN, PM_MAX = 0.05, 0.2
ETA_C = 20
ETA_M = 100

# 目标函数权重
WEIGHT_MAKESPAN = 0.70
WEIGHT_LOAD = 0.20
WEIGHT_DISTANCE = 0.10

# 参考值（归一化用）
REF_MAKESPAN = 1.0
REF_LOAD = 1.0
REF_DISTANCE = 1.0
OBJECTIVE_NORMALIZATION_MODE = LEGACY_MODE
OBJECTIVE_NORMALIZATION_SPEC = None


def build_external_normalization_spec(args):
    """Build and validate the externally fixed official normalization payload."""
    ideal = (args.ideal_makespan, args.ideal_load_imbalance, args.ideal_distance)
    baseline = (args.baseline_makespan, args.baseline_load_imbalance, args.baseline_distance)
    scale = (args.scale_makespan, args.scale_load_imbalance, args.scale_distance)
    official_values = ideal + baseline + scale
    refs = (args.ref_makespan, args.ref_load, args.ref_distance)
    if args.normalization_mode == OFFICIAL_MODE:
        if any(value is not None for value in refs):
            raise ValueError("--ref-* cannot be mixed with ideal_baseline_range_v1")
        if not all(value is not None for value in official_values):
            raise ValueError("official normalization requires all ideal, baseline, and scale values")
        spec = {
            "mode": OFFICIAL_MODE,
            "ideal": dict(zip(("makespan", "load_imbalance", "idle_distance"), map(float, ideal))),
            "baseline": {"algorithm": BASELINE_ALGORITHM, "metrics": dict(zip(("makespan", "load_imbalance", "idle_distance"), map(float, baseline)))},
            "scale": dict(zip(("makespan", "load_imbalance", "idle_distance"), map(float, scale))),
            "floors": {},
            "weights": [WEIGHT_MAKESPAN, WEIGHT_LOAD, WEIGHT_DISTANCE],
        }
        validate_normalization_spec(spec)
        return spec
    if any(value is not None for value in official_values):
        raise ValueError("ideal/baseline/scale arguments require ideal_baseline_range_v1")
    return None


def validate_objective_parameters(
    weight_makespan: float,
    weight_load: float,
    weight_distance: float,
    ref_makespan: float,
    ref_load: float,
    ref_distance: float,
) -> None:
    """Validate the normalized weighted objective in equations (19)-(20)."""
    weights = (weight_makespan, weight_load, weight_distance)
    references = (ref_makespan, ref_load, ref_distance)
    if any(weight < 0.0 for weight in weights):
        raise ValueError("objective weights must be non-negative")
    if not math.isclose(sum(weights), 1.0, rel_tol=0.0, abs_tol=1e-12):
        raise ValueError("objective weights must sum to 1")
    if any(reference <= 0.0 or not math.isfinite(reference) for reference in references):
        raise ValueError("objective reference values must be finite and positive")


def derive_objective_references(welds: List[Weld]) -> Tuple[float, float, float]:
    """Build fixed, deterministic positive scales for equation (19).

    The references depend only on the input instance and physical parameters,
    never on a search generation or a candidate partition.  Therefore every
    candidate in one run is compared with the same objective function.
    """
    params = get_active_time_params()
    total_weld_time = sum(weld.length / params["weld_speed"] for weld in welds)
    ref_makespan = max(total_weld_time / 4.0, 1e-12)
    ref_load = ref_makespan

    max_vertical = max(
        (abs(params["safe_z"] - point[2]) for weld in welds for point in weld.endpoints()),
        default=abs(params["safe_z"]),
    )
    max_empty_move = math.hypot(PLATFORM_W_M, PLATFORM_H_M) + 2.0 * max_vertical
    transition_scale = max(len(welds) - 4, 1)
    ref_distance = max(transition_scale * max_empty_move, 1e-12)
    return ref_makespan, ref_load, ref_distance


def resolve_objective_references(
    welds: List[Weld],
    ref_makespan: float = None,
    ref_load: float = None,
    ref_distance: float = None,
) -> Tuple[float, float, float]:
    """Use three explicit references together, or derive all three."""
    supplied = (ref_makespan, ref_load, ref_distance)
    if any(value is not None for value in supplied):
        if not all(value is not None for value in supplied):
            raise ValueError("provide all three objective references, or none")
        refs = tuple(float(value) for value in supplied)
    else:
        refs = derive_objective_references(welds)
    validate_objective_parameters(
        WEIGHT_MAKESPAN, WEIGHT_LOAD, WEIGHT_DISTANCE, *refs
    )
    return refs


def weighted_objective(
    makespan: float,
    load_imb: float,
    total_distance: float,
    ref_makespan: float,
    ref_load: float,
    ref_distance: float,
) -> float:
    """Evaluate exactly the normalized weighted objective in equation (19)."""
    if OBJECTIVE_NORMALIZATION_MODE == OFFICIAL_MODE:
        if OBJECTIVE_NORMALIZATION_SPEC is None:
            raise ValueError("official objective normalization is not configured")
        return normalized_objective(
            {"makespan": makespan, "load_imbalance": load_imb, "idle_distance": total_distance},
            OBJECTIVE_NORMALIZATION_SPEC,
        )
    validate_objective_parameters(
        WEIGHT_MAKESPAN,
        WEIGHT_LOAD,
        WEIGHT_DISTANCE,
        ref_makespan,
        ref_load,
        ref_distance,
    )
    return (
        WEIGHT_MAKESPAN * makespan / ref_makespan
        + WEIGHT_LOAD * load_imb / ref_load
        + WEIGHT_DISTANCE * total_distance / ref_distance
    )

# ---------- ACO 参数 ----------
ACO_ANT_COUNT = 15           # 蚂蚁数量
ACO_MAX_ITER = 20            # 最大迭代次数（可根据焊缝数量动态调整）
ACO_ALPHA = 1.0              # 信息素重要程度
ACO_BETA = 2.0               # 启发式信息重要程度
ACO_RHO = 0.5                # 信息素蒸发率
ACO_Q = 1.0                  # 信息素增加强度

# ---------- 梯形加减速时间计算 ----------
def get_active_time_params():
    if TIME_MODEL == "legacy":
        return {
            "time_model": TIME_MODEL,
            "weld_speed": LEGACY_WELD_SPEED,
            "travel_speed": LEGACY_TRAVEL_SPEED,
            "acc": LEGACY_ACC,
            "safe_z": LEGACY_EMPTY_PLANE_Z,
            "setup_time": 0.0,
            "post_time": 0.0,
        }
    if TIME_MODEL == "corrected":
        return {
            "time_model": TIME_MODEL,
            "weld_speed": CORRECTED_WELD_SPEED,
            "travel_speed": CORRECTED_TRAVEL_SPEED,
            "acc": CORRECTED_ACC,
            "safe_z": CORRECTED_SAFE_Z,
            "setup_time": CORRECTED_SETUP_TIME,
            "post_time": CORRECTED_POST_TIME,
        }
    raise ValueError(f"Unknown TIME_MODEL: {TIME_MODEL}")

def trapezoidal_time_legacy(distance: float, v_max: float, a: float) -> float:
    if distance <= 0:
        return 0.0
    d_acc = v_max * v_max / a
    if distance >= 2 * d_acc:
        t_acc = v_max / a
        t_const = (distance - 2 * d_acc) / v_max
        return 2 * t_acc + t_const
    else:
        return 2 * math.sqrt(distance / a)

def trapezoidal_time(distance: float, v_max: float, a: float) -> float:
    if distance <= 0:
        return 0.0
    if v_max <= 0 or a <= 0:
        raise ValueError("v_max and a must be positive")
    d_acc = v_max * v_max / (2 * a)
    if distance >= 2 * d_acc:
        t_acc = v_max / a
        t_const = (distance - 2 * d_acc) / v_max
        return 2 * t_acc + t_const
    else:
        return 2 * math.sqrt(distance / a)

def travel_time(p1: Tuple[float,float,float], p2: Tuple[float,float,float]) -> float:
    params = get_active_time_params()
    time_func = trapezoidal_time_legacy if params["time_model"] == "legacy" else trapezoidal_time
    x1, y1, z1 = p1
    x2, y2, z2 = p2
    dh = math.hypot(x2 - x1, y2 - y1)
    safe_z = params["safe_z"]
    t_up = time_func(abs(safe_z - z1), params["travel_speed"], params["acc"])
    t_xy = time_func(dh, params["travel_speed"], params["acc"])
    t_down = time_func(abs(safe_z - z2), params["travel_speed"], params["acc"])
    return t_up + t_xy + t_down

def travel_distance(p1: Tuple[float,float,float], p2: Tuple[float,float,float]) -> float:
    params = get_active_time_params()
    x1, y1, z1 = p1
    x2, y2, z2 = p2
    dh = math.hypot(x2 - x1, y2 - y1)
    safe_z = params["safe_z"]
    dv = abs(safe_z - z1) + abs(safe_z - z2)
    return dh + dv

def weld_time(weld: Weld) -> float:
    params = get_active_time_params()
    return weld.length / params["weld_speed"]

def weld_process_time(weld: Weld) -> float:
    params = get_active_time_params()
    return weld.length / params["weld_speed"]

def setup_time_value() -> float:
    return 0.0

def post_time_value() -> float:
    return 0.0

def _empty_route_stats() -> Dict[str, float]:
    return {
        'total_time': 0.0,
        'total_weld_time': 0.0,
        'total_travel_time': 0.0,
        'total_idle_distance': 0.0,
        'setup_post_saved_time': 0.0,
        'merged_setup_post_count': 0,
        'pure_weld_time': 0.0,
        'setup_post_time': 0.0,
        'reversed_weld_count': 0,
        'direction_flags': [],
        'route_order': [],
        'route_task_ids': [],
    }

class DirectedWeld:
    def __init__(self, weld, reversed=False):
        self.weld = weld
        self.reversed = reversed

    def start_point(self):
        return self.weld.end_point() if self.reversed else self.weld.start_point()

    def end_point(self):
        return self.weld.start_point() if self.reversed else self.weld.end_point()

    def midpoint(self):
        return self.weld.midpoint()

    def endpoints(self):
        return (self.start_point(), self.end_point())

    @property
    def length(self):
        return self.weld.length

    @property
    def id(self):
        return self.weld.id

    @property
    def wid(self):
        return self.weld.wid

    def __getattr__(self, name):
        return getattr(self.weld, name)

def can_merge_split_setup_post(prev_weld, curr_weld, eps=1e-6):
    prev_parent = getattr(prev_weld, "parent_id", None)
    curr_parent = getattr(curr_weld, "parent_id", None)
    if prev_parent is None or curr_parent is None or prev_parent != curr_parent:
        return False
    prev_is_split = getattr(prev_weld, "is_split_segment", False)
    curr_is_split = getattr(curr_weld, "is_split_segment", False)
    if not (prev_is_split or curr_is_split or getattr(prev_weld, "segment_id", None) != getattr(curr_weld, "segment_id", None)):
        return False

    prev_start, prev_end = prev_weld.endpoints()
    curr_start, curr_end = curr_weld.endpoints()
    if math.dist(prev_end, curr_start) <= eps:
        return True
    # Phase 4B 近似：尚未实现方向选择，若任意端点相接，也视作同一原焊缝连续子段。
    endpoint_pairs = [
        (prev_start, curr_start),
        (prev_start, curr_end),
        (prev_end, curr_end),
    ]
    return any(math.dist(a, b) <= eps for a, b in endpoint_pairs)

def can_merge_split_setup_post_directed(prev_task, curr_task, eps=1e-6):
    prev_parent = getattr(prev_task, "parent_id", None)
    curr_parent = getattr(curr_task, "parent_id", None)
    if prev_parent is None or curr_parent is None or prev_parent != curr_parent:
        return False
    prev_is_split = getattr(prev_task, "is_split_segment", False)
    curr_is_split = getattr(curr_task, "is_split_segment", False)
    if not (prev_is_split or curr_is_split or getattr(prev_task, "segment_id", None) != getattr(curr_task, "segment_id", None)):
        return False
    return math.dist(prev_task.end_point(), curr_task.start_point()) <= eps

def _compute_route_stats_conservative(seq: List[Weld]) -> Dict[str, float]:
    """给定焊接顺序，计算详细统计（总时间、焊接时间、空载时间、空载距离）"""
    if not seq:
        return _empty_route_stats()
    total_time = weld_time(seq[0])
    total_weld = weld_time(seq[0])
    total_travel = 0.0
    total_dist = 0.0
    pure_weld = weld_process_time(seq[0])
    setup_post = setup_time_value() + post_time_value()
    prev_end = seq[0].end_point()
    for i in range(1, len(seq)):
        t_travel = travel_time(prev_end, seq[i].start_point())
        d_travel = travel_distance(prev_end, seq[i].start_point())
        w_time = weld_time(seq[i])
        total_travel += t_travel
        total_dist += d_travel
        total_time += t_travel + w_time
        total_weld += w_time
        pure_weld += weld_process_time(seq[i])
        setup_post += setup_time_value() + post_time_value()
        prev_end = seq[i].end_point()
    return {
        'total_time': total_time,
        'total_weld_time': total_weld,
        'total_travel_time': total_travel,
        'total_idle_distance': total_dist,
        'setup_post_saved_time': 0.0,
        'merged_setup_post_count': 0,
        'pure_weld_time': pure_weld,
        'setup_post_time': setup_post,
    }

def compute_route_stats_with_timing_mode(seq: List[Weld], split_timing_mode=None) -> Dict[str, float]:
    mode = SPLIT_TIMING_MODE if split_timing_mode is None else split_timing_mode
    if mode == "conservative":
        return _compute_route_stats_conservative(seq)
    if mode != "parent_aware":
        raise ValueError(f"Unknown split_timing_mode: {mode}")
    if not seq:
        return _empty_route_stats()

    setup_post_unit = setup_time_value() + post_time_value()
    first_weld_time = weld_time(seq[0])
    total_time = first_weld_time
    total_weld = first_weld_time
    total_travel = 0.0
    total_dist = 0.0
    pure_weld = weld_process_time(seq[0])
    setup_post = setup_post_unit
    setup_post_saved = 0.0
    merged_count = 0
    prev = seq[0]
    prev_end = prev.end_point()

    for curr in seq[1:]:
        t_travel = travel_time(prev_end, curr.start_point())
        d_travel = travel_distance(prev_end, curr.start_point())
        pure_time = weld_process_time(curr)
        if can_merge_split_setup_post(prev, curr):
            curr_weld_time = pure_time
            setup_post_saved += setup_post_unit
            merged_count += 1
        else:
            curr_weld_time = setup_post_unit + pure_time
            setup_post += setup_post_unit
        total_travel += t_travel
        total_dist += d_travel
        total_time += t_travel + curr_weld_time
        total_weld += curr_weld_time
        pure_weld += pure_time
        prev = curr
        prev_end = curr.end_point()

    return {
        'total_time': total_time,
        'total_weld_time': total_weld,
        'total_travel_time': total_travel,
        'total_idle_distance': total_dist,
        'setup_post_saved_time': setup_post_saved,
        'merged_setup_post_count': merged_count,
        'pure_weld_time': pure_weld,
        'setup_post_time': setup_post,
    }

def compute_route_stats(seq: List[Weld]) -> Dict[str, float]:
    return compute_route_stats_with_timing_mode(seq, SPLIT_TIMING_MODE)

def compute_directed_route_stats(directed_seq, split_timing_mode=None) -> Dict[str, float]:
    mode = SPLIT_TIMING_MODE if split_timing_mode is None else split_timing_mode
    if mode not in ("conservative", "parent_aware"):
        raise ValueError(f"Unknown split_timing_mode: {mode}")
    if not directed_seq:
        stats = _empty_route_stats()
        stats["reversed_weld_count"] = 0
        stats["direction_mode"] = "bidirectional"
        return stats

    setup_post_unit = setup_time_value() + post_time_value()
    first_weld_time = weld_time(directed_seq[0])
    total_time = first_weld_time
    total_weld = first_weld_time
    total_travel = 0.0
    total_dist = 0.0
    pure_weld = weld_process_time(directed_seq[0])
    setup_post = setup_post_unit
    setup_post_saved = 0.0
    merged_count = 0
    prev = directed_seq[0]

    for curr in directed_seq[1:]:
        t_travel = travel_time(prev.end_point(), curr.start_point())
        d_travel = travel_distance(prev.end_point(), curr.start_point())
        pure_time = weld_process_time(curr)
        if mode == "parent_aware" and can_merge_split_setup_post_directed(prev, curr):
            curr_weld_time = pure_time
            setup_post_saved += setup_post_unit
            merged_count += 1
        else:
            curr_weld_time = setup_post_unit + pure_time
            setup_post += setup_post_unit
        total_travel += t_travel
        total_dist += d_travel
        total_time += t_travel + curr_weld_time
        total_weld += curr_weld_time
        pure_weld += pure_time
        prev = curr

    return {
        'total_time': total_time,
        'total_weld_time': total_weld,
        'total_travel_time': total_travel,
        'total_idle_distance': total_dist,
        'setup_post_saved_time': setup_post_saved,
        'merged_setup_post_count': merged_count,
        'pure_weld_time': pure_weld,
        'setup_post_time': setup_post,
        'reversed_weld_count': sum(1 for task in directed_seq if task.reversed),
        'direction_mode': 'bidirectional',
        'direction_flags': [bool(task.reversed) for task in directed_seq],
    }

def optimize_directions_for_order(welds, order, split_timing_mode=None):
    if not order:
        return [], 0.0

    mode = SPLIT_TIMING_MODE if split_timing_mode is None else split_timing_mode
    if mode not in ("conservative", "parent_aware"):
        raise ValueError(f"Unknown split_timing_mode: {mode}")

    setup_post_unit = setup_time_value() + post_time_value()
    n = len(order)
    dp = [[float("inf"), float("inf")] for _ in range(n)]
    parent = [[None, None] for _ in range(n)]

    first_cost = weld_time(welds[order[0]])
    dp[0][0] = first_cost
    dp[0][1] = first_cost

    for k in range(1, n):
        curr_weld = welds[order[k]]
        for curr_d in (0, 1):
            curr_task = DirectedWeld(curr_weld, reversed=bool(curr_d))
            pure_time = weld_process_time(curr_task)
            for prev_d in (0, 1):
                prev_task = DirectedWeld(welds[order[k - 1]], reversed=bool(prev_d))
                t_travel = travel_time(prev_task.end_point(), curr_task.start_point())
                if mode == "parent_aware" and can_merge_split_setup_post_directed(prev_task, curr_task):
                    curr_weld_time = pure_time
                else:
                    curr_weld_time = setup_post_unit + pure_time
                candidate = dp[k - 1][prev_d] + t_travel + curr_weld_time
                if candidate < dp[k][curr_d]:
                    dp[k][curr_d] = candidate
                    parent[k][curr_d] = prev_d

    last_d = 0 if dp[-1][0] <= dp[-1][1] else 1
    directions = [False] * n
    d = last_d
    for k in range(n - 1, -1, -1):
        directions[k] = bool(d)
        d = parent[k][d] if k > 0 else 0

    directed_seq = [
        DirectedWeld(welds[idx], reversed=directions[pos])
        for pos, idx in enumerate(order)
    ]
    return directed_seq, dp[-1][last_d]

# ---------- 蚁群算法（内层优化）----------
class AntColony:
    def __init__(self, welds: List[Weld], ant_count=ACO_ANT_COUNT, max_iter=ACO_MAX_ITER,
                 alpha=ACO_ALPHA, beta=ACO_BETA, rho=ACO_RHO, q=ACO_Q):
        self.welds = welds
        self.n = len(welds)
        if self.n <= 1:
            return
        self.ant_count = min(ant_count, self.n)
        self.max_iter = max_iter
        self.alpha = alpha
        self.beta = beta
        self.rho = rho
        self.q = q

        # 构建距离矩阵（转移时间）
        self.dist = np.zeros((self.n, self.n))
        for i in range(self.n):
            for j in range(self.n):
                if i != j:
                    self.dist[i][j] = travel_time(welds[i].end_point(), welds[j].start_point())
                else:
                    self.dist[i][j] = 0.0
        # 启发式信息矩阵
        self.eta = np.zeros((self.n, self.n))
        for i in range(self.n):
            for j in range(self.n):
                if i != j:
                    self.eta[i][j] = 1.0 / (self.dist[i][j] + 1e-10)
                else:
                    self.eta[i][j] = 0.0
        # 信息素矩阵
        self.pheromone = np.ones((self.n, self.n)) * 0.1

        # 焊接时间列表
        self.weld_times = [weld_time(w) for w in welds]

    def _path_cost(self, path: List[int]) -> float:
        """计算完整路径的总时间（焊接时间 + 转移时间）"""
        if DIRECTION_MODE == "bidirectional":
            _, best_cost = optimize_directions_for_order(self.welds, path, SPLIT_TIMING_MODE)
            return best_cost
        if SPLIT_TIMING_MODE == "parent_aware":
            # Phase 4B 仅改变路径成本计算，使 ACO 评价与 split-aware route timing 一致；
            # 蚂蚁构造路径、信息素蒸发和更新机制保持不变。
            seq = [self.welds[i] for i in path]
            return compute_route_stats_with_timing_mode(seq, "parent_aware")["total_time"]
        total = self.weld_times[path[0]]
        for k in range(1, len(path)):
            total += self.dist[path[k-1]][path[k]] + self.weld_times[path[k]]
        return total

    def _run_ant(self) -> Tuple[List[int], float]:
        """单只蚂蚁构造路径，返回路径（节点索引）和总成本"""
        visited = [False] * self.n
        start = random.randint(0, self.n - 1)
        path = [start]
        visited[start] = True

        for _ in range(1, self.n):
            current = path[-1]
            # 计算未访问节点的选择概率
            probs = []
            for nxt in range(self.n):
                if not visited[nxt]:
                    tau = self.pheromone[current][nxt] ** self.alpha
                    eta = self.eta[current][nxt] ** self.beta
                    probs.append(tau * eta)
                else:
                    probs.append(0.0)
            total_prob = sum(probs)
            if total_prob == 0:
                # 防止除零，随机选择一个未访问节点
                available = [i for i in range(self.n) if not visited[i]]
                nxt = random.choice(available)
            else:
                probs = [p / total_prob for p in probs]
                nxt = np.random.choice(range(self.n), p=probs)
            path.append(nxt)
            visited[nxt] = True
        cost = self._path_cost(path)
        return path, cost

    def solve(self) -> Dict[str, float]:
        """运行ACO，返回最优路径的统计字典"""
        return self.solve_with_order()["stats"]

    def solve_with_order(self) -> Dict[str, object]:
        """Run ACO and return both route stats and the selected route order."""
        if self.n <= 1:
            order = list(range(self.n))
            if DIRECTION_MODE == "bidirectional":
                directed_seq, _ = optimize_directions_for_order(self.welds, order, SPLIT_TIMING_MODE)
                stats = compute_directed_route_stats(directed_seq, SPLIT_TIMING_MODE)
            else:
                stats = compute_route_stats(self.welds)
            stats["route_order"] = order
            stats["route_task_ids"] = [str(self.welds[index].id) for index in order]
            return {"stats": stats, "order": order}

        best_path = None
        best_cost = float('inf')

        for _ in range(self.max_iter):
            all_paths = []
            all_costs = []
            for _ in range(self.ant_count):
                path, cost = self._run_ant()
                all_paths.append(path)
                all_costs.append(cost)
                if cost < best_cost:
                    best_cost = cost
                    best_path = path

            self.pheromone *= (1 - self.rho)

            for path, cost in zip(all_paths, all_costs):
                delta = self.q / cost
                for i in range(self.n - 1):
                    a, b = path[i], path[i + 1]
                    self.pheromone[a][b] += delta

        if DIRECTION_MODE == "bidirectional":
            directed_seq, _ = optimize_directions_for_order(self.welds, best_path, SPLIT_TIMING_MODE)
            stats = compute_directed_route_stats(directed_seq, SPLIT_TIMING_MODE)
        else:
            best_seq = [self.welds[i] for i in best_path]
            stats = compute_route_stats(best_seq)
            stats["direction_flags"] = [False] * len(best_path)
        stats["route_order"] = list(best_path)
        stats["route_task_ids"] = [str(self.welds[index].id) for index in best_path]
        return {"stats": stats, "order": list(best_path)}

def solve_routing_aco(welds: List[Weld]) -> Dict[str, float]:
    """调用蚁群算法求解焊接顺序，返回统计字典"""
    if not welds:
        if DIRECTION_MODE == "bidirectional":
            return compute_directed_route_stats([], SPLIT_TIMING_MODE)
        return compute_route_stats([])
    # 动态调整ACO迭代次数：焊缝数量少时减少迭代
    n = len(welds)
    if n <= 5:
        max_iter = 10
        ant_count = 5
    elif n <= 15:
        max_iter = 20
        ant_count = 10
    else:
        max_iter = 30
        ant_count = 15
    aco = AntColony(welds, ant_count=ant_count, max_iter=max_iter)
    return aco.solve()

# ---------- 分区与任务分配 ----------
def solve_routing_aco_with_order(welds: List[Weld]) -> Dict[str, object]:
    """ACO route solver variant for Phase 9B post-processing audits."""
    if not welds:
        if DIRECTION_MODE == "bidirectional":
            stats = compute_directed_route_stats([], SPLIT_TIMING_MODE)
        else:
            stats = compute_route_stats([])
        return {"stats": stats, "order": []}
    n = len(welds)
    if n <= 5:
        max_iter = 10
        ant_count = 5
    elif n <= 15:
        max_iter = 20
        ant_count = 10
    else:
        max_iter = 30
        ant_count = 15
    aco = AntColony(welds, ant_count=ant_count, max_iter=max_iter)
    return aco.solve_with_order()

def local_partition_y(world_y: float) -> float:
    return world_y - PLATFORM_H_M / 2.0

def partition_world_y(y_h: float = FIXED_Y_H) -> float:
    return y_h + PLATFORM_H_M / 2.0

def classify_point_region(point, x_up, x_low, y_h=FIXED_Y_H, eps=1e-9):
    x, y, _ = point
    y_boundary = partition_world_y(y_h)
    if y >= y_boundary - eps:
        return 0 if x <= x_up + eps else 1
    return 2 if x <= x_low + eps else 3

def _point_compatible_with_region(point, region, x_up, x_low, y_h=FIXED_Y_H, eps=1e-9):
    x, y, _ = point
    y_boundary = partition_world_y(y_h)
    if region == 0:
        return y >= y_boundary - eps and x <= x_up + eps
    if region == 1:
        return y >= y_boundary - eps and x >= x_up - eps
    if region == 2:
        return y <= y_boundary + eps and x <= x_low + eps
    if region == 3:
        return y <= y_boundary + eps and x >= x_low - eps
    return False

def classify_weld_region(weld, x_up, x_low, y_h=FIXED_Y_H, eps=1e-9):
    start = weld.start_point()
    end = weld.end_point()
    start_region = classify_point_region(start, x_up, x_low, y_h, eps)
    end_region = classify_point_region(end, x_up, x_low, y_h, eps)
    if start_region == end_region:
        return start_region

    # A subsegment may start or end exactly on a boundary. In that case the
    # midpoint tells which side owns the nonzero-length segment, while the
    # boundary endpoint remains compatible with both adjacent regions.
    mid_region = classify_point_region(weld.midpoint(), x_up, x_low, y_h, eps)
    if (
        _point_compatible_with_region(start, mid_region, x_up, x_low, y_h, eps)
        and _point_compatible_with_region(end, mid_region, x_up, x_low, y_h, eps)
    ):
        return mid_region
    return None

def _point_on_weld_at_t(weld, t):
    x1, y1, z1 = weld.start_point()
    x2, y2, z2 = weld.end_point()
    return (
        x1 + (x2 - x1) * t,
        y1 + (y2 - y1) * t,
        z1 + (z2 - z1) * t,
    )

def _add_split_t(t_values, t, eps):
    if eps < t < 1.0 - eps:
        t_values.append(t)

def _dedupe_sorted_t_values(t_values, eps):
    result = []
    for t in sorted(t_values):
        t = min(1.0, max(0.0, t))
        if not result or abs(t - result[-1]) > eps:
            result.append(t)
    return result

def split_weld_by_partition_boundaries(
    weld,
    x_up,
    x_low,
    y_h=FIXED_Y_H,
    eps=1e-9,
    min_subweld_length=0.0,
):
    """
    按当前分区边界切分单条焊缝，但不接入 GA/ACO 主流程。

    切分后的子焊缝保留 parent_id 和 segment_id。后续接入主流程时，
    Phase 10B 后 setup/post-processing 时间为 0，parent_id 仅保留同源子段追踪信息。
    """
    y_boundary = partition_world_y(y_h)
    x1, y1, _ = weld.start_point()
    x2, y2, _ = weld.end_point()
    dx = x2 - x1
    dy = y2 - y1
    t_values = [0.0, 1.0]
    boundary_overlap_count = 0
    discarded_short_subweld_count = 0

    if abs(dy) <= eps:
        if abs(y1 - y_boundary) <= eps and abs(y2 - y_boundary) <= eps:
            boundary_overlap_count += 1
    else:
        if (y1 - y_boundary) * (y2 - y_boundary) < -(eps * eps):
            _add_split_t(t_values, (y_boundary - y1) / dy, eps)

    if abs(dx) <= eps:
        if abs(x1 - x_up) <= eps and abs(x2 - x_up) <= eps and max(y1, y2) >= y_boundary - eps:
            boundary_overlap_count += 1
        if abs(x1 - x_low) <= eps and abs(x2 - x_low) <= eps and min(y1, y2) <= y_boundary + eps:
            boundary_overlap_count += 1
    else:
        if (x1 - x_up) * (x2 - x_up) < -(eps * eps):
            t_x_up = (x_up - x1) / dx
            _, y_at, _ = _point_on_weld_at_t(weld, t_x_up)
            if y_at >= y_boundary - eps:
                _add_split_t(t_values, t_x_up, eps)
        if (x1 - x_low) * (x2 - x_low) < -(eps * eps):
            t_x_low = (x_low - x1) / dx
            _, y_at, _ = _point_on_weld_at_t(weld, t_x_low)
            if y_at < y_boundary + eps:
                _add_split_t(t_values, t_x_low, eps)

    t_values = _dedupe_sorted_t_values(t_values, eps)
    if len(t_values) <= 2 and classify_weld_region(weld, x_up, x_low, y_h, eps) is not None:
        stats = {
            "original_weld_id": weld.id,
            "was_split": False,
            "subweld_count": 1,
            "cross_region": False,
            "boundary_overlap_count": boundary_overlap_count,
            "discarded_short_subweld_count": 0,
            "length_error": 0.0,
        }
        return [weld], stats

    subwelds = []
    for t0, t1 in zip(t_values[:-1], t_values[1:]):
        p_start = _point_on_weld_at_t(weld, t0)
        p_end = _point_on_weld_at_t(weld, t1)
        # Equation (26) requires complete length conservation.  Only an exact
        # zero-length numerical duplicate may be removed; every positive
        # fragment remains a task, however short it is.
        if math.dist(p_start, p_end) <= max(0.0, min_subweld_length):
            discarded_short_subweld_count += 1
            continue
        subwelds.append(make_subweld(weld, len(subwelds), p_start, p_end))

    length_error = abs(sum(sub.length for sub in subwelds) - weld.length)
    was_split = len(subwelds) > 1
    stats = {
        "original_weld_id": weld.id,
        "was_split": was_split,
        "subweld_count": len(subwelds),
        "cross_region": classify_weld_region(weld, x_up, x_low, y_h, eps) is None or was_split,
        "boundary_overlap_count": boundary_overlap_count,
        "discarded_short_subweld_count": discarded_short_subweld_count,
        "length_error": length_error,
    }
    return subwelds, stats

def split_welds_for_partition(
    welds,
    x_up,
    x_low,
    y_h=FIXED_Y_H,
    eps=1e-9,
    min_subweld_length=0.0,
):
    all_subwelds = []
    split_weld_count = 0
    cross_region_weld_count = 0
    boundary_overlap_count = 0
    discarded_short_subweld_count = 0
    length_errors = []

    for weld in welds:
        subwelds, stats = split_weld_by_partition_boundaries(
            weld, x_up, x_low, y_h=y_h, eps=eps, min_subweld_length=min_subweld_length
        )
        all_subwelds.extend(subwelds)
        if stats["was_split"]:
            split_weld_count += 1
        if stats["cross_region"]:
            cross_region_weld_count += 1
        boundary_overlap_count += stats["boundary_overlap_count"]
        discarded_short_subweld_count += stats["discarded_short_subweld_count"]
        length_errors.append(stats["length_error"])

    summary_stats = {
        "original_weld_count": len(welds),
        "subweld_count": len(all_subwelds),
        "split_weld_count": split_weld_count,
        "cross_region_weld_count": cross_region_weld_count,
        "boundary_overlap_count": boundary_overlap_count,
        "discarded_short_subweld_count": discarded_short_subweld_count,
        "max_length_error": max(length_errors) if length_errors else 0.0,
        "sum_length_error": sum(length_errors),
    }
    return all_subwelds, summary_stats

def _assert_split_result(weld, subwelds, x_up, x_low, expected_count=None, min_count=None, eps=1e-6):
    if expected_count is not None:
        assert len(subwelds) == expected_count
    if min_count is not None:
        assert len(subwelds) >= min_count
    assert abs(sum(sub.length for sub in subwelds) - weld.length) <= eps
    for sub in subwelds:
        assert sub.length > 0.0
        assert classify_weld_region(sub, x_up, x_low, eps=eps) is not None
        assert getattr(sub, 'parent_id', sub.id) == weld.parent_id

def _self_check_partition_splitting():
    x_up = 10.0
    x_low = 8.0
    yb = partition_world_y(FIXED_Y_H)
    z = 0.1

    w0 = Weld("r0", 1.0, yb + 1.0, z, 3.0, yb + 1.0, z)
    subs, stats = split_weld_by_partition_boundaries(w0, x_up, x_low)
    assert stats["was_split"] is False
    _assert_split_result(w0, subs, x_up, x_low, expected_count=1)
    assert subs[0] is w0

    w_up = Weld("up_cross", x_up - 1.0, yb + 1.0, z, x_up + 1.0, yb + 1.0, z)
    subs, stats = split_weld_by_partition_boundaries(w_up, x_up, x_low)
    assert stats["was_split"] is True
    _assert_split_result(w_up, subs, x_up, x_low, expected_count=2)
    assert all(sub.is_split_segment for sub in subs)

    w_low = Weld("low_cross", x_low - 1.0, yb - 1.0, z, x_low + 1.0, yb - 1.0, z)
    subs, stats = split_weld_by_partition_boundaries(w_low, x_up, x_low)
    assert stats["was_split"] is True
    _assert_split_result(w_low, subs, x_up, x_low, expected_count=2)

    w_y = Weld("y_cross", 2.0, yb + 1.0, z, 2.0, yb - 1.0, z)
    subs, stats = split_weld_by_partition_boundaries(w_y, x_up, x_low)
    assert stats["was_split"] is True
    _assert_split_result(w_y, subs, x_up, x_low, min_count=2)

    w_diag = Weld("diag_cross", x_up + 1.0, yb + 1.0, z, x_up - 1.0, yb - 1.0, z)
    subs, stats = split_weld_by_partition_boundaries(w_diag, x_up, x_low)
    assert stats["was_split"] is True
    assert 2 <= len(subs) <= 3
    _assert_split_result(w_diag, subs, x_up, x_low)

    w_endpoint = Weld("endpoint_boundary", x_up, yb + 1.0, z, x_up + 1.0, yb + 1.0, z)
    subs, stats = split_weld_by_partition_boundaries(w_endpoint, x_up, x_low)
    assert stats["discarded_short_subweld_count"] == 0
    _assert_split_result(w_endpoint, subs, x_up, x_low, expected_count=1)

    w_overlap = Weld("boundary_overlap", x_up, yb + 0.5, z, x_up, yb + 1.5, z)
    subs, stats = split_weld_by_partition_boundaries(w_overlap, x_up, x_low)
    assert stats["boundary_overlap_count"] >= 1
    _assert_split_result(w_overlap, subs, x_up, x_low, expected_count=1)

def assign_welds_to_robots(welds: List[Weld], x_up: float, x_low: float, y_h: float = FIXED_Y_H) -> List[List[Weld]]:
    robots = [[] for _ in range(4)]
    for w in welds:
        mx, my, _ = w.midpoint()
        if local_partition_y(my) >= y_h:
            if mx <= x_up:
                robots[0].append(w)
            else:
                robots[1].append(w)
        else:
            if mx <= x_low:
                robots[2].append(w)
            else:
                robots[3].append(w)
    return robots

def assign_welds_to_robots_split(
    welds,
    x_up,
    x_low,
    y_h=FIXED_Y_H,
    eps=1e-9,
    min_subweld_length=0.0,
):
    subwelds, split_stats = split_welds_for_partition(
        welds, x_up, x_low, y_h=y_h, eps=eps, min_subweld_length=min_subweld_length
    )
    robots = [[] for _ in range(4)]
    unassigned_subweld_ids = []
    for subweld in subwelds:
        region = classify_weld_region(subweld, x_up, x_low, y_h, eps)
        if region in (0, 1, 2, 3):
            robots[region].append(subweld)
        else:
            unassigned_subweld_ids.append(subweld.id)

    assigned_subweld_count = sum(len(robot_welds) for robot_welds in robots)
    parent_ids = [getattr(w, "parent_id", w.id) for w in subwelds]
    split_segment_count = sum(1 for w in subwelds if getattr(w, "is_split_segment", False))
    assignment_stats = {
        **split_stats,
        "assignment_mode": "split",
        "assigned_subweld_count": assigned_subweld_count,
        "unassigned_subweld_count": len(unassigned_subweld_ids),
        "unassigned_subweld_ids": str(unassigned_subweld_ids),
        "robot_0_task_count": len(robots[0]),
        "robot_1_task_count": len(robots[1]),
        "robot_2_task_count": len(robots[2]),
        "robot_3_task_count": len(robots[3]),
        "parent_id_count": len(set(parent_ids)),
        "split_segment_count": split_segment_count,
    }
    return robots, assignment_stats

def get_assignment_for_partition(welds, x_up, x_low, assignment_mode=None):
    mode = ASSIGNMENT_MODE if assignment_mode is None else assignment_mode
    if mode == "midpoint":
        robots = assign_welds_to_robots(welds, x_up, x_low)
        assigned_count = sum(len(robot_welds) for robot_welds in robots)
        assignment_stats = {
            "assignment_mode": "midpoint",
            "original_weld_count": len(welds),
            "subweld_count": len(welds),
            "split_weld_count": 0,
            "cross_region_weld_count": 0,
            "boundary_overlap_count": 0,
            "discarded_short_subweld_count": 0,
            "max_length_error": 0.0,
            "sum_length_error": 0.0,
            "assigned_subweld_count": assigned_count,
            "unassigned_subweld_count": 0,
            "unassigned_subweld_ids": "[]",
            "robot_0_task_count": len(robots[0]),
            "robot_1_task_count": len(robots[1]),
            "robot_2_task_count": len(robots[2]),
            "robot_3_task_count": len(robots[3]),
            "parent_id_count": len({getattr(w, "parent_id", w.id) for w in welds}),
            "split_segment_count": sum(1 for w in welds if getattr(w, "is_split_segment", False)),
        }
        return robots, assignment_stats
    if mode == "split":
        return assign_welds_to_robots_split(welds, x_up, x_low)
    raise ValueError(f"Unknown assignment_mode: {mode}")

def _self_check_split_assignment():
    x_up = 10.0
    x_low = 8.0
    yb = partition_world_y(FIXED_Y_H)
    z = 0.1
    welds = [
        Weld("r0", 1.0, yb + 1.0, z, 3.0, yb + 1.0, z),
        Weld("up_cross", x_up - 1.0, yb + 1.0, z, x_up + 1.0, yb + 1.0, z),
        Weld("low_cross", x_low - 1.0, yb - 1.0, z, x_low + 1.0, yb - 1.0, z),
        Weld("y_cross", 2.0, yb + 1.0, z, 2.0, yb - 1.0, z),
    ]
    robots, stats = assign_welds_to_robots_split(welds, x_up, x_low)
    robot_counts = [len(r) for r in robots]
    assert stats["unassigned_subweld_count"] == 0
    assert sum(robot_counts) == stats["assigned_subweld_count"]
    assert stats["subweld_count"] >= stats["original_weld_count"]
    assert stats["split_weld_count"] > 0
    for region, robot_welds in enumerate(robots):
        for subweld in robot_welds:
            assert classify_weld_region(subweld, x_up, x_low) == region

def _self_check_split_aware_timing():
    z = 0.1
    parent = Weld("p0", 0.0, 0.0, z, 2.0, 0.0, z)
    sub0 = make_subweld(parent, 0, (0.0, 0.0, z), (1.0, 0.0, z))
    sub1 = make_subweld(parent, 1, (1.0, 0.0, z), (2.0, 0.0, z))
    conservative = compute_route_stats_with_timing_mode([sub0, sub1], "conservative")
    parent_aware = compute_route_stats_with_timing_mode([sub0, sub1], "parent_aware")
    assert abs(conservative["total_time"] - parent_aware["total_time"]) <= 1e-6
    assert abs(conservative["total_weld_time"] - parent_aware["total_weld_time"]) <= 1e-6
    assert parent_aware["merged_setup_post_count"] == 1
    assert parent_aware["setup_post_saved_time"] == 0.0
    assert parent_aware["setup_post_time"] == 0.0
    assert abs(parent_aware["pure_weld_time"] - parent_aware["total_weld_time"]) <= 1e-6

    other = Weld("p1", 1.0, 0.0, z, 2.0, 0.0, z)
    no_merge = compute_route_stats_with_timing_mode([sub0, other], "parent_aware")
    assert no_merge["merged_setup_post_count"] == 0
    assert no_merge["setup_post_saved_time"] == 0.0
    assert no_merge["setup_post_time"] == 0.0

    distant = make_subweld(parent, 2, (10.0, 0.0, z), (11.0, 0.0, z))
    distant_stats = compute_route_stats_with_timing_mode([sub0, distant], "parent_aware")
    assert distant_stats["merged_setup_post_count"] == 0
    assert distant_stats["setup_post_saved_time"] == 0.0
    assert distant_stats["setup_post_time"] == 0.0

def _self_check_direction_optimization():
    z = 0.1

    a = Weld("a", 0.0, 0.0, z, 10.0, 0.0, z)
    b = Weld("b", 11.0, 0.0, z, 20.0, 0.0, z)
    directed_seq, best_cost = optimize_directions_for_order([a, b], [0, 1], "conservative")
    assert directed_seq[0].reversed is False
    assert directed_seq[1].reversed is False
    assert abs(best_cost - compute_directed_route_stats(directed_seq, "conservative")["total_time"]) <= 1e-6

    b_reverse_better = Weld("b_reverse_better", 20.0, 0.0, z, 11.0, 0.0, z)
    directed_seq, _ = optimize_directions_for_order([a, b_reverse_better], [0, 1], "conservative")
    assert directed_seq[1].reversed is True

    parent = Weld("parent", 0.0, 0.0, z, 2.0, 0.0, z)
    sub0 = make_subweld(parent, 0, (0.0, 0.0, z), (1.0, 0.0, z))
    sub1 = make_subweld(parent, 1, (2.0, 0.0, z), (1.0, 0.0, z))
    directed_seq, _ = optimize_directions_for_order([sub0, sub1], [0, 1], "parent_aware")
    stats = compute_directed_route_stats(directed_seq, "parent_aware")
    assert directed_seq[1].reversed is True
    assert directed_seq[0].end_point() == directed_seq[1].start_point()
    assert stats["merged_setup_post_count"] == 1
    assert stats["setup_post_saved_time"] == 0.0
    assert stats["setup_post_time"] == 0.0
    assert abs(stats["pure_weld_time"] - stats["total_weld_time"]) <= 1e-6

    fixed_stats = compute_route_stats_with_timing_mode([sub0, sub1], "parent_aware")
    assert fixed_stats == compute_route_stats_with_timing_mode([sub0, sub1], "parent_aware")

def _self_check_no_setup_post_time_model():
    z = 0.1
    w = Weld("time_model_10m", 0.0, 0.0, z, 10.0, 0.0, z)
    weld_speed = get_active_time_params()["weld_speed"]
    expected_time = 10.0 / weld_speed
    assert abs(weld_time(w) - expected_time) < 1e-9
    assert abs(weld_process_time(w) - expected_time) < 1e-9
    assert setup_time_value() == 0.0
    assert post_time_value() == 0.0

    w2 = Weld("time_model_2m", 10.0, 0.0, z, 12.0, 0.0, z)
    stats = compute_route_stats_with_timing_mode([w, w2], "parent_aware")
    expected_weld_time = (w.length + w2.length) / weld_speed
    assert abs(stats["total_weld_time"] - expected_weld_time) < 1e-9
    assert abs(stats["pure_weld_time"] - expected_weld_time) < 1e-9
    assert stats["setup_post_time"] == 0.0
    assert stats["setup_post_saved_time"] == 0.0

def assignment_is_feasible(assignment_stats: Dict, length_tolerance: float = 1e-8) -> bool:
    """Check equations (11), (12), and (26) for a split assignment."""
    if assignment_stats.get("assignment_mode") != "split":
        return True
    assigned = int(assignment_stats.get("assigned_subweld_count", 0))
    subwelds = int(assignment_stats.get("subweld_count", 0))
    unassigned = int(assignment_stats.get("unassigned_subweld_count", 0))
    length_error = float(assignment_stats.get("sum_length_error", 0.0))
    return assigned == subwelds and unassigned == 0 and length_error <= length_tolerance


def evaluate_partition(welds: List[Weld], x_up: float, x_low: float) -> Tuple[float, float, float, List[Dict], Dict]:
    """
    评估分区方案，返回：
        makespan      : 最大完成时间（秒）
        load_imb      : 负载不平衡度（秒）
        total_distance: 所有机器人空载移动距离之和（米）
        robots_stats  : 每个机器人的详细统计字典列表
        assignment_stats: 当前分配模式和切分统计
    """
    robots, assignment_stats = get_assignment_for_partition(welds, x_up, x_low)
    if not assignment_is_feasible(assignment_stats):
        return float("inf"), float("inf"), float("inf"), [_empty_route_stats() for _ in range(4)], assignment_stats
    robots_stats = []
    for rob_welds in robots:
        stats = solve_routing_aco(rob_welds)   # 替换为蚁群算法
        robots_stats.append(stats)
    times = [stats['total_time'] for stats in robots_stats]
    makespan = max(times)
    load_imb = max(times) - min(times)
    total_distance = sum(stats['total_idle_distance'] for stats in robots_stats)
    return makespan, load_imb, total_distance, robots_stats, assignment_stats

# ---------- 遗传算法组件 ----------
def evaluate_partition_with_orders(welds: List[Weld], x_up: float, x_low: float) -> Tuple[float, float, float, List[Dict], Dict, List[List[int]]]:
    """Evaluate a partition and keep route orders for Phase 9B audit export."""
    robots, assignment_stats = get_assignment_for_partition(welds, x_up, x_low)
    if not assignment_is_feasible(assignment_stats):
        return (
            float("inf"),
            float("inf"),
            float("inf"),
            [_empty_route_stats() for _ in range(4)],
            assignment_stats,
            [[] for _ in range(4)],
        )
    robots_stats = []
    robot_orders = []
    for rob_welds in robots:
        result = solve_routing_aco_with_order(rob_welds)
        robots_stats.append(result["stats"])
        robot_orders.append(result["order"])
    times = [stats['total_time'] for stats in robots_stats]
    makespan = max(times) if times else 0.0
    load_imb = max(times) - min(times) if times else 0.0
    total_distance = sum(stats['total_idle_distance'] for stats in robots_stats)
    return makespan, load_imb, total_distance, robots_stats, assignment_stats, robot_orders

class Individual:
    def __init__(self, x_up, x_low):
        self.y_h = FIXED_Y_H
        self.x_up = x_up
        self.x_low = x_low
        self.makespan = 0.0
        self.load_imb = 0.0
        self.total_distance = 0.0
        self.robots_stats = []
        self.assignment_stats = {}
        self.fitness = 0.0

def clamp(value, low, high):
    return max(low, min(high, value))

def init_population(pop_size: int) -> List[Individual]:
    return [Individual(random.uniform(0, PLATFORM_W_M),
                       random.uniform(0, PLATFORM_W_M))
            for _ in range(pop_size)]

def compute_fitness(ind: Individual, ref_makespan: float, ref_load: float, ref_dist: float):
    ind.fitness = weighted_objective(
        ind.makespan,
        ind.load_imb,
        ind.total_distance,
        ref_makespan,
        ref_load,
        ref_dist,
    )

def tournament_selection(pop: List[Individual], k=2) -> Individual:
    best = random.choice(pop)
    for _ in range(k-1):
        other = random.choice(pop)
        if other.fitness < best.fitness:
            best = other
    return best

def sbx_crossover(p1: Individual, p2: Individual, eta=ETA_C) -> Tuple[Individual, Individual]:
    u = random.random()
    if u <= 0.5:
        beta = (2*u) ** (1/(eta+1))
    else:
        beta = (1/(2*(1-u))) ** (1/(eta+1))
    c1 = Individual(0,0)
    c2 = Individual(0,0)
    c1.x_up = 0.5 * ((1+beta)*p1.x_up + (1-beta)*p2.x_up)
    c2.x_up = 0.5 * ((1-beta)*p1.x_up + (1+beta)*p2.x_up)
    c1.x_low = 0.5 * ((1+beta)*p1.x_low + (1-beta)*p2.x_low)
    c2.x_low = 0.5 * ((1-beta)*p1.x_low + (1+beta)*p2.x_low)
    for ind in [c1,c2]:
        ind.y_h = FIXED_Y_H
        ind.x_up = clamp(ind.x_up, 0, PLATFORM_W_M)
        ind.x_low = clamp(ind.x_low, 0, PLATFORM_W_M)
    return c1, c2

def polynomial_mutation(ind: Individual, pm: float, eta=ETA_M):
    if random.random() > pm:
        return
    for gene in ['x_up', 'x_low']:
        if random.random() < pm:
            u = random.random()
            if u < 0.5:
                delta = (2*u) ** (1/(eta+1)) - 1
            else:
                delta = 1 - (2*(1-u)) ** (1/(eta+1))
            low = 0.0
            high = PLATFORM_W_M
            new_val = getattr(ind, gene) + delta * (high - low)
            setattr(ind, gene, clamp(new_val, low, high))

def adaptive_pc(gen, max_gen):
    return PC_MIN + (PC_MAX - PC_MIN) * (gen / max_gen)

def adaptive_pm(gen, max_gen):
    return PM_MAX - (PM_MAX - PM_MIN) * (gen / max_gen)

def run_ga(
    welds: List[Weld],
    pop_size=POP_SIZE,
    max_gen=MAX_GEN,
    elite_size=ELITE_SIZE,
    objective_refs=None,
    max_objective_evaluations=0,
):
    global REF_MAKESPAN, REF_LOAD, REF_DISTANCE
    if objective_refs is None:
        objective_refs = resolve_objective_references(welds)
    REF_MAKESPAN, REF_LOAD, REF_DISTANCE = objective_refs
    validate_objective_parameters(
        WEIGHT_MAKESPAN,
        WEIGHT_LOAD,
        WEIGHT_DISTANCE,
        REF_MAKESPAN,
        REF_LOAD,
        REF_DISTANCE,
    )
    if max_objective_evaluations and max_objective_evaluations < pop_size:
        raise ValueError("--max-objective-evaluations must be 0 or at least --pop-size")
    objective_evaluation_count = 0
    trace = IncumbentTrace()
    trace_started = time.perf_counter()

    def evaluate_individual(ind):
        nonlocal objective_evaluation_count
        ind.makespan, ind.load_imb, ind.total_distance, ind.robots_stats, ind.assignment_stats = evaluate_partition(
            welds, ind.x_up, ind.x_low)
        compute_fitness(ind, REF_MAKESPAN, REF_LOAD, REF_DISTANCE)
        objective_evaluation_count += 1
        trace.observe(objective_evaluation_count, ind.fitness, ind.makespan,
                      ind.load_imb, ind.total_distance,
                      time.perf_counter() - trace_started)

    pop = init_population(pop_size)
    for ind in pop:
        evaluate_individual(ind)

    best_individual = min(pop, key=lambda x: x.fitness)
    best_fitness_history = [best_individual.fitness]

    actual_generations = 0
    budget_exhausted = False
    for gen in range(max_gen):
        pc = adaptive_pc(gen, max_gen)
        pm = adaptive_pm(gen, max_gen)
        elites = sorted(pop, key=lambda x: x.fitness)[:elite_size]
        new_pop = elites.copy()
        while len(new_pop) < pop_size:
            if max_objective_evaluations and objective_evaluation_count >= max_objective_evaluations:
                budget_exhausted = True
                break
            p1 = tournament_selection(pop)
            p2 = tournament_selection(pop)
            if random.random() < pc:
                c1, c2 = sbx_crossover(p1, p2)
            else:
                c1 = Individual(p1.x_up, p1.x_low)
                c2 = Individual(p2.x_up, p2.x_low)
            polynomial_mutation(c1, pm)
            polynomial_mutation(c2, pm)
            evaluate_individual(c1)
            new_pop.append(c1)
            if len(new_pop) < pop_size and not (
                max_objective_evaluations and objective_evaluation_count >= max_objective_evaluations
            ):
                evaluate_individual(c2)
                new_pop.append(c2)
            elif len(new_pop) < pop_size:
                budget_exhausted = True
                break
        if len(new_pop) < pop_size:
            retained = sorted(pop, key=lambda x: x.fitness)
            new_pop.extend(retained[: pop_size - len(new_pop)])
        pop = new_pop[:pop_size]
        current_best = min(pop, key=lambda x: x.fitness)
        if current_best.fitness < best_individual.fitness:
            best_individual = current_best
        best_fitness_history.append(best_individual.fitness)
        actual_generations = gen + 1
        if gen % 10 == 0:
            print(f"Gen {gen}: best fitness={best_individual.fitness:.4f}, makespan={best_individual.makespan:.2f}s, load_imb={best_individual.load_imb:.2f}s, total_dist={best_individual.total_distance:.2f}m")
        if budget_exhausted:
            break
    budget_exhausted = budget_exhausted or bool(
        max_objective_evaluations and objective_evaluation_count >= max_objective_evaluations
    )
    best_individual.objective_evaluation_count = objective_evaluation_count
    best_individual.objective_budget_exhausted = budget_exhausted
    best_individual.ga_actual_generations = actual_generations
    best_individual.checkpoint_trace = trace.records
    best_individual.stop_reason = "objective_budget" if budget_exhausted else "generation_limit"
    return best_individual, best_fitness_history

# ---------- 可视化（略作修改，增加距离信息标题）----------
def visualize_solution(welds: List[Weld], best: Individual, output_image: str = None):
    robots, _ = get_assignment_for_partition(welds, best.x_up, best.x_low, assignment_mode=ASSIGNMENT_MODE)
    safe_z = get_active_time_params()["safe_z"]
    colors = ['red', 'blue', 'green', 'orange']
    fig = plt.figure(figsize=(12,8))
    ax = fig.add_subplot(111, projection='3d')
    for rob_idx, rob_welds in enumerate(robots):
        color = colors[rob_idx % len(colors)]
        for w in rob_welds:
            ax.plot([w.x1, w.x2], [w.y1, w.y2], [w.z1, w.z2], color=color, linewidth=1.2, marker='o', markersize=3)
            ax.scatter(w.x1, w.y1, w.z1, color=color, s=12, marker='o')
            ax.scatter(w.x2, w.y2, w.z2, color=color, s=8, marker='^')
    z_line = safe_z
    y_world = partition_world_y(FIXED_Y_H)
    ax.plot([0, PLATFORM_W_M], [y_world, y_world], [z_line, z_line], color='black', linewidth=2, linestyle='--')
    ax.plot([best.x_up, best.x_up], [y_world, PLATFORM_H_M], [z_line, z_line], color='black', linewidth=2, linestyle='--')
    ax.plot([best.x_low, best.x_low], [0, y_world], [z_line, z_line], color='black', linewidth=2, linestyle='--')
    for rob_idx, rob_welds in enumerate(robots):
        if len(rob_welds) <= 1:
            continue
        unvisited = set(rob_welds)
        start = random.choice(list(unvisited))
        seq = [start]
        unvisited.remove(start)
        while unvisited:
            last = seq[-1].end_point()
            best_next = min(unvisited, key=lambda w: travel_time(last, w.start_point()))
            seq.append(best_next)
            unvisited.remove(best_next)
        for i in range(len(seq)-1):
            p1 = seq[i].end_point()
            p2 = seq[i+1].start_point()
            ax.plot([p1[0], p2[0]], [p1[1], p2[1]], [safe_z, safe_z],
                    color=colors[rob_idx], linewidth=1, alpha=0.7, linestyle=':')
    ax.set_xlabel('X (m)')
    ax.set_ylabel('Y (m)')
    ax.set_zlabel('Z (m)')
    ax.set_title(f'X-cut Partition ({ASSIGNMENT_MODE}): y_h={FIXED_Y_H:.2f} local, x_up={best.x_up:.2f}, x_low={best.x_low:.2f}\n'
                 f'Makespan={best.makespan:.2f}s, Load imbalance={best.load_imb:.2f}s, Total idle distance={best.total_distance:.2f}m')
    ax.set_xlim(0, PLATFORM_W_M)
    ax.set_ylim(0, PLATFORM_H_M)
    if output_image:
        plt.savefig(output_image, dpi=150)
        print(f"图片保存至 {output_image}")
    else:
        plt.show()

# ---------- baseline 指标收集与输出 ----------
BASELINE_METRIC_FIELDS = [
    "solver_name",
    "instance_path",
    "instance_seed",
    "solver_seed",
    "instance_hash",
    "target_weld_count",
    "requested_group_count",
    "placed_group_count",
    "actual_weld_count",
    "total_original_weld_length",
    "seed",
    "objective_mode",
    "fitness",
    "weight_makespan",
    "weight_load",
    "weight_distance",
    "ref_makespan",
    "ref_load",
    "ref_distance",
    "time_model",
    "weld_speed",
    "travel_speed",
    "acc",
    "acceleration",
    "safe_z",
    "setup_time",
    "post_time",
    "split_timing_mode",
    "direction_mode",
    "route_type",
    "reversed_weld_count_total",
    "direction_optimized_route_count",
    "setup_post_saved_time_total",
    "merged_setup_post_count_total",
    "pure_weld_time_total",
    "setup_post_time_total",
    "weld_count",
    "assignment_mode",
    "original_weld_count",
    "subweld_count",
    "split_weld_count",
    "cross_region_weld_count",
    "boundary_overlap_count",
    "discarded_short_subweld_count",
    "max_length_error",
    "sum_length_error",
    "assigned_subweld_count",
    "unassigned_subweld_count",
    "unassigned_subweld_ids",
    "parent_id_count",
    "split_segment_count",
    "original_to_subweld_count_match",
    "x_up",
    "x_low",
    "makespan",
    "load_imb",
    "total_idle_distance",
    "total_weld_time",
    "total_travel_time",
    "algo_time",
    "objective_evaluation_count",
    "max_objective_evaluations",
    "objective_budget_exhausted",
    "ga_actual_generations",
    "robot_counts",
    "sum_robot_counts",
    "count_match",
    "robot_0_count",
    "robot_1_count",
    "robot_2_count",
    "robot_3_count",
    "robot_0_total_time",
    "robot_1_total_time",
    "robot_2_total_time",
    "robot_3_total_time",
    "robot_0_weld_time",
    "robot_1_weld_time",
    "robot_2_weld_time",
    "robot_3_weld_time",
    "robot_0_travel_time",
    "robot_1_travel_time",
    "robot_2_travel_time",
    "robot_3_travel_time",
    "robot_0_idle_distance",
    "robot_1_idle_distance",
    "robot_2_idle_distance",
    "robot_3_idle_distance",
]

COLLISION_AUDIT_METRIC_FIELDS = [
    "collision_audit_enabled",
    "collision_check_mode",
    "collision_safety_distance",
    "collision_band_width",
    "collision_dt",
    "base_makespan_before_collision",
    "collision_adjusted_makespan",
    "collision_wait_time_total",
    "upper_pair_wait_time",
    "lower_pair_wait_time",
    "upper_pair_violation_count_before",
    "lower_pair_violation_count_before",
    "upper_pair_violation_count_after",
    "lower_pair_violation_count_after",
    "upper_pair_min_separation_before",
    "lower_pair_min_separation_before",
    "upper_pair_min_separation_after",
    "lower_pair_min_separation_after",
]

BASELINE_METRIC_FIELDS.extend(COLLISION_AUDIT_METRIC_FIELDS)
for _robot_id in range(4):
    BASELINE_METRIC_FIELDS.extend([
        f"robot_{_robot_id}_total_weld_time",
        f"robot_{_robot_id}_total_travel_time",
        f"robot_{_robot_id}_total_time",
        f"robot_{_robot_id}_total_idle_distance",
        f"robot_{_robot_id}_task_count",
    ])
BASELINE_METRIC_FIELDS.extend(["sum_robot_total_time", "stop_reason"])

def _collision_metrics_disabled(args=None):
    return {
        "collision_audit_enabled": False,
        "collision_check_mode": "",
        "collision_safety_distance": getattr(args, "collision_safety_distance", ""),
        "collision_band_width": getattr(args, "collision_band_width", ""),
        "collision_dt": getattr(args, "collision_dt", ""),
        "base_makespan_before_collision": "",
        "collision_adjusted_makespan": "",
        "collision_wait_time_total": "",
        "upper_pair_wait_time": "",
        "lower_pair_wait_time": "",
        "upper_pair_violation_count_before": "",
        "lower_pair_violation_count_before": "",
        "upper_pair_violation_count_after": "",
        "lower_pair_violation_count_after": "",
        "upper_pair_min_separation_before": "",
        "lower_pair_min_separation_before": "",
        "upper_pair_min_separation_after": "",
        "lower_pair_min_separation_after": "",
    }

def _collision_metrics_from_audit(audit_stats):
    return {
        "collision_audit_enabled": True,
        "collision_check_mode": audit_stats.get("collision_check_mode", ""),
        "collision_safety_distance": audit_stats.get("safety_distance", ""),
        "collision_band_width": audit_stats.get("band_width", ""),
        "collision_dt": audit_stats.get("dt", ""),
        "base_makespan_before_collision": audit_stats.get("base_makespan", ""),
        "collision_adjusted_makespan": audit_stats.get("collision_adjusted_makespan", ""),
        "collision_wait_time_total": audit_stats.get("collision_wait_time_total", ""),
        "upper_pair_wait_time": audit_stats.get("upper_pair_wait_time", ""),
        "lower_pair_wait_time": audit_stats.get("lower_pair_wait_time", ""),
        "upper_pair_violation_count_before": audit_stats.get("upper_pair_violation_count_before", ""),
        "lower_pair_violation_count_before": audit_stats.get("lower_pair_violation_count_before", ""),
        "upper_pair_violation_count_after": audit_stats.get("upper_pair_violation_count_after", ""),
        "lower_pair_violation_count_after": audit_stats.get("lower_pair_violation_count_after", ""),
        "upper_pair_min_separation_before": audit_stats.get("upper_pair_min_separation_before", ""),
        "lower_pair_min_separation_before": audit_stats.get("lower_pair_min_separation_before", ""),
        "upper_pair_min_separation_after": audit_stats.get("upper_pair_min_separation_after", ""),
        "lower_pair_min_separation_after": audit_stats.get("lower_pair_min_separation_after", ""),
    }

def run_collision_audit_if_enabled(args, robots, robot_orders, x_up, x_low):
    if not getattr(args, "enable_collision_audit", False):
        return None, _collision_metrics_disabled(args)
    import collision_aware_schedule as cas

    directed_sequences = cas.make_directed_sequences_from_robot_orders(
        robots,
        robot_orders,
        split_timing_mode=SPLIT_TIMING_MODE,
    )
    audit_stats = cas.audit_boundary_collisions_for_four_robots(
        directed_sequences,
        x_up=x_up,
        x_low=x_low,
        safety_distance=args.collision_safety_distance,
        band_width=args.collision_band_width,
        dt=args.collision_dt,
    )
    return audit_stats, _collision_metrics_from_audit(audit_stats)

def print_collision_audit_summary(audit_stats):
    if not audit_stats:
        return
    print("Collision audit summary (post-processing only):")
    print(f"  base_makespan_before_collision: {audit_stats['base_makespan']:.6f}")
    print(f"  collision_adjusted_makespan   : {audit_stats['collision_adjusted_makespan']:.6f}")
    print(f"  collision_wait_time_total     : {audit_stats['collision_wait_time_total']:.6f}")
    print(f"  upper violations before/after : {audit_stats['upper_pair_violation_count_before']} / {audit_stats['upper_pair_violation_count_after']}")
    print(f"  lower violations before/after : {audit_stats['lower_pair_violation_count_before']} / {audit_stats['lower_pair_violation_count_after']}")

def collect_baseline_metrics(best, algo_time, robot_counts, seed, weld_count=None, assignment_stats=None):
    params = get_active_time_params()
    assignment_stats = assignment_stats or getattr(best, "assignment_stats", {}) or {}
    assigned_subweld_count = assignment_stats.get("assigned_subweld_count", sum(robot_counts))
    original_weld_count = assignment_stats.get("original_weld_count", weld_count)
    subweld_count = assignment_stats.get("subweld_count", assigned_subweld_count)
    total_weld_time = sum(stats['total_weld_time'] for stats in best.robots_stats)
    total_travel_time = sum(stats['total_travel_time'] for stats in best.robots_stats)
    reversed_weld_count_total = sum(stats.get('reversed_weld_count', 0) for stats in best.robots_stats)
    direction_optimized_route_count = sum(
        1 for stats in best.robots_stats if stats.get('direction_mode') == "bidirectional"
    )
    setup_post_saved_time_total = sum(stats.get('setup_post_saved_time', 0.0) for stats in best.robots_stats)
    merged_setup_post_count_total = sum(stats.get('merged_setup_post_count', 0) for stats in best.robots_stats)
    pure_weld_time_total = sum(stats.get('pure_weld_time', 0.0) for stats in best.robots_stats)
    setup_post_time_total = sum(stats.get('setup_post_time', 0.0) for stats in best.robots_stats)
    sum_robot_counts = sum(robot_counts)
    count_match = sum_robot_counts == assigned_subweld_count
    metrics = {
        "solver_name": SOLVER_NAME,
        "seed": seed,
        "objective_mode": "equation_19_fixed_references",
        "fitness": best.fitness,
        "weight_makespan": WEIGHT_MAKESPAN,
        "weight_load": WEIGHT_LOAD,
        "weight_distance": WEIGHT_DISTANCE,
        "ref_makespan": REF_MAKESPAN,
        "ref_load": REF_LOAD,
        "ref_distance": REF_DISTANCE,
        "time_model": params["time_model"],
        "weld_speed": params["weld_speed"],
        "travel_speed": params["travel_speed"],
        "acc": params["acc"],
        "acceleration": params["acc"],
        "safe_z": params["safe_z"],
        "setup_time": params["setup_time"],
        "post_time": params["post_time"],
        "split_timing_mode": SPLIT_TIMING_MODE,
        "direction_mode": DIRECTION_MODE,
        "route_type": "open",
        "reversed_weld_count_total": reversed_weld_count_total,
        "direction_optimized_route_count": direction_optimized_route_count,
        "setup_post_saved_time_total": setup_post_saved_time_total,
        "merged_setup_post_count_total": merged_setup_post_count_total,
        "pure_weld_time_total": pure_weld_time_total,
        "setup_post_time_total": setup_post_time_total,
        "weld_count": weld_count,
        "assignment_mode": assignment_stats.get("assignment_mode", ASSIGNMENT_MODE),
        "original_weld_count": original_weld_count,
        "subweld_count": subweld_count,
        "split_weld_count": assignment_stats.get("split_weld_count", 0),
        "cross_region_weld_count": assignment_stats.get("cross_region_weld_count", 0),
        "boundary_overlap_count": assignment_stats.get("boundary_overlap_count", 0),
        "discarded_short_subweld_count": assignment_stats.get("discarded_short_subweld_count", 0),
        "max_length_error": assignment_stats.get("max_length_error", 0.0),
        "sum_length_error": assignment_stats.get("sum_length_error", 0.0),
        "assigned_subweld_count": assigned_subweld_count,
        "unassigned_subweld_count": assignment_stats.get("unassigned_subweld_count", 0),
        "unassigned_subweld_ids": assignment_stats.get("unassigned_subweld_ids", "[]"),
        "parent_id_count": assignment_stats.get("parent_id_count", original_weld_count or 0),
        "split_segment_count": assignment_stats.get("split_segment_count", 0),
        "original_to_subweld_count_match": (original_weld_count is None) or (original_weld_count <= subweld_count),
        "x_up": best.x_up,
        "x_low": best.x_low,
        "makespan": best.makespan,
        "load_imb": best.load_imb,
        "total_idle_distance": best.total_distance,
        "total_weld_time": total_weld_time,
        "total_travel_time": total_travel_time,
        "algo_time": algo_time,
        "objective_evaluation_count": getattr(best, "objective_evaluation_count", 0),
        "max_objective_evaluations": getattr(best, "max_objective_evaluations", 0),
        "objective_budget_exhausted": getattr(best, "objective_budget_exhausted", False),
        "ga_actual_generations": getattr(best, "ga_actual_generations", 0),
        "robot_counts": str(robot_counts),
        "sum_robot_counts": sum_robot_counts,
        "count_match": count_match,
    }
    for i in range(4):
        stats = best.robots_stats[i] if i < len(best.robots_stats) else {}
        metrics[f"robot_{i}_count"] = robot_counts[i] if i < len(robot_counts) else 0
        metrics[f"robot_{i}_total_time"] = stats.get('total_time', 0.0)
        metrics[f"robot_{i}_weld_time"] = stats.get('total_weld_time', 0.0)
        metrics[f"robot_{i}_travel_time"] = stats.get('total_travel_time', 0.0)
        metrics[f"robot_{i}_idle_distance"] = stats.get('total_idle_distance', 0.0)
    metrics.update(_collision_metrics_disabled())
    return metrics

def save_baseline_metrics_csv(metrics: Dict, csv_path: str):
    os.makedirs(os.path.dirname(os.path.abspath(csv_path)), exist_ok=True)
    file_exists = os.path.exists(csv_path)
    if file_exists:
        with open(csv_path, mode="r", newline="", encoding="utf-8-sig") as f:
            reader = csv.reader(f)
            existing_header = next(reader, None)
        if existing_header != BASELINE_METRIC_FIELDS:
            print(
                f"警告：CSV 文件 {csv_path} 的表头与当前指标字段不一致，已跳过写入。"
                "请换用新的 CSV 文件名，例如 phase1_corrected_metrics.csv。"
            )
            return False
    with open(csv_path, mode="a", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=BASELINE_METRIC_FIELDS)
        if not file_exists:
            writer.writeheader()
        writer.writerow({field: metrics.get(field, "") for field in BASELINE_METRIC_FIELDS})
    return True

def print_results_table(best: Individual, algo_time: float, metrics=None):
    """
    打印详细结果表格。
    """
    print("\n" + "="*80)
    print("优化结果汇总")
    print("="*80)
    # 每个区域的详细数据
    headers = ["机器人", "焊接时间(s)", "空载时间(s)", "总时间(s)", "空载距离(m)"]
    print(f"{headers[0]:<8} {headers[1]:>12} {headers[2]:>12} {headers[3]:>12} {headers[4]:>12}")
    print("-"*60)
    total_weld = 0.0
    total_travel_time = 0.0
    total_idle_dist = 0.0
    for i, stats in enumerate(best.robots_stats):
        wt = stats['total_weld_time']
        tt = stats['total_travel_time']
        tot = stats['total_time']
        dist = stats['total_idle_distance']
        total_weld += wt
        total_travel_time += tt
        total_idle_dist += dist
        print(f"区域{i:<7} {wt:>12.2f} {tt:>12.2f} {tot:>12.2f} {dist:>12.2f}")
    print("-"*60)
    print(f"{'合计':<8} {total_weld:>12.2f} {total_travel_time:>12.2f} {best.makespan:>12.2f} {total_idle_dist:>12.2f}")
    print("\n其他指标：")
    print(f"  焊接总用时 (makespan)     : {best.makespan:.2f} 秒")
    print(f"  负载均衡度 (max-min)      : {best.load_imb:.2f} 秒")
    print(f"  总空载移动距离            : {best.total_distance:.2f} 米")
    print(f"  算法运行时间              : {algo_time:.2f} 秒")
    if metrics is not None:
        print("\nBaseline 指标：")
        print(f"  seed                       : {metrics['seed']}")
        print(f"  time_model                 : {metrics['time_model']}")
        print(f"  weld_speed                 : {metrics['weld_speed']:.6f} m/s")
        print(f"  travel_speed               : {metrics['travel_speed']:.6f} m/s")
        print(f"  acc                        : {metrics['acc']:.6f} m/s^2")
        print(f"  safe_z                     : {metrics['safe_z']:.6f} m")
        print(f"  setup_time                 : {metrics['setup_time']:.2f} 秒")
        print(f"  post_time                  : {metrics['post_time']:.2f} 秒")
        print(f"  split_timing_mode          : {metrics['split_timing_mode']}")
        print(f"  direction_mode             : {metrics['direction_mode']}")
        print(f"  reversed_weld_count_total  : {metrics['reversed_weld_count_total']}")
        print(f"  direction_optimized_route_count: {metrics['direction_optimized_route_count']}")
        print(f"  setup_post_saved_time_total: {metrics['setup_post_saved_time_total']:.2f} 秒")
        print(f"  merged_setup_post_count_total: {metrics['merged_setup_post_count_total']}")
        print(f"  pure_weld_time_total       : {metrics['pure_weld_time_total']:.2f} 秒")
        print(f"  setup_post_time_total      : {metrics['setup_post_time_total']:.2f} 秒")
        print(f"  weld_count                 : {metrics['weld_count']}")
        print(f"  assignment_mode            : {metrics['assignment_mode']}")
        print(f"  original_weld_count        : {metrics['original_weld_count']}")
        print(f"  subweld_count              : {metrics['subweld_count']}")
        print(f"  split_weld_count           : {metrics['split_weld_count']}")
        print(f"  cross_region_weld_count    : {metrics['cross_region_weld_count']}")
        print(f"  assigned_subweld_count     : {metrics['assigned_subweld_count']}")
        print(f"  unassigned_subweld_count   : {metrics['unassigned_subweld_count']}")
        print(f"  parent_id_count            : {metrics['parent_id_count']}")
        print(f"  split_segment_count        : {metrics['split_segment_count']}")
        print(f"  max_length_error           : {metrics['max_length_error']:.6e}")
        print(f"  sum_length_error           : {metrics['sum_length_error']:.6e}")
        if metrics['assignment_mode'] == "split" and metrics['split_timing_mode'] == "conservative":
            print("  timing_note                : no setup/post model; conservative timing matches parent-aware numerically")
        if metrics['assignment_mode'] == "split" and metrics['split_timing_mode'] == "parent_aware":
            print("  timing_note                : no setup/post model; parent-aware keeps continuity count only")
        print(f"  x_up                       : {metrics['x_up']:.6f} m")
        print(f"  x_low                      : {metrics['x_low']:.6f} m")
        print(f"  robot_counts               : {metrics['robot_counts']}")
        print(f"  sum_robot_counts           : {metrics['sum_robot_counts']}")
        print(f"  count_match                : {metrics['count_match']}")
        print(f"  total_weld_time            : {metrics['total_weld_time']:.2f} 秒")
        print(f"  total_travel_time          : {metrics['total_travel_time']:.2f} 秒")
        print(f"  total_idle_distance        : {metrics['total_idle_distance']:.2f} 米")
        print(f"  algo_time                  : {metrics['algo_time']:.2f} 秒")
        if metrics['unassigned_subweld_count'] > 0:
            print(f"  警告：存在未分配子焊缝: {metrics['unassigned_subweld_ids']}")
        if not metrics['count_match']:
            print("  警告：机器人任务数量之和与已分配子焊缝数量不一致。")
    print("="*80)

# ---------- 主函数 ----------
def _self_check_aco_order_export():
    z = DEFAULT_WELD_Z_M
    welds = [
        Weld("order_a", 0.0, 0.0, z, 1.0, 0.0, z),
        Weld("order_b", 2.0, 0.0, z, 3.0, 0.0, z),
        Weld("order_c", 4.0, 0.0, z, 5.0, 0.0, z),
    ]
    result = solve_routing_aco_with_order(welds)
    order = result["order"]
    stats = result["stats"]
    assert sorted(order) == [0, 1, 2]
    assert math.isfinite(stats["total_time"])

def _self_check_collision_audit_integration_gaaco():
    import collision_aware_schedule as cas

    z = DEFAULT_WELD_Z_M
    welds = [
        Weld("ga_r0", 9.8, 7.0, z, 9.9, 7.0, z),
        Weld("ga_r1", 10.1, 7.0, z, 10.2, 7.0, z),
        Weld("ga_r2", 1.0, 2.0, z, 1.2, 2.0, z),
        Weld("ga_r3", 14.0, 2.0, z, 14.2, 2.0, z),
    ]
    result = evaluate_partition_with_orders(welds, 10.0, 7.0)
    _, _, _, _, _, robot_orders = result
    robots, _ = get_assignment_for_partition(welds, 10.0, 7.0)
    assert len(robot_orders) == 4
    directed_sequences = cas.make_directed_sequences_from_robot_orders(
        robots,
        robot_orders,
        split_timing_mode=SPLIT_TIMING_MODE,
    )
    audit_stats = cas.audit_boundary_collisions_for_four_robots(
        directed_sequences,
        x_up=10.0,
        x_low=7.0,
        safety_distance=0.5,
        band_width=0.5,
        dt=0.5,
    )
    assert "collision_adjusted_makespan" in audit_stats

def main():
    global TIME_MODEL, ASSIGNMENT_MODE, SPLIT_TIMING_MODE, DIRECTION_MODE
    global WEIGHT_MAKESPAN, WEIGHT_LOAD, WEIGHT_DISTANCE
    global OBJECTIVE_NORMALIZATION_MODE, OBJECTIVE_NORMALIZATION_SPEC
    global CORRECTED_WELD_SPEED, CORRECTED_TRAVEL_SPEED, CORRECTED_ACC, CORRECTED_SAFE_Z
    parser = argparse.ArgumentParser(description="Run MRTA GA + ACO on a frozen weld instance.")
    parser.add_argument("--instance-path", help="Frozen instance XLSX (formal mode).")
    parser.add_argument("--source-excel", help="Raw assembly XLSX (debug generation only).")
    parser.add_argument("--excel-path", default=None, help="Deprecated alias for --source-excel.")
    parser.add_argument("--group-count", type=int, default=None, help="Requested assembly/group count in debug generation mode.")
    parser.add_argument("--weld-count", type=int, default=None, help="Deprecated expected actual weld count; never a group count.")
    parser.add_argument("--instance-seed", type=int, default=None, help="Layout seed used only in debug generation mode.")
    parser.add_argument("--solver-seed", type=int, default=None, help="Algorithm random seed.")
    parser.add_argument("--seed", type=int, default=None, help="Deprecated alias for --solver-seed.")
    parser.add_argument("--pop-size", type=int, default=POP_SIZE)
    parser.add_argument("--max-gen", type=int, default=MAX_GEN)
    parser.add_argument("--max-objective-evaluations", type=int, default=0,
                        help="Maximum complete system candidate evaluations; 0 disables the cap.")
    parser.add_argument("--output-image", default=DEFAULT_OUTPUT_IMAGE)
    parser.add_argument("--weld-z-mm", type=float, default=DEFAULT_WELD_Z_M * 1000.0)
    parser.add_argument("--min-weld-length-mm", type=float, default=DEFAULT_MIN_WELD_LENGTH_M * 1000.0)
    parser.add_argument("--metrics-csv", default=None, help="可选：追加保存 baseline 指标到 CSV 文件")
    parser.add_argument("--result-json", default=None, help="Optional complete result JSON with routes and directions.")
    parser.add_argument("--ref-makespan", type=float, default=None,
                        help="式(19)固定 Cref；三个参考值必须同时提供，否则按实例确定性生成")
    parser.add_argument("--ref-load", type=float, default=None, help="式(19)固定 Bref")
    parser.add_argument("--ref-distance", type=float, default=None, help="式(19)固定 Dref")
    parser.add_argument("--normalization-mode", choices=[OFFICIAL_MODE, LEGACY_MODE], default=LEGACY_MODE)
    parser.add_argument("--ideal-makespan", type=float)
    parser.add_argument("--ideal-load-imbalance", type=float)
    parser.add_argument("--ideal-distance", type=float)
    parser.add_argument("--baseline-makespan", type=float)
    parser.add_argument("--baseline-load-imbalance", type=float)
    parser.add_argument("--baseline-distance", type=float)
    parser.add_argument("--scale-makespan", type=float)
    parser.add_argument("--scale-load-imbalance", type=float)
    parser.add_argument("--scale-distance", type=float)
    parser.add_argument("--time-model", choices=["legacy", "corrected"], default="corrected",
                        help="时间模型：legacy 复现 Phase 0，corrected 使用 Phase 1 修正工艺模型")
    parser.add_argument("--assignment-mode", choices=["midpoint", "split"], default="split",
                        help="任务分配模式：midpoint 复现旧中点分配，split 先切分跨区域焊缝再分配")
    parser.add_argument("--split-timing-mode", choices=["conservative", "parent_aware"], default="parent_aware",
                        help="切分焊缝计时模式：Phase 10B 后 setup/post 为 0，conservative 与 parent_aware 时间数值一致")
    parser.add_argument("--direction-mode", choices=["fixed", "bidirectional"], default="bidirectional",
                        help="Weld direction mode: fixed preserves start->end, bidirectional optimizes each weld direction")
    parser.add_argument("--weight-makespan", type=float, default=WEIGHT_MAKESPAN)
    parser.add_argument("--weight-load", type=float, default=WEIGHT_LOAD)
    parser.add_argument("--weight-distance", type=float, default=WEIGHT_DISTANCE)
    parser.add_argument("--weld-speed", type=float, default=CORRECTED_WELD_SPEED)
    parser.add_argument("--travel-speed", type=float, default=CORRECTED_TRAVEL_SPEED)
    parser.add_argument("--acceleration", type=float, default=CORRECTED_ACC)
    parser.add_argument("--safe-z", type=float, default=CORRECTED_SAFE_Z)
    parser.add_argument("--enable-collision-audit", action="store_true",
                        help="Enable Phase 9B post-processing boundary collision audit.")
    parser.add_argument("--collision-safety-distance", type=float, default=0.5)
    parser.add_argument("--collision-band-width", type=float, default=None)
    parser.add_argument("--collision-dt", type=float, default=1.0)
    args = parser.parse_args()
    TIME_MODEL = args.time_model
    ASSIGNMENT_MODE = args.assignment_mode
    SPLIT_TIMING_MODE = args.split_timing_mode
    DIRECTION_MODE = args.direction_mode
    WEIGHT_MAKESPAN = args.weight_makespan
    WEIGHT_LOAD = args.weight_load
    WEIGHT_DISTANCE = args.weight_distance
    CORRECTED_WELD_SPEED = args.weld_speed
    CORRECTED_TRAVEL_SPEED = args.travel_speed
    CORRECTED_ACC = args.acceleration
    CORRECTED_SAFE_Z = args.safe_z
    OBJECTIVE_NORMALIZATION_MODE = args.normalization_mode
    OBJECTIVE_NORMALIZATION_SPEC = build_external_normalization_spec(args)
    if args.excel_path:
        if args.source_excel:
            parser.error("use --source-excel or deprecated --excel-path, not both")
        print("Warning: --excel-path is deprecated; use --source-excel.", file=sys.stderr)
        args.source_excel = args.excel_path
    solver_seed = resolve_solver_seed(args.solver_seed, args.seed)
    random.seed(solver_seed)
    np.random.seed(solver_seed)

    welds, input_metadata = load_solver_weld_input(
        instance_path=args.instance_path,
        source_excel=args.source_excel,
        group_count=args.group_count,
        instance_seed=args.instance_seed,
        input_units="mm",
        thickness_m=0.01,
        spacing_m=0.03,
        allow_rotate=True,
        min_weld_length_m=args.min_weld_length_mm / 1000.0,
        weld_z_m=args.weld_z_mm / 1000.0,
        expected_weld_count=args.weld_count,
    )
    print(
        f"Input instance: actual_weld_count={len(welds)}, "
        f"instance_seed={input_metadata.get('instance_seed')}, solver_seed={solver_seed}, "
        f"instance_hash={input_metadata.get('instance_hash')}"
    )
    print(f"实际生成焊缝数量: {len(welds)}")
    z_values = [p[2] for w in welds for p in w.endpoints()]
    z_min_mm = min(z_values) * 1000.0 if z_values else float('nan')
    z_max_mm = max(z_values) * 1000.0 if z_values else float('nan')
    lengths_mm = [w.length * 1000.0 for w in welds]
    min_length_mm = min(lengths_mm) if lengths_mm else float('nan')
    max_length_mm = max(lengths_mm) if lengths_mm else float('nan')
    mean_length_mm = float(np.mean(lengths_mm)) if lengths_mm else float('nan')
    has_short_welds = any(length_mm < args.min_weld_length_mm for length_mm in lengths_mm)
    print(f"焊缝统一高度: {args.weld_z_mm:.1f} mm")
    print(f"z_min: {z_min_mm:.1f} mm")
    print(f"z_max: {z_max_mm:.1f} mm")
    print(f"最小焊缝长度阈值: {args.min_weld_length_mm:.1f} mm")
    print(f"最终焊缝最小长度: {min_length_mm:.1f} mm")
    print(f"最终焊缝最大长度: {max_length_mm:.1f} mm")
    print(f"最终焊缝平均长度: {mean_length_mm:.1f} mm")
    print(f"是否存在短焊缝: {'是' if has_short_welds else '否'}")
    if not welds:
        print("警告：实际生成焊缝数量低于 40，请检查 generate_welds.py 的布局参数或约束。")
    elif False:
        print("提示：实际生成数量少于请求数量，但仍在 40 条以上，可继续运行。")

    print("开始遗传算法优化...")
    print(f"时间模型: {TIME_MODEL}")
    print(f"任务分配模式: {ASSIGNMENT_MODE}")
    print(f"切分计时模式: {SPLIT_TIMING_MODE}")
    print(f"Direction mode: {DIRECTION_MODE}")
    print(f"GA 参数: pop_size={args.pop_size}, max_gen={args.max_gen}, elite_size={ELITE_SIZE}")
    print("ACO 参数策略: 每个机器人区域按焊缝数量动态选择 ant_count=5/10/15, max_iter=10/20/30")
    print(f"分区方式: 两条 x 切分线 + 固定局部 y_h={FIXED_Y_H:.3f}m")
    objective_refs = (
        (1.0, 1.0, 1.0)
        if OBJECTIVE_NORMALIZATION_MODE == OFFICIAL_MODE
        else resolve_objective_references(
            welds, args.ref_makespan, args.ref_load, args.ref_distance
        )
    )
    print(
        "式(19)目标: "
        f"weights=({WEIGHT_MAKESPAN:.2f}, {WEIGHT_LOAD:.2f}, {WEIGHT_DISTANCE:.2f}), "
        f"refs=({objective_refs[0]:.6f}, {objective_refs[1]:.6f}, {objective_refs[2]:.6f})"
    )
    start_time = time.time()
    best, history = run_ga(
        welds,
        pop_size=args.pop_size,
        max_gen=args.max_gen,
        objective_refs=objective_refs,
        max_objective_evaluations=args.max_objective_evaluations,
    )
    best.max_objective_evaluations = args.max_objective_evaluations
    algo_time = time.time() - start_time

    robots, assignment_stats = get_assignment_for_partition(
        welds, best.x_up, best.x_low, assignment_mode=ASSIGNMENT_MODE
    )
    robot_counts = [len(r) for r in robots]
    metrics = collect_baseline_metrics(
        best=best,
        algo_time=algo_time,
        robot_counts=robot_counts,
        seed=solver_seed,
        weld_count=len(welds),
        assignment_stats=assignment_stats,
    )
    metrics.update(instance_metrics(input_metadata, welds, solver_seed))
    if OBJECTIVE_NORMALIZATION_MODE == OFFICIAL_MODE:
        raw_metrics = {
            "makespan": best.makespan,
            "load_imbalance": best.load_imb,
            "idle_distance": best.total_distance,
        }
        components = normalized_components(raw_metrics, OBJECTIVE_NORMALIZATION_SPEC)
        metrics.update({
            "normalization_mode": OFFICIAL_MODE,
            "normalization_source": "externally_fixed_unified_protocol",
            "normalized_makespan": components["makespan"],
            "normalized_load_imbalance": components["load_imbalance"],
            "normalized_idle_distance": components["idle_distance"],
            "fitness": normalized_objective(raw_metrics, OBJECTIVE_NORMALIZATION_SPEC),
            "normalization": OBJECTIVE_NORMALIZATION_SPEC,
        })
    else:
        metrics["normalization_mode"] = LEGACY_MODE
    robot_order_ids = [list(stats.get("route_task_ids", [])) for stats in best.robots_stats]
    robot_direction_flags = [list(stats.get("direction_flags", [])) for stats in best.robots_stats]
    robot_metrics = build_robot_metrics(robot_order_ids, robot_direction_flags, best.robots_stats)
    system_metrics = build_system_metrics(robot_metrics, metrics["fitness"])
    metrics.update(flatten_robot_metrics(robot_metrics))
    metrics.update({
        "makespan": system_metrics["makespan"],
        "load_imb": system_metrics["load_imbalance"],
        "load_imbalance": system_metrics["load_imbalance"],
        "total_weld_time": system_metrics["total_weld_time"],
        "total_travel_time": system_metrics["total_travel_time"],
        "total_idle_distance": system_metrics["total_idle_distance"],
        "sum_robot_total_time": system_metrics["sum_robot_total_time"],
        "algorithm_time": algo_time,
        "stop_reason": getattr(best, "stop_reason", "unknown"),
    })

    print(f"优化完成！最佳分区：y_h={FIXED_Y_H:.3f}m, x_up={best.x_up:.3f}m, x_low={best.x_low:.3f}m")
    print(f"四个机器人区域焊缝数量: {robot_counts}")
    if args.enable_collision_audit:
        robot_orders = [list(stats.get("route_order", [])) for stats in best.robots_stats]
        audit_stats, collision_metrics = run_collision_audit_if_enabled(
            args,
            robots,
            robot_orders,
            best.x_up,
            best.x_low,
        )
        metrics.update(collision_metrics)
        print_collision_audit_summary(audit_stats)
    print_results_table(best, algo_time, metrics=metrics)
    if args.metrics_csv:
        if save_baseline_metrics_csv(metrics, args.metrics_csv):
            print(f"baseline 指标已追加保存至: {args.metrics_csv}")

    if args.result_json:
        result_path = os.path.abspath(args.result_json)
        os.makedirs(os.path.dirname(result_path), exist_ok=True)
        result_payload = build_standard_result(
            SOLVER_NAME, input_metadata.get("instance_hash", ""), solver_seed,
            best.x_up, best.x_low, robot_metrics, system_metrics, algo_time,
        )
        result_payload.update({
            "metrics": metrics,
            "history": history,
            "checkpoint_trace": getattr(best, "checkpoint_trace", []),
            "robot_orders": [list(stats.get("route_order", [])) for stats in best.robots_stats],
            "robot_order_ids": robot_order_ids,
            "robot_direction_flags": robot_direction_flags,
            "assignment_stats": assignment_stats,
        })
        with open(result_path, "w", encoding="utf-8") as handle:
            json.dump(result_payload, handle, ensure_ascii=False, indent=2, default=str)

    visualize_solution(welds, best, output_image=args.output_image)
    print(f"输出图片路径: {args.output_image}")

if __name__ == "__main__":
    main()
