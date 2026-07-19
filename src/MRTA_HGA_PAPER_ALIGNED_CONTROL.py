"""Paper-Aligned-HGA-XCut-Control.

A paper-aligned HGA control adapted to the project-specific four-region
X-cut allocation model.  This is not a line-by-line reproduction of
Liu et al. (2025): the project has a different allocation model, open routes,
and a fixed normalized three-term objective.

The search uses objective binary tournaments, independently selected route indices a/b,
paper-defined inter-route M1-M3/M5-M6, restart VND, route-only normalized
Hamming diversity, and an incremental (mu+lambda) growth pool.
"""
from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import json
import math
import random
import statistics
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from generate_welds import Weld, instance_metrics, load_frozen_weld_instance
from mrta_problem_core import (
    DEFAULT_MOTION_MODEL, MotionModel, assign_welds_to_robots_split,
    evaluate_order, travel_time,
)
from objective_normalization import (
    BASELINE_ALGORITHM, LEGACY_MODE, OFFICIAL_MODE,
    normalized_components, normalized_objective, validate_normalization_spec,
)
from solver_output_metrics import (
    IncumbentTrace, build_robot_metrics, build_standard_result,
    build_system_metrics, flatten_robot_metrics,
)

EPS = 1e-10
PLATFORM_W_M = 20.0
WEIGHTS = (0.70, 0.20, 0.10)
SOLVER_NAME = "Paper-Aligned-HGA-XCut-Control"
PAPER_REFERENCE = "Liu et al. (2025), Expert Systems With Applications 280, 127299"
PAPER_VERSION = "section-3-paper-aligned-project-adaptation-v1"
DEFAULT_CONFIG_PATH = "config/profiles/paper_aligned_hga_control_defaults.json"
MODULE_DEFAULTS = {
    "mu": 20, "lamb": 10, "alpha_neighbors": 20,
    "offspring_pool_mode": "incremental", "vnd_mode": "full",
    "improvement_mode": "first", "boundary_mutation_rate": 0.10,
    "route_mutation_rate": 0.0, "project_fallback_enabled": False,
    "partition_neighborhood_enabled": True,
    "paper_route_crossover_enabled": True, "paper_vnd_enabled": True,
    "alpha_candidates_enabled": True, "biased_fitness_enabled": True,
    "biased_diversity_coefficient": 1.0,
    "max_generations": 0, "max_offspring": 200000,
    "max_objective_evaluations": 0,
    "max_local_search_moves": 200000, "max_neighbor_checks": 300,
    "no_improve_limit": 0, "time_limit_s": 0.0,
}


class ObjectiveBudgetExceeded(RuntimeError):
    """Internal control-flow signal for the non-invasive objective budget cap."""


def clamp(value: float) -> float:
    return min(PLATFORM_W_M, max(0.0, float(value)))


def _round_point(point) -> Tuple[float, float, float]:
    return tuple(round(float(v), 9) for v in point)


def task_id(weld: Weld) -> str:
    return str(getattr(weld, "id", getattr(weld, "wid", "")))


def geometry_id(weld: Weld) -> str:
    parent = str(getattr(weld, "parent_id", getattr(weld, "id", "")))
    a, b = sorted((_round_point(weld.start_point()), _round_point(weld.end_point())))
    return f"{parent}|{a}|{b}"


@dataclass
class PaperAlignedHGAIndividual:
    """Joint X-cut and four-route individual; direction remains DP-decoded."""
    x_up: float
    x_low: float
    robot_order_ids: List[List[str]] = None
    robot_orders: List[List[int]] = None
    makespan: float = math.inf
    load_imbalance: float = math.inf
    total_idle_distance: float = math.inf
    fitness: float = math.inf
    robots_stats: List[dict] = None
    assignment_stats: dict = None
    objective_rank: Optional[float] = 0.0
    diversity_rank: Optional[float] = 0.0
    biased_fitness: Optional[float] = 0.0
    generation: int = 0
    diagnostics: dict = None

    def __post_init__(self):
        if self.robot_order_ids is None: self.robot_order_ids = [[] for _ in range(4)]
        if self.robot_orders is None: self.robot_orders = [[] for _ in range(4)]
        if self.robots_stats is None: self.robots_stats = []
        if self.assignment_stats is None: self.assignment_stats = {}
        if self.diagnostics is None: self.diagnostics = {}


@dataclass(frozen=True)
class ObjectiveRefs:
    makespan: float
    load: float
    distance: float
    normalization: Optional[dict] = field(default=None, compare=False, hash=False)


def derive_refs(welds: Sequence[Weld], model: MotionModel = DEFAULT_MOTION_MODEL) -> ObjectiveRefs:
    total_weld = sum(float(w.length) / model.weld_speed for w in welds)
    makespan = max(total_weld / 4.0, 1e-12)
    max_vertical = max((abs(model.safe_z-p[2]) for w in welds for p in w.endpoints()), default=model.safe_z)
    max_move = math.hypot(20.0, 12.0) + 2.0*max_vertical
    return ObjectiveRefs(makespan, makespan, max(max(len(welds)-4, 1)*max_move, 1e-12))


def objective(makespan: float, load: float, distance: float, refs: ObjectiveRefs) -> float:
    if refs.normalization is not None:
        return normalized_objective(
            {"makespan": makespan, "load_imbalance": load, "idle_distance": distance},
            refs.normalization,
        )
    return (WEIGHTS[0]*makespan/refs.makespan + WEIGHTS[1]*load/refs.load +
            WEIGHTS[2]*distance/refs.distance)


def _copy_routes(routes: Sequence[Sequence[str]]) -> List[List[str]]:
    return [list(route) for route in routes]


def _legal(routes: Sequence[Sequence[str]], legal_sets: Sequence[set]) -> bool:
    flat = [x for route in routes for x in route]
    return (len(flat) == len(set(flat)) and
            all(set(route) <= legal_sets[r] for r, route in enumerate(routes)))


def paper_move_candidates(
    routes: Sequence[Sequence[str]], legal_sets: Sequence[set],
    candidate_lists: Dict[str, List[str]], operator: int, critical_route: int,
    max_checks: int = 300,
) -> Tuple[List[List[List[str]]], int, int]:
    """Enumerate paper M1-M6 orders without evaluating the objective.

    This pure helper allows artificial overlapping legal sets in unit tests.
    Production calls pass the strict unique-ownership sets from split assignment.
    Every operator is filtered by the top-alpha candidate list.
    Returns (feasible candidates, checked pairs, infeasible candidates).
    """
    out: List[List[List[str]]] = []
    checks = infeasible = 0

    def near(u: str, v: str) -> bool:
        return v in candidate_lists.get(u, []) or u in candidate_lists.get(v, [])

    def add(candidate):
        nonlocal infeasible
        if _legal(candidate, legal_sets): out.append(candidate)
        else: infeasible += 1

    if operator == 1:  # Relocate u from longest route after v in another route.
        a = critical_route
        for i, u in enumerate(routes[a]):
            for b, route_b in enumerate(routes):
                if b == a: continue
                for j, v in enumerate(route_b):
                    if checks >= max_checks: return out, checks, infeasible
                    if not near(u, v): continue
                    checks += 1; nr = _copy_routes(routes)
                    nr[a].pop(i); nr[b].insert(j + 1, u); add(nr)
    elif operator == 2:  # Inter-route swap, one route is longest.
        a = critical_route
        for b in range(len(routes)):
            if b == a: continue
            for i, u in enumerate(routes[a]):
                for j, v in enumerate(routes[b]):
                    if checks >= max_checks: return out, checks, infeasible
                    if not near(u, v): continue
                    checks += 1; nr = _copy_routes(routes)
                    nr[a][i], nr[b][j] = nr[b][j], nr[a][i]; add(nr)
    elif operator == 3:  # Swap consecutive pairs across routes.
        a = critical_route
        for b in range(len(routes)):
            if b == a: continue
            for i in range(max(0, len(routes[a]) - 1)):
                for j in range(max(0, len(routes[b]) - 1)):
                    u, v = routes[a][i], routes[b][j]
                    if checks >= max_checks: return out, checks, infeasible
                    if not near(u, v): continue
                    checks += 1; nr = _copy_routes(routes)
                    nr[a][i:i+2], nr[b][j:j+2] = nr[b][j:j+2], nr[a][i:i+2]; add(nr)
    elif operator == 4:  # Intra-route 2-opt; order only, direction is decoded later.
        for a, route in enumerate(routes):
            for i in range(len(route) - 1):
                for j in range(i + 1, len(route)):
                    if checks >= max_checks: return out, checks, infeasible
                    if not near(route[i], route[j]): continue
                    checks += 1; nr = _copy_routes(routes)
                    nr[a][i:j+1] = reversed(nr[a][i:j+1]); add(nr)
    elif operator in (5, 6):
        # At least one route is the longest. Cuts identify paper edges (u,x),(v,y).
        a = critical_route
        for b in range(len(routes)):
            if b == a: continue
            for i in range(max(0, len(routes[a]) - 1)):
                for j in range(max(0, len(routes[b]) - 1)):
                    u, v = routes[a][i], routes[b][j]
                    if checks >= max_checks: return out, checks, infeasible
                    if not near(u, v): continue
                    checks += 1
                    if operator == 5:
                        # (u,x),(v,y) -> (u,v),(x,y), open-route realization.
                        nr = _copy_routes(routes)
                        nr[a] = list(routes[a][:i+1]) + list(reversed(routes[b][:j+1]))
                        nr[b] = list(reversed(routes[a][i+1:])) + list(routes[b][j+1:])
                    else:
                        # (u,x),(v,y) -> (u,y),(v,x): ordinary suffix exchange.
                        nr = _copy_routes(routes)
                        nr[a] = list(routes[a][:i+1]) + list(routes[b][j+1:])
                        nr[b] = list(routes[b][:j+1]) + list(routes[a][i+1:])
                    add(nr)
    else:
        raise ValueError("operator must be in 1..6")
    return out, checks, infeasible


class HGAControlCore:
    """Final-HGA-only model adapter: caches, repair, evaluation and X-cut moves."""
    def __init__(self, welds: Sequence[Weld], seed: int = 42, *,
                 alpha_neighbors: int = 20, max_neighbor_checks: int = 300,
                 refs: Optional[ObjectiveRefs] = None,
                 model: MotionModel = DEFAULT_MOTION_MODEL,
                 max_objective_evaluations: int = 0):
        self.welds = list(welds); self.rng = random.Random(seed); self.seed = seed
        self.alpha_neighbors = max(0, int(alpha_neighbors))
        self.max_neighbor_checks = max(1, int(max_neighbor_checks))
        self.model = model
        self.refs = refs or derive_refs(self.welds, self.model)
        self.max_objective_evaluations = max(0, int(max_objective_evaluations))
        self.trace = IncumbentTrace(); self.trace_started = time.perf_counter()
        self.assignment_cache = {}; self.route_cache = {}; self.candidate_cache = {}
        self.complete_candidate_keys = set()
        self.counts = {k: 0 for k in (
            "boundary_crossover_count route_duplicate_remove_count route_invalidated_count "
            "route_repair_insert_count partition_vnd_move_count partition_vnd_improvement_count "
            "candidate_list_build_count fitness_evaluation_count assignment_cache_hit "
            "assignment_cache_miss route_cache_hit route_cache_miss "
            "unique_complete_candidate_count duplicate_complete_candidate_count"
        ).split()}

    def _assignment(self, x_up, x_low):
        key=(round(clamp(x_up),9),round(clamp(x_low),9))
        if key in self.assignment_cache:
            self.counts["assignment_cache_hit"]+=1
            robots,stats=self.assignment_cache[key];return robots,dict(stats)
        self.counts["assignment_cache_miss"]+=1
        robots,stats=assign_welds_to_robots_split(self.welds,*key)
        self.assignment_cache[key]=(robots,dict(stats));return robots,dict(stats)

    def _route_eval(self, welds, ids):
        by_id={task_id(w):i for i,w in enumerate(welds)};order=[by_id[i] for i in ids]
        key=(tuple((task_id(w),geometry_id(w)) for w in welds),tuple(ids),
             self.model,"bidirectional","open_no_return",self.refs)
        if key in self.route_cache:
            self.counts["route_cache_hit"]+=1;cost,stats=self.route_cache[key]
            return cost,dict(stats),order
        self.counts["route_cache_miss"]+=1
        cost,stats=evaluate_order(welds,order,self.model)
        self.route_cache[key]=(cost,dict(stats));return cost,dict(stats),order

    def _assert_feasible(self, ind, robots):
        expected=[task_id(w) for route in robots for w in route]
        actual=[x for route in ind.robot_order_ids for x in route]
        if len(actual)!=len(set(actual)) or set(actual)!=set(expected):
            raise AssertionError("decoded routes must contain every current subweld exactly once")
        for r,route in enumerate(ind.robot_order_ids):
            if not set(route)<={task_id(w) for w in robots[r]}:
                raise AssertionError("route contains a task owned by another robot")
        stats=ind.assignment_stats
        if int(stats.get("unassigned_subweld_count",-1))!=0:raise AssertionError("unassigned subweld")
        if int(stats.get("assigned_subweld_count",-1))!=int(stats.get("subweld_count",-2)):
            raise AssertionError("assignment count mismatch")
        if float(stats.get("sum_length_error",math.inf))>1e-8:raise AssertionError("length conservation failed")

    def _evaluate(self, ind, robots):
        if (
            self.max_objective_evaluations
            and self.counts["fitness_evaluation_count"] >= self.max_objective_evaluations
        ):
            raise ObjectiveBudgetExceeded("HGA system objective evaluation budget exhausted")
        complete_key=(round(ind.x_up,9),round(ind.x_low,9),
                      tuple(tuple(route) for route in ind.robot_order_ids))
        if complete_key in self.complete_candidate_keys:
            self.counts["duplicate_complete_candidate_count"]+=1
        else:
            self.complete_candidate_keys.add(complete_key)
            self.counts["unique_complete_candidate_count"]+=1
        stats=[];orders=[]
        for welds,ids in zip(robots,ind.robot_order_ids):
            _,route_stats,order=self._route_eval(welds,ids);stats.append(route_stats);orders.append(order)
        times=[float(s["total_time"]) for s in stats]
        ind.robot_orders=orders;ind.robots_stats=stats
        ind.makespan=max(times,default=0.0)
        ind.load_imbalance=max(times,default=0.0)-min(times,default=0.0)
        ind.total_idle_distance=sum(float(s["total_idle_distance"]) for s in stats)
        ind.fitness=objective(ind.makespan,ind.load_imbalance,ind.total_idle_distance,self.refs)
        self.counts["fitness_evaluation_count"]+=1
        self.trace.observe(self.counts["fitness_evaluation_count"],ind.fitness,ind.makespan,
                           ind.load_imbalance,ind.total_idle_distance,
                           time.perf_counter()-self.trace_started)
        return ind

    def _best_insertion(self, base, robots, robot, missing_id):
        best=None
        for pos in range(len(base.robot_order_ids[robot])+1):
            ids=list(base.robot_order_ids[robot]);ids.insert(pos,missing_id)
            route_time,_,_=self._route_eval(robots[robot],ids)
            # Repair construction is not a complete four-robot candidate, so
            # use the exact route decoder here.  The completed individual is
            # evaluated immediately afterward with the shared system objective.
            item=(round(route_time,14),pos)
            if best is None or item<best[0]:best=(item,pos)
        return best[1]

    def decode_and_repair_individual(self, individual, inherited_route_templates=None, *, randomize_missing=False):
        individual.x_up=clamp(individual.x_up);individual.x_low=clamp(individual.x_low)
        robots,assignment_stats=self._assignment(individual.x_up,individual.x_low)
        templates=inherited_route_templates if inherited_route_templates is not None else individual.robot_order_ids
        individual.robot_order_ids=[[] for _ in range(4)];seen=set();invalid=duplicates=inserted=0
        for r in range(4):
            legal={task_id(w):w for w in robots[r]};geo={geometry_id(w):task_id(w) for w in robots[r]}
            for inherited in (templates[r] if r<len(templates) else []):
                mapped=inherited if inherited in legal else geo.get(str(inherited))
                if mapped is None:invalid+=1;continue
                if mapped in seen:duplicates+=1;continue
                individual.robot_order_ids[r].append(mapped);seen.add(mapped)
        individual.assignment_stats=assignment_stats
        for r in range(4):
            missing=[task_id(w) for w in robots[r] if task_id(w) not in seen]
            if randomize_missing:self.rng.shuffle(missing)
            else:missing.sort()
            for mid in missing:
                pos=self._best_insertion(individual,robots,r,mid) if individual.robot_order_ids[r] else 0
                individual.robot_order_ids[r].insert(pos,mid);seen.add(mid);inserted+=1
        individual.diagnostics.update({"invalidated_task_count":invalid,
            "duplicate_removed_count":duplicates,"repair_insert_count":inserted})
        self.counts["route_invalidated_count"]+=invalid
        self.counts["route_duplicate_remove_count"]+=duplicates
        self.counts["route_repair_insert_count"]+=inserted
        self._evaluate(individual,robots);self._assert_feasible(individual,robots);return individual

    def build_candidate_lists(self, ind):
        robots,_=self._assignment(ind.x_up,ind.x_low);all_welds=[w for route in robots for w in route]
        key=(round(ind.x_up,9),round(ind.x_low,9),self.alpha_neighbors,tuple(task_id(w) for w in all_welds))
        if key in self.candidate_cache:return copy.deepcopy(self.candidate_cache[key])
        result={}
        for a in all_welds:
            scored=[]
            for b in all_welds:
                if task_id(a)==task_id(b):continue
                cost=min(travel_time(pa,pb,self.model) for pa in a.endpoints() for pb in b.endpoints())
                scored.append((cost,math.dist(a.midpoint(),b.midpoint()),
                    0 if getattr(a,"parent_id",a.id)==getattr(b,"parent_id",b.id) else 1,task_id(b)))
            scored.sort();result[task_id(a)]=[x[-1] for x in scored[:self.alpha_neighbors]]
        self.candidate_cache[key]=copy.deepcopy(result);self.counts["candidate_list_build_count"]+=1;return result

    def _candidate(self, ind, routes):
        cand=copy.deepcopy(ind);cand.robot_order_ids=[list(r) for r in routes]
        robots,_=self._assignment(cand.x_up,cand.x_low);return self._evaluate(cand,robots),robots

    def partition_vnd(self, ind):
        current=copy.deepcopy(ind)
        events=sorted({clamp(p[0]+e) for w in self.welds for p in w.endpoints() for e in (-1e-6,0.0,1e-6)})
        moves=[(clamp(current.x_up+d),current.x_low) for d in (-.5,.5)]
        moves += [(current.x_up,clamp(current.x_low+d)) for d in (-.5,.5)]
        moves += [(clamp(current.x_up+d),clamp(current.x_low+d)) for d in (-.5,.5)]
        for value in (current.x_up,current.x_low):
            near=sorted(events,key=lambda x:(abs(x-value),x))[:4]
            moves += [(x,current.x_low) for x in near]+[(current.x_up,x) for x in near]
        seen=set()
        for xu,xl in moves[:self.max_neighbor_checks]:
            key=(round(xu,9),round(xl,9))
            if key in seen or key==(round(current.x_up,9),round(current.x_low,9)):continue
            seen.add(key);self.counts["partition_vnd_move_count"]+=1
            cand=self.decode_and_repair_individual(PaperAlignedHGAIndividual(xu,xl,generation=current.generation),current.robot_order_ids)
            if cand.fitness<current.fitness-EPS:
                current=cand;self.counts["partition_vnd_improvement_count"]+=1
                if self.improvement_mode=="first":break
        return current


class PaperAlignedHGA(HGAControlCore):
    def __init__(self, welds, seed=42, *, alpha_neighbors=20,
                 max_neighbor_checks=300, vnd_mode="full",
                 improvement_mode="first", max_local_search_moves=200000,
                 enable_paper_vnd=True, enable_alpha=True,
                 enable_biased_fitness=True, enable_project_fallback=False,
                 enable_partition=True, enable_route_crossover=True,
                 biased_diversity_coefficient=1.0,
                 boundary_mutation_rate=0.10, route_mutation_rate=0.0,
                 refs=None, model=DEFAULT_MOTION_MODEL,
                 max_objective_evaluations=0):
        super().__init__(welds, seed, alpha_neighbors=alpha_neighbors,
                         max_neighbor_checks=max_neighbor_checks, refs=refs,
                         model=model,
                         max_objective_evaluations=max_objective_evaluations)
        self.vnd_mode = vnd_mode
        self.improvement_mode = improvement_mode
        self.max_local_search_moves = max(1, int(max_local_search_moves))
        self.enable_paper_vnd = enable_paper_vnd
        self.enable_alpha = enable_alpha
        self.enable_biased_fitness = enable_biased_fitness
        self.enable_project_fallback = enable_project_fallback
        self.enable_partition = enable_partition
        self.enable_route_crossover = enable_route_crossover
        self.biased_diversity_coefficient = float(biased_diversity_coefficient)
        self.boundary_mutation_rate = max(0.0, min(1.0, float(boundary_mutation_rate)))
        self.route_mutation_rate = max(0.0, min(1.0, float(route_mutation_rate)))
        self._paper_counts = self._new_paper_counts()
        self.initialization_budget_exhausted = False

    @staticmethod
    def _new_paper_counts():
        c = {k: 0 for k in (
            "binary_tournament_count binary_tournament_tie_count paper_route_crossover_count "
            "cross_index_route_replacement_count route_mapping_success_count "
            "route_mapping_rejected_count duplicate_remove_count repair_insert_count "
            "project_fallback_operator_checks project_fallback_operator_accepts "
            "partition_neighbor_checks partition_neighbor_accepts "
            "pool_incremental_child_parent_use_count population_update_count "
            "duplicate_rejection_count vnd_restart_count vnd_budget_stop_count "
            "vnd_time_stop_count vnd_total_candidate_checks vnd_call_count "
            "vnd_completed_local_optimum_count boundary_mutation_attempt_count "
            "boundary_mutation_effective_count route_mutation_attempt_count "
            "route_mutation_effective_count"
        ).split()}
        c["route_a_index_count"] = [0, 0, 0, 0]
        c["route_b_index_count"] = [0, 0, 0, 0]
        for i in range(1, 7):
            for s in ("checks", "feasible", "infeasible", "accepts"):
                c[f"paper_m{i}_{s}"] = 0
        return c

    def paper_mutation(self, ind, boundary_rate=None, route_rate=None):
        """Project-required X-cut mutation, separate from optional route mutation.

        Liu et al.'s disclosed HGA flow has no independent route mutation.  The
        formal control therefore defaults route_rate to zero.  Both branches
        always pass through the verified unified decoder/repair.
        """
        boundary_rate = self.boundary_mutation_rate if boundary_rate is None else float(boundary_rate)
        route_rate = self.route_mutation_rate if route_rate is None else float(route_rate)
        child = copy.deepcopy(ind); templates = _copy_routes(child.robot_order_ids)
        self._paper_counts["boundary_mutation_attempt_count"] += 1
        boundary_changed = False
        if self.rng.random() < boundary_rate:
            attrs = [self.rng.choice(("x_up", "x_low"))]
            if self.rng.random() < 0.25: attrs = ["x_up", "x_low"]
            for attr in attrs:
                old = getattr(child, attr)
                value = self.rng.uniform(0, 20) if self.rng.random() < 0.2 else old + self.rng.gauss(0, 1.5)
                setattr(child, attr, clamp(value)); boundary_changed |= abs(getattr(child, attr)-old) > EPS
        self._paper_counts["boundary_mutation_effective_count"] += int(boundary_changed)

        self._paper_counts["route_mutation_attempt_count"] += int(route_rate > 0)
        route_changed = False
        if route_rate > 0 and self.rng.random() < route_rate:
            available = [r for r, route in enumerate(templates) if len(route) >= 2]
            if available:
                r = self.rng.choice(available); route = templates[r]
                op = self.rng.choice(("project_route_relocate", "project_route_swap", "project_route_reverse"))
                i, j = sorted(self.rng.sample(range(len(route)), 2))
                if op == "project_route_swap": route[i], route[j] = route[j], route[i]
                elif op == "project_route_reverse": route[i:j+1] = reversed(route[i:j+1])
                else:
                    item = route.pop(i); route.insert(min(j, len(route)), item)
                route_changed = True
        self._paper_counts["route_mutation_effective_count"] += int(route_changed)
        return self.decode_and_repair_individual(child, templates)

    def binary_tournament(self, pool):
        if not pool: raise ValueError("empty tournament pool")
        if len(pool) == 1: return pool[0]
        a, b = self.rng.sample(list(pool), 2)
        self._paper_counts["binary_tournament_count"] += 1
        if abs(a.fitness - b.fitness) <= EPS:
            self._paper_counts["binary_tournament_tie_count"] += 1
        return min((a, b), key=lambda x: (x.fitness, x.x_up, x.x_low,
                                           tuple(tuple(r) for r in x.robot_order_ids)))

    def _all_candidate_lists(self, ind):
        if self.enable_alpha: return self.build_candidate_lists(ind)
        ids = [x for route in ind.robot_order_ids for x in route]
        return {x: [y for y in ids if y != x] for x in ids}

    def paper_crossover(self, parent_a, parent_b, generation, forced_indices=None):
        alpha = self.rng.random()
        if self.rng.random() < 0.5:
            xu = alpha*parent_a.x_up + (1-alpha)*parent_b.x_up
            xl = alpha*parent_a.x_low + (1-alpha)*parent_b.x_low
        else:
            g = self.rng.uniform(-0.25, 1.25)
            xu = parent_a.x_up + g*(parent_b.x_up-parent_a.x_up)
            xl = parent_a.x_low + g*(parent_b.x_low-parent_a.x_low)
        self.counts["boundary_crossover_count"] += 1
        a, b = forced_indices if forced_indices is not None else (self.rng.randrange(4), self.rng.randrange(4))
        self._paper_counts["route_a_index_count"][a] += 1
        self._paper_counts["route_b_index_count"][b] += 1
        self._paper_counts["cross_index_route_replacement_count"] += int(a != b)
        templates = _copy_routes(parent_a.robot_order_ids)
        donor = list(parent_b.robot_order_ids[b]) if self.enable_route_crossover else list(parent_a.robot_order_ids[a])
        templates[a] = donor
        child = PaperAlignedHGAIndividual(clamp(xu), clamp(xl), generation=generation)
        before = copy.deepcopy(self.counts)
        child = self.decode_and_repair_individual(child, templates, randomize_missing=True)
        robots, _ = self._assignment(child.x_up, child.x_low)
        legal_a = {task_id(w) for w in robots[a]}
        mapped = sum(x in legal_a and x in set(child.robot_order_ids[a]) for x in donor)
        rejected = len(donor) - mapped
        if self.enable_route_crossover: self._paper_counts["paper_route_crossover_count"] += 1
        self._paper_counts["route_mapping_success_count"] += mapped
        self._paper_counts["route_mapping_rejected_count"] += rejected
        self._paper_counts["duplicate_remove_count"] += self.counts["route_duplicate_remove_count"] - before["route_duplicate_remove_count"]
        self._paper_counts["repair_insert_count"] += self.counts["route_repair_insert_count"] - before["route_repair_insert_count"]
        child.diagnostics.update({"paper_route_a": a, "paper_route_b": b,
                                  "paper_route_mapping_success": mapped,
                                  "paper_route_mapping_rejected": rejected})
        return child

    def _critical_route(self, ind):
        times = [float(s["total_time"]) for s in ind.robots_stats]
        return min(range(4), key=lambda r: (-times[r], r))

    def _paper_neighborhood(self, ind, operator, remaining_budget):
        robots, _ = self._assignment(ind.x_up, ind.x_low)
        legal_sets = [{task_id(w) for w in route} for route in robots]
        max_checks = self._neighborhood_check_limit(remaining_budget)
        candidates, checks, infeasible = paper_move_candidates(
            ind.robot_order_ids, legal_sets, self._all_candidate_lists(ind),
            operator, self._critical_route(ind), max_checks)
        self._paper_counts[f"paper_m{operator}_checks"] += checks
        self._paper_counts[f"paper_m{operator}_infeasible"] += infeasible
        evaluated = []
        for routes in candidates:
            cand, current_robots = self._candidate(ind, routes)
            self._assert_feasible(cand, current_robots)
            self._paper_counts[f"paper_m{operator}_feasible"] += 1
            evaluated.append(cand)
            if self.improvement_mode == "first" and cand.fitness < ind.fitness-EPS: break
        if not evaluated: return None, checks
        if self.improvement_mode == "first":
            return next((x for x in evaluated if x.fitness < ind.fitness-EPS), None), checks
        best = min(evaluated, key=lambda x:(x.fitness, tuple(tuple(r) for r in x.robot_order_ids)))
        return (best if best.fitness < ind.fitness-EPS else None), checks

    def _neighborhood_check_limit(self, remaining_budget):
        """Full exhausts Nk; bounded caps each Nk and the total VND budget."""
        return (remaining_budget if self.vnd_mode == "full"
                else min(self.max_neighbor_checks, remaining_budget))

    def _project_fallback(self, ind, remaining_budget):
        """A1-A3 intra-route moves, explicitly not paper M1-M3."""
        checked = 0; candidates=[]; neighbors=self._all_candidate_lists(ind)
        for r, route in enumerate(ind.robot_order_ids):
            for i in range(len(route)):
                for j in range(i+1, len(route)):
                    if checked >= min(self.max_neighbor_checks, remaining_budget): break
                    if route[j] not in neighbors.get(route[i],[]): continue
                    for op in range(3):
                        nr=_copy_routes(ind.robot_order_ids)
                        if op==0:
                            x=nr[r].pop(i); nr[r].insert(j,x)
                        elif op==1: nr[r][i],nr[r][j]=nr[r][j],nr[r][i]
                        elif i+1<len(route):
                            block=nr[r][i:i+2]; del nr[r][i:i+2]; nr[r][min(j,len(nr[r])):min(j,len(nr[r]))]=block
                        else: continue
                        checked += 1; self._paper_counts["project_fallback_operator_checks"] += 1
                        cand,_=self._candidate(ind,nr); candidates.append(cand)
                        if self.improvement_mode=="first" and cand.fitness<ind.fitness-EPS: return cand,checked
        if candidates:
            best=min(candidates,key=lambda x:x.fitness)
            if best.fitness<ind.fitness-EPS:return best,checked
        return None,checked

    def paper_vnd(self, ind, time_deadline=None):
        if not self.enable_paper_vnd: return ind
        self._paper_counts["vnd_call_count"] += 1
        current=copy.deepcopy(ind); k=1; used=0; stopped=False
        while k<=6:
            if time_deadline and time.perf_counter()>=time_deadline:
                self._paper_counts["vnd_time_stop_count"] += 1; stopped=True; break
            if used>=self.max_local_search_moves:
                self._paper_counts["vnd_budget_stop_count"] += 1; stopped=True; break
            candidate,checks=self._paper_neighborhood(current,k,self.max_local_search_moves-used); used+=checks
            self._paper_counts["vnd_total_candidate_checks"] += checks
            if candidate is not None:
                current=candidate; self._paper_counts[f"paper_m{k}_accepts"]+=1
                self._paper_counts["vnd_restart_count"]+=1; k=1
            else:k+=1
        if self.vnd_mode == "full" and k > 6 and not stopped:
            self._paper_counts["vnd_completed_local_optimum_count"] += 1
        if self.enable_project_fallback and used<self.max_local_search_moves and not stopped:
            cand,checks=self._project_fallback(current,self.max_local_search_moves-used); used+=checks
            self._paper_counts["vnd_total_candidate_checks"] += checks
            if cand is not None:
                current=cand; self._paper_counts["project_fallback_operator_accepts"]+=1
        return current

    def project_partition_search(self, ind):
        if not self.enable_partition:return ind
        # Reuse the verified repair-based P-neighborhood, but keep paper stats separate.
        before_checks=self.counts["partition_vnd_move_count"]
        before_accepts=self.counts["partition_vnd_improvement_count"]
        result=super().partition_vnd(ind)
        self._paper_counts["partition_neighbor_checks"] += self.counts["partition_vnd_move_count"]-before_checks
        self._paper_counts["partition_neighbor_accepts"] += self.counts["partition_vnd_improvement_count"]-before_accepts
        return result

    def local_search(self, ind, deadline=None):
        return self.project_partition_search(self.paper_vnd(ind,deadline))

    def initialize_paper(self, mu, deadline=None):
        # Same legal project boundary seeds, but paper-style random task extraction
        # and a complete restart VND for each independently constructed solution.
        mu=max(1,int(mu)); boundaries=[(10.0,10.0)]
        best=None
        for xu in range(0,21,2):
            for xl in range(0,21,2):
                robots,stats=self._assignment(xu,xl)
                loads=[sum(w.length/self.model.weld_speed for w in r) for r in robots]
                item=(max(loads)-min(loads),max(loads),xu,xl)
                if stats["unassigned_subweld_count"]==0 and (best is None or item<best):best=item
        if mu>1 and best is not None:boundaries.append((best[2],best[3]))
        rem=mu-len(boundaries); p1=list(range(rem));p2=list(range(rem));self.rng.shuffle(p1);self.rng.shuffle(p2)
        for i in range(rem):boundaries.append(((p1[i]+self.rng.random())/rem*20,(p2[i]+self.rng.random())/rem*20))
        pop=[]
        self.initialization_budget_exhausted = False
        for xu,xl in boundaries:
            try:
                ind=PaperAlignedHGAIndividual(xu,xl,generation=0)
                ind=self.decode_and_repair_individual(ind,randomize_missing=True)
                # Keep the last fully evaluated decoded individual.  If the
                # local search reaches the hard objective budget, it remains a
                # valid resident and lets the solver close the run cleanly.
                pop.append(ind)
                pop[-1]=self.local_search(ind,deadline)
            except ObjectiveBudgetExceeded:
                self.initialization_budget_exhausted = True
                break
        if not pop:
            raise RuntimeError("objective budget exhausted before HGA produced one complete individual")
        return pop

    @staticmethod
    def _paper_edges(ind):
        """Paper edge identity over executable full stable subweld IDs."""
        return {(r, str(a), str(b)) for r,route in enumerate(ind.robot_order_ids)
                for a,b in zip(route[:-1],route[1:])}

    def route_hamming_distance(self,a,b):
        ea,eb=self._paper_edges(a),self._paper_edges(b); denom=len(ea)+len(eb)
        return len(ea^eb)/denom if denom else 0.0

    def _rank_paper_pool(self, pool):
        """Assign 1-based objective/diversity ranks without selecting survivors."""
        n=len(pool)
        for rank,i in enumerate(sorted(range(n),key=lambda i:(pool[i].fitness,pool[i].x_up,pool[i].x_low)),1):pool[i].objective_rank=float(rank)
        contrib=[]
        for i in range(n):
            ds=sorted(self.route_hamming_distance(pool[i],pool[j]) for j in range(n) if j!=i)
            contrib.append(sum(ds[:min(3,len(ds))])/min(3,len(ds)) if ds else 0.0)
        for rank,i in enumerate(sorted(range(n),key=lambda i:(-contrib[i],pool[i].x_up,pool[i].x_low)),1):
            pool[i].diversity_rank=float(rank)
            pool[i].biased_fitness=(pool[i].objective_rank+self.biased_diversity_coefficient*pool[i].diversity_rank
                                      if self.enable_biased_fitness else pool[i].objective_rank)
        return pool

    @staticmethod
    def _same_solution(a, b):
        return (abs(a.fitness-b.fitness)<=EPS and abs(a.x_up-b.x_up)<=EPS and
                abs(a.x_low-b.x_low)<=EPS and a.robot_order_ids==b.robot_order_ids)

    def apply_final_best_ranks(self, best, population):
        """Rank final residents and copy ranks to best, or use honest nulls."""
        self._rank_paper_pool(population)
        match=next((x for x in population if self._same_solution(best,x)),None)
        if match is None:
            best.objective_rank=None;best.diversity_rank=None;best.biased_fitness=None
            return False
        best.objective_rank=match.objective_rank;best.diversity_rank=match.diversity_rank
        best.biased_fitness=match.biased_fitness
        return True

    def finalize_operator_metrics(self):
        for i in range(1,7):
            checks=self._paper_counts[f"paper_m{i}_checks"]
            self._paper_counts[f"paper_m{i}_feasible_rate"]=(self._paper_counts[f"paper_m{i}_feasible"]/checks if checks else 0.0)
        cross=(1,2,3,5,6)
        self._paper_counts["paper_cross_robot_operator_feasible_total"]=sum(self._paper_counts[f"paper_m{i}_feasible"] for i in cross)
        self._paper_counts["paper_cross_robot_operator_accept_total"]=sum(self._paper_counts[f"paper_m{i}_accepts"] for i in cross)

    def update_paper_pool(self,pool,mu):
        n=len(pool); best=min(pool,key=lambda x:(x.fitness,x.x_up,x.x_low))
        self._rank_paper_pool(pool)
        survivors=[best]
        for x in sorted(pool,key=lambda x:(x.biased_fitness,x.fitness,x.x_up,x.x_low)):
            if x is best:continue
            if any(self.route_hamming_distance(x,s)<=EPS for s in survivors):
                self._paper_counts["duplicate_rejection_count"]+=1;continue
            survivors.append(x)
            if len(survivors)==mu:break
        for x in sorted(pool,key=lambda x:(x.fitness,x.x_up,x.x_low)):
            if len(survivors)==mu:break
            if all(x is not y for y in survivors):survivors.append(x)
        self._paper_counts["population_update_count"]+=1
        return survivors

    def run_paper(self,mu=20,lamb=10,max_generations=0,max_offspring=200000,
                  no_improve_limit=0,time_limit_s=0,boundary_mutation_rate=None,
                  route_mutation_rate=None,
                  offspring_pool_mode="incremental"):
        start=time.perf_counter();self.trace_started=start;deadline=start+time_limit_s if time_limit_s>0 else None
        population=self.initialize_paper(mu,deadline);best=copy.deepcopy(min(population,key=lambda x:x.fitness))
        history=[best.fitness];gen_best=[best.fitness];offspring_index=0;best_offspring_index=0;best_generation=0;no_improve=0;gen=0
        objective_budget_exhausted=self.initialization_budget_exhausted
        generation_limit=max_generations if max_generations>0 else math.inf
        while (not objective_budget_exhausted and gen<generation_limit and
               offspring_index<max_offspring):
            if deadline and time.perf_counter()>=deadline:break
            gen+=1; base_pool=list(population); children=[]
            while len(children)<lamb and offspring_index<max_offspring:
                selection_pool=(base_pool+children if offspring_pool_mode=="incremental" else base_pool)
                pa=self.binary_tournament(selection_pool);pb=self.binary_tournament(selection_pool)
                if pa.generation==gen or pb.generation==gen:self._paper_counts["pool_incremental_child_parent_use_count"]+=1
                try:
                    child=self.paper_crossover(pa,pb,gen)
                    child=self.paper_mutation(child,boundary_mutation_rate,route_mutation_rate)
                    child=self.local_search(child,deadline)
                except ObjectiveBudgetExceeded:
                    objective_budget_exhausted=True
                    break
                children.append(child);offspring_index+=1
                if child.fitness<best.fitness-EPS:
                    best=copy.deepcopy(child);best_offspring_index=offspring_index;best_generation=gen;no_improve=0
                else:no_improve+=1
                if no_improve_limit>0 and no_improve>=no_improve_limit:break
                if deadline and time.perf_counter()>=deadline:break
            population=self.update_paper_pool(base_pool+children,mu)
            gen_best.append(min(x.fitness for x in population));history.append(best.fitness)
            if no_improve_limit>0 and no_improve>=no_improve_limit:break
            if objective_budget_exhausted:break
        # Final ranking is required for meaningful output fields. A historical
        # global best may legitimately have left the final resident pool.
        present=self.apply_final_best_ranks(best,population)
        distances=[self.route_hamming_distance(a,b) for i,a in enumerate(population) for b in population[i+1:]]
        self.finalize_operator_metrics()
        stop_reason=("objective_budget" if objective_budget_exhausted else
                     "time_limit" if deadline and time.perf_counter()>=deadline else
                     "no_improve_limit" if no_improve_limit>0 and no_improve>=no_improve_limit else
                     "offspring_limit" if offspring_index>=max_offspring else "generation_limit")
        stats={"history":history,"generation_best_fitness":gen_best,"best_generation":best_generation,
               "best_offspring_index":best_offspring_index,"no_improve_count":no_improve,
               "offspring_generated":offspring_index,"algorithm_time_s":time.perf_counter()-start,
               "route_hamming_mean":statistics.mean(distances) if distances else 0.0,
               "route_hamming_min":min(distances,default=0.0),
               "route_hamming_identity_mode":"full_stable_subweld_id",
               "best_present_in_final_population":present,
               "objective_evaluation_count":self.counts["fitness_evaluation_count"],
               "max_objective_evaluations":self.max_objective_evaluations,
               "objective_budget_exhausted":objective_budget_exhausted,
               "stop_reason":stop_reason,"checkpoint_trace":self.trace.records,
               "final_objective_rank":best.objective_rank,
               "final_diversity_rank":best.diversity_rank,
               "final_biased_fitness":best.biased_fitness,
               "vnd_completed_local_optimum":(
                    self._paper_counts["vnd_call_count"]>0 and
                    self._paper_counts["vnd_completed_local_optimum_count"]==self._paper_counts["vnd_call_count"]),
               "assignment_cache_hits":self.counts["assignment_cache_hit"],
               "assignment_cache_misses":self.counts["assignment_cache_miss"],
               "route_cache_hits":self.counts["route_cache_hit"],
               "route_cache_misses":self.counts["route_cache_miss"],
               **self.counts,**self._paper_counts}
        return best,stats


def collect_metrics(best,stats,metadata,welds,args,solver):
    total_weld_time=sum(float(item.get("total_weld_time",0.0)) for item in best.robots_stats)
    total_travel_time=sum(float(item.get("total_travel_time",0.0)) for item in best.robots_stats)
    metrics={**instance_metrics(metadata,list(welds),args.solver_seed),
        "solver_name":SOLVER_NAME,"paper_aligned_control":True,
        "paper_reference":PAPER_REFERENCE,"paper_mechanism_version":PAPER_VERSION,
        "individual_encoding":"x_up+x_low+four_full_stable_subweld_id_open_routes",
        "mu":args.mu,"lambda":args.lamb,"alpha_neighbors":args.alpha_neighbors,
        "population_update_mode":"incremental_mu_plus_lambda_biased_survival",
        "vnd_mode":args.vnd_mode,"offspring_pool_mode":args.offspring_pool_mode,
        "x_up":best.x_up,"x_low":best.x_low,"fitness":best.fitness,
        "makespan":best.makespan,"load_imbalance":best.load_imbalance,
        "total_idle_distance":best.total_idle_distance,
        "total_weld_time":total_weld_time,"total_travel_time":total_travel_time,
        "weight_makespan":WEIGHTS[0],"weight_load":WEIGHTS[1],"weight_distance":WEIGHTS[2],
        "ref_makespan":solver.refs.makespan,"ref_load":solver.refs.load,"ref_distance":solver.refs.distance,
        "weld_speed":solver.model.weld_speed,"travel_speed":solver.model.travel_speed,
        "acceleration":solver.model.acceleration,"safe_z":solver.model.safe_z,
        "time_model":"corrected","assignment_mode":"split",
        "split_timing_mode":"parent_aware",
        "split_timing_note":"zero_setup_post_equivalent",
        "direction_mode":"bidirectional","direction_decoder":"two_state_dp",
        "route_type":"open","path_mode":"open_no_initial_no_return",
        "objective_rank":best.objective_rank,"diversity_rank":best.diversity_rank,
        "biased_fitness":best.biased_fitness,"biased_fitness_coefficient":args.biased_diversity_coefficient,
        "paper_route_crossover_enabled":args.paper_route_crossover_enabled,
        "paper_vnd_enabled":args.paper_vnd_enabled,"alpha_candidates_enabled":args.alpha_candidates_enabled,
        "biased_fitness_enabled":args.biased_fitness_enabled,
        "project_fallback_enabled":args.project_fallback_enabled,
        "partition_neighborhood_enabled":args.partition_neighborhood_enabled,
        "paper_operator_only_route_search":(
            not args.project_fallback_enabled and args.route_mutation_rate==0.0),
        "boundary_mutation_rate":args.boundary_mutation_rate,
        "route_mutation_rate":args.route_mutation_rate,
        "route_mutation_enabled":args.route_mutation_rate>0.0,
        "config_path":str(Path(args.config_path).resolve()),
        "config_hash":args.config_hash,
        "effective_config":args.effective_config,
        **best.assignment_stats,**stats,
        "robot_orders":best.robot_orders,
        "robot_order_ids":best.robot_order_ids,
        "robot_direction_flags":[list(item.get("direction_flags",[])) for item in best.robots_stats]}
    total=stats["assignment_cache_hit"]+stats["assignment_cache_miss"]
    metrics["assignment_cache_hit_rate"]=stats["assignment_cache_hit"]/total if total else 0.0
    total=stats["route_cache_hit"]+stats["route_cache_miss"]
    metrics["route_cache_hit_rate"]=stats["route_cache_hit"]/total if total else 0.0
    return metrics


def save_outputs(metrics, metrics_json, metrics_csv, result_payload=None):
    if metrics_json:
        path=Path(metrics_json);path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps(result_payload or metrics,ensure_ascii=False,indent=2,sort_keys=True),encoding="utf-8")
    if metrics_csv:
        path=Path(metrics_csv);path.parent.mkdir(parents=True,exist_ok=True)
        row={k:(json.dumps(v,ensure_ascii=False) if isinstance(v,(list,dict)) else v) for k,v in metrics.items()}
        exists=path.exists()
        with path.open("a",newline="",encoding="utf-8-sig") as handle:
            writer=csv.DictWriter(handle,fieldnames=list(row))
            if not exists:writer.writeheader()
            writer.writerow(row)


def build_parser():
    p=argparse.ArgumentParser(description=SOLVER_NAME)
    p.add_argument("--instance-path",required=True);p.add_argument("--solver-seed",type=int,default=42)
    p.add_argument("--config-path",default=DEFAULT_CONFIG_PATH)
    p.add_argument("--mu",type=int);p.add_argument("--lambda",dest="lamb",type=int)
    p.add_argument("--alpha-neighbors",type=int);p.add_argument("--max-generations",type=int)
    p.add_argument("--max-offspring",type=int);p.add_argument("--max-local-search-moves",type=int)
    p.add_argument("--max-objective-evaluations",type=int)
    p.add_argument("--max-neighbor-checks",type=int);p.add_argument("--no-improve-limit",type=int)
    p.add_argument("--time-limit-s",type=float)
    p.add_argument("--boundary-mutation-rate",type=float);p.add_argument("--route-mutation-rate",type=float)
    p.add_argument("--mutation-rate",type=float,help="Deprecated: maps only to boundary mutation.")
    p.add_argument("--vnd-mode",choices=("full","bounded"))
    p.add_argument("--improvement-mode",choices=("first","best"))
    p.add_argument("--offspring-pool-mode",choices=("incremental","batch"))
    p.add_argument("--biased-diversity-coefficient",type=float)
    p.add_argument("--ref-makespan",type=float);p.add_argument("--ref-load",type=float);p.add_argument("--ref-distance",type=float)
    p.add_argument("--normalization-mode",choices=(OFFICIAL_MODE,LEGACY_MODE),default=LEGACY_MODE)
    p.add_argument("--ideal-makespan",type=float);p.add_argument("--ideal-load-imbalance",type=float);p.add_argument("--ideal-distance",type=float)
    p.add_argument("--baseline-makespan",type=float);p.add_argument("--baseline-load-imbalance",type=float);p.add_argument("--baseline-distance",type=float)
    p.add_argument("--scale-makespan",type=float);p.add_argument("--scale-load-imbalance",type=float);p.add_argument("--scale-distance",type=float)
    p.add_argument("--weight-makespan",type=float,default=WEIGHTS[0]);p.add_argument("--weight-load",type=float,default=WEIGHTS[1]);p.add_argument("--weight-distance",type=float,default=WEIGHTS[2])
    p.add_argument("--weld-speed",type=float,default=DEFAULT_MOTION_MODEL.weld_speed)
    p.add_argument("--travel-speed",type=float,default=DEFAULT_MOTION_MODEL.travel_speed)
    p.add_argument("--acceleration",type=float,default=DEFAULT_MOTION_MODEL.acceleration)
    p.add_argument("--safe-z",type=float,default=DEFAULT_MOTION_MODEL.safe_z)
    p.set_defaults(paper_route_crossover_enabled=None,paper_vnd_enabled=None,
                   alpha_candidates_enabled=None,biased_fitness_enabled=None,
                   project_fallback_enabled=None,partition_neighborhood_enabled=None)
    p.add_argument("--disable-paper-route-crossover",dest="paper_route_crossover_enabled",action="store_false")
    p.add_argument("--disable-paper-vnd",dest="paper_vnd_enabled",action="store_false")
    p.add_argument("--disable-alpha-candidates",dest="alpha_candidates_enabled",action="store_false")
    p.add_argument("--disable-biased-fitness",dest="biased_fitness_enabled",action="store_false")
    p.add_argument("--enable-project-fallback",dest="project_fallback_enabled",action="store_true")
    p.add_argument("--disable-project-fallback",dest="project_fallback_enabled",action="store_false")
    p.add_argument("--disable-partition-neighborhood",dest="partition_neighborhood_enabled",action="store_false")
    p.add_argument("--metrics-json");p.add_argument("--metrics-csv");return p


def resolve_args(argv=None):
    """Resolve explicit CLI > JSON config > module defaults."""
    args=build_parser().parse_args(argv)
    path=Path(args.config_path)
    raw=path.read_bytes(); config=json.loads(raw.decode("utf-8"))
    args.config_hash=hashlib.sha256(raw).hexdigest()
    key_map={"lambda":"lamb"}
    for key,default in MODULE_DEFAULTS.items():
        attr=key_map.get(key,key)
        if not hasattr(args,attr):continue
        if getattr(args,attr) is None:
            setattr(args,attr,config.get(key,default))
    bool_map={
        "paper_route_crossover_enabled":"paper_route_crossover_enabled",
        "paper_vnd_enabled":"paper_vnd_enabled",
        "alpha_candidates_enabled":"alpha_candidates_enabled",
        "biased_fitness_enabled":"biased_fitness_enabled",
        "project_fallback_enabled":"project_fallback_enabled",
        "partition_neighborhood_enabled":"partition_neighborhood_enabled",
    }
    for attr,key in bool_map.items():
        if getattr(args,attr) is None:setattr(args,attr,bool(config.get(key,MODULE_DEFAULTS[key])))
    if args.mutation_rate is not None:
        print("Warning: --mutation-rate is deprecated; it maps only to --boundary-mutation-rate. Route mutation remains unchanged.",file=sys.stderr)
        if "--boundary-mutation-rate" not in (argv or sys.argv[1:]):args.boundary_mutation_rate=args.mutation_rate
    for name in ("boundary_mutation_rate","route_mutation_rate"):
        value=float(getattr(args,name))
        if not 0.0<=value<=1.0:raise ValueError(f"{name} must be in [0,1]")
        setattr(args,name,value)
    effective={
        "solver_name":SOLVER_NAME,"mu":args.mu,"lambda":args.lamb,
        "alpha_neighbors":args.alpha_neighbors,"offspring_pool_mode":args.offspring_pool_mode,
        "vnd_mode":args.vnd_mode,"improvement_mode":args.improvement_mode,
        "boundary_mutation_rate":args.boundary_mutation_rate,"route_mutation_rate":args.route_mutation_rate,
        **{k:getattr(args,k) for k in bool_map},
        "biased_diversity_coefficient":args.biased_diversity_coefficient,
        "max_generations":args.max_generations,"max_offspring":args.max_offspring,
        "max_objective_evaluations":args.max_objective_evaluations,
        "max_local_search_moves":args.max_local_search_moves,
        "max_neighbor_checks":args.max_neighbor_checks,"no_improve_limit":args.no_improve_limit,
        "time_limit_s":args.time_limit_s,
    }
    args.effective_config=effective
    return args


def main():
    args=resolve_args();welds,metadata=load_frozen_weld_instance(args.instance_path)
    global WEIGHTS
    WEIGHTS=(float(args.weight_makespan),float(args.weight_load),float(args.weight_distance))
    if any(value<0 for value in WEIGHTS) or not math.isclose(sum(WEIGHTS),1.0,rel_tol=0.0,abs_tol=1e-12):
        raise ValueError("objective weights must be non-negative and sum to 1")
    model=MotionModel(args.weld_speed,args.travel_speed,args.acceleration,args.safe_z);model.validate()
    supplied=(args.ref_makespan,args.ref_load,args.ref_distance)
    official=(args.ideal_makespan,args.ideal_load_imbalance,args.ideal_distance,
              args.baseline_makespan,args.baseline_load_imbalance,args.baseline_distance,
              args.scale_makespan,args.scale_load_imbalance,args.scale_distance)
    normalization=None
    if args.normalization_mode==OFFICIAL_MODE:
        if any(value is not None for value in supplied):raise ValueError("--ref-* cannot be mixed with official normalization")
        if not all(value is not None for value in official):raise ValueError("official normalization requires all ideal, baseline, and scale values")
        normalization={"mode":OFFICIAL_MODE,
            "ideal":dict(zip(("makespan","load_imbalance","idle_distance"),map(float,official[:3]))),
            "baseline":{"algorithm":BASELINE_ALGORITHM,"metrics":dict(zip(("makespan","load_imbalance","idle_distance"),map(float,official[3:6])))},
            "scale":dict(zip(("makespan","load_imbalance","idle_distance"),map(float,official[6:9]))),
            "floors":{},"weights":list(WEIGHTS)}
        validate_normalization_spec(normalization);refs=ObjectiveRefs(1.0,1.0,1.0,normalization)
    else:
        if any(value is not None for value in official):raise ValueError("ideal/baseline/scale arguments require official normalization")
        if any(value is not None for value in supplied) and not all(value is not None for value in supplied):
            raise ValueError("provide all three objective references, or none")
        refs=(ObjectiveRefs(*map(float,supplied)) if all(value is not None for value in supplied) else derive_refs(welds,model))
    s=PaperAlignedHGA(welds,args.solver_seed,alpha_neighbors=args.alpha_neighbors,max_neighbor_checks=args.max_neighbor_checks,
        vnd_mode=args.vnd_mode,improvement_mode=args.improvement_mode,max_local_search_moves=args.max_local_search_moves,
        enable_paper_vnd=args.paper_vnd_enabled,enable_alpha=args.alpha_candidates_enabled,
        enable_biased_fitness=args.biased_fitness_enabled,enable_project_fallback=args.project_fallback_enabled,
        enable_partition=args.partition_neighborhood_enabled,enable_route_crossover=args.paper_route_crossover_enabled,
        biased_diversity_coefficient=args.biased_diversity_coefficient,
        boundary_mutation_rate=args.boundary_mutation_rate,route_mutation_rate=args.route_mutation_rate,
        refs=refs,model=model,max_objective_evaluations=args.max_objective_evaluations)
    best,stats=s.run_paper(args.mu,args.lamb,args.max_generations,args.max_offspring,args.no_improve_limit,
                           args.time_limit_s,args.boundary_mutation_rate,args.route_mutation_rate,args.offspring_pool_mode)
    metrics=collect_metrics(best,stats,metadata,welds,args,s)
    if normalization is not None:
        raw={"makespan":best.makespan,"load_imbalance":best.load_imbalance,"idle_distance":best.total_idle_distance}
        components=normalized_components(raw,normalization)
        metrics.update({"normalization_mode":OFFICIAL_MODE,"normalization_source":"externally_fixed_unified_protocol",
            "normalized_makespan":components["makespan"],"normalized_load_imbalance":components["load_imbalance"],
            "normalized_idle_distance":components["idle_distance"],"fitness":normalized_objective(raw,normalization),
            "normalization":normalization})
    else:metrics["normalization_mode"]=LEGACY_MODE
    robot_direction_flags=[list(item.get("direction_flags",[])) for item in best.robots_stats]
    robot_metrics=build_robot_metrics(best.robot_order_ids,robot_direction_flags,best.robots_stats)
    system_metrics=build_system_metrics(robot_metrics,metrics["fitness"])
    metrics.update(flatten_robot_metrics(robot_metrics))
    metrics.update({"makespan":system_metrics["makespan"],"load_imbalance":system_metrics["load_imbalance"],
        "total_weld_time":system_metrics["total_weld_time"],"total_travel_time":system_metrics["total_travel_time"],
        "total_idle_distance":system_metrics["total_idle_distance"],
        "sum_robot_total_time":system_metrics["sum_robot_total_time"],
        "algorithm_time":stats["algorithm_time_s"]})
    result_payload=build_standard_result(SOLVER_NAME,metadata.get("instance_hash",""),args.solver_seed,
        best.x_up,best.x_low,robot_metrics,system_metrics,stats["algorithm_time_s"])
    result_payload.update({"metrics":metrics,"history":stats.get("history",[]),
        "checkpoint_trace":stats.get("checkpoint_trace",[]),"robot_orders":best.robot_orders,
        "robot_order_ids":best.robot_order_ids,"robot_direction_flags":robot_direction_flags,
        "assignment_stats":best.assignment_stats})
    save_outputs(metrics,args.metrics_json,args.metrics_csv,result_payload)
    print(json.dumps({k:metrics[k] for k in ("solver_name","instance_hash","actual_weld_count","x_up","x_low","fitness","makespan","load_imbalance","total_idle_distance","subweld_count","unassigned_subweld_count","sum_length_error","offspring_generated")},ensure_ascii=False,indent=2))


if __name__=="__main__":main()
