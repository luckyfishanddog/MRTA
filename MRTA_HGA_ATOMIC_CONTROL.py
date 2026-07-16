"""Project-adapted Paper-Aligned-HGA control for fixed atomic weld inputs.

The control preserves HGA mechanisms (route construction/crossover, six local
neighbourhoods, biased quality-diversity population update) while sharing the
new event model, transition table, normalization, budget and RNG contract.
"""

from __future__ import annotations

import argparse
import json
import random
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple

from atomic_problem_core import AtomicInstance, event_pair_routes, load_atomic_instance
from atomic_route_evaluator import AtomicRouteEvaluator, SystemResult, normalization_hash
from MRTA_EPRK_MA import ObjectiveBudgetExceeded, load_normalization


SOLVER_NAME = "Paper-Aligned-HGA-Atomic-Control"
SOLVER_VERSION = "HGA-Atomic-Control-v0.1-development"


@dataclass(frozen=True)
class HGAAtomicConfig:
    population_size: int = 20
    offspring_size: int = 10
    alpha_candidate_size: int = 20
    local_search_candidates: int = 12


@dataclass(frozen=True)
class HGAAtomicIndividual:
    upper_event_index: int
    lower_event_index: int
    routes: Tuple[Tuple[str, ...], ...]
    result: SystemResult

    @property
    def fitness(self) -> float:
        return self.result.fitness

    @property
    def phenotype(self) -> Tuple[Any, ...]:
        return (self.upper_event_index, self.lower_event_index, *self.routes)


class HGAAtomicSolver:
    def __init__(self, instance: AtomicInstance, normalization_spec: Mapping[str, Any], seed: int,
                 primary_budget: int | None = None, time_limit_s: float | None = None,
                 config: HGAAtomicConfig | None = None):
        if normalization_spec.get("atomic_instance_hash") != instance.atomic_instance_hash:
            raise ValueError("normalization and instance hashes differ")
        self.instance = instance; self.normalization_spec = dict(normalization_spec)
        self.seed = int(seed); self.rng = random.Random(self.seed)
        self.primary_budget = primary_budget; self.time_limit_s = time_limit_s
        self.config = config or HGAAtomicConfig(); self.started = time.perf_counter()
        self.evaluator = AtomicRouteEvaluator(instance, normalization_spec)
        self.primary_evaluations = 0; self.phenotype_cache_hits = 0
        self.cache: Dict[Tuple[Any, ...], SystemResult] = {}
        self.best: HGAAtomicIndividual | None = None; self.best_trace: List[Dict[str, Any]] = []
        self.generations = 0; self.local_search_requests = 0; self.crossover_requests = 0

    def _can_evaluate(self) -> bool:
        time_ok = self.time_limit_s is None or time.perf_counter() - self.started < self.time_limit_s
        budget_ok = self.primary_budget is None or self.primary_evaluations < self.primary_budget
        return time_ok and budget_ok

    def evaluate(self, up: int, low: int, routes: Sequence[Sequence[str]]) -> HGAAtomicIndividual:
        if not self._can_evaluate(): raise ObjectiveBudgetExceeded
        route_tuple = tuple(tuple(r) for r in routes); key = (up, low, *route_tuple)
        self.primary_evaluations += 1
        if key in self.cache:
            self.phenotype_cache_hits += 1; result = self.cache[key]
        else:
            expected = event_pair_routes(self.instance.upper_events[up], self.instance.lower_events[low])
            if any(set(route_tuple[r]) != set(expected[r]) for r in range(4)):
                raise ValueError("HGA routes do not match selected atomic boundary events")
            result = self.evaluator.evaluate_system(route_tuple); self.cache[key] = result
        individual = HGAAtomicIndividual(up, low, route_tuple, result)
        if self.best is None or individual.fitness < self.best.fitness - 1e-15:
            self.best = individual; self.best_trace.append({"primary_evaluations": self.primary_evaluations,
                                                            "fitness": individual.fitness,
                                                            "elapsed_s": time.perf_counter() - self.started})
        return individual

    def _construct_route(self, ids: Sequence[str]) -> List[str]:
        remaining = list(ids); self.rng.shuffle(remaining); route: List[str] = []
        while remaining:
            if not route:
                route.append(remaining.pop()); continue
            previous = self.instance.by_id[route[-1]]
            sample = remaining if len(remaining) <= self.config.alpha_candidate_size else self.rng.sample(remaining, self.config.alpha_candidate_size)
            chosen = min(sample, key=lambda wid: (min(__import__("math").dist(a, b) for a in (previous.start, previous.end)
                                                     for b in (self.instance.by_id[wid].start, self.instance.by_id[wid].end)), wid))
            remaining.remove(chosen); route.append(chosen)
        return route

    def random_individual(self) -> HGAAtomicIndividual:
        up = self.rng.randrange(len(self.instance.upper_events)); low = self.rng.randrange(len(self.instance.lower_events))
        return self.evaluate(up, low, [self._construct_route(ids) for ids in event_pair_routes(self.instance.upper_events[up], self.instance.lower_events[low])])

    def _repair_order(self, required: Sequence[str], primary: Sequence[str], secondary: Sequence[str]) -> List[str]:
        required_set = set(required); result: List[str] = []
        for wid in (*primary, *secondary):
            if wid in required_set and wid not in result: result.append(wid)
        missing = [wid for wid in required if wid not in result]; self.rng.shuffle(missing); result.extend(missing)
        return result

    def crossover(self, a: HGAAtomicIndividual, b: HGAAtomicIndividual) -> HGAAtomicIndividual:
        self.crossover_requests += 1
        up = self.rng.choice((a.upper_event_index, b.upper_event_index))
        low = self.rng.choice((a.lower_event_index, b.lower_event_index))
        required = event_pair_routes(self.instance.upper_events[up], self.instance.lower_events[low])
        donor_robot = self.rng.randrange(4)
        routes = []
        for robot in range(4):
            first, second = ((b.routes[robot], a.routes[robot]) if robot == donor_robot else (a.routes[robot], b.routes[robot]))
            routes.append(self._repair_order(required[robot], first, second))
        return self.evaluate(up, low, routes)

    def _neighbours(self, individual: HGAAtomicIndividual) -> Iterable[Tuple[int, int, List[List[str]]]]:
        routes = [list(r) for r in individual.routes]
        critical = max(range(4), key=lambda r: individual.result.robot_total_times[r])
        r = routes[critical]; n = len(r); produced = 0; cap = self.config.local_search_candidates
        # M1 relocate from longest route; M2 swap across routes; M3 double swap;
        # M4/M5/M6 within/cross-route 2-opt variants.
        for i in range(n):
            if n > 1:
                for j in range(n):
                    if i == j: continue
                    candidate = [x[:] for x in routes]; item = candidate[critical].pop(i); candidate[critical].insert(j, item)
                    yield individual.upper_event_index, individual.lower_event_index, candidate; produced += 1
                    if produced >= cap: return
                j = (i + 1) % n
                candidate = [x[:] for x in routes]; candidate[critical][i], candidate[critical][j] = candidate[critical][j], candidate[critical][i]
                yield individual.upper_event_index, individual.lower_event_index, candidate; produced += 1
                if produced >= cap: return
            for other in range(4):
                if other == critical or not routes[other]: continue
                # Only exchange tasks if assignment remains legal; boundary event mutation is separate.
                continue
        for i in range(max(0, n - 1)):
            for j in range(i + 1, min(n, i + 1 + self.config.alpha_candidate_size)):
                candidate = [x[:] for x in routes]; candidate[critical][i:j + 1] = reversed(candidate[critical][i:j + 1])
                yield individual.upper_event_index, individual.lower_event_index, candidate; produced += 1
                if produced >= cap: return
        # Region-division neighbour; repair routes according to new assignment.
        for boundary, old, count in ((0, individual.upper_event_index, len(self.instance.upper_events)),
                                     (1, individual.lower_event_index, len(self.instance.lower_events))):
            for delta in (-1, 1):
                new = old + delta
                if not 0 <= new < count: continue
                up = new if boundary == 0 else individual.upper_event_index
                low = new if boundary == 1 else individual.lower_event_index
                required = event_pair_routes(self.instance.upper_events[up], self.instance.lower_events[low])
                all_order = [wid for route in individual.routes for wid in route]
                candidate = [self._repair_order(ids, all_order, ()) for ids in required]
                yield up, low, candidate; produced += 1
                if produced >= cap: return

    def local_search(self, start: HGAAtomicIndividual) -> HGAAtomicIndividual:
        current = start
        improved = True
        while improved and self._can_evaluate():
            improved = False
            for up, low, routes in self._neighbours(current):
                if not self._can_evaluate(): break
                self.local_search_requests += 1; candidate = self.evaluate(up, low, routes)
                if candidate.fitness < current.fitness - 1e-15:
                    current = candidate; improved = True; break
        return current

    @staticmethod
    def _distance(a: HGAAtomicIndividual, b: HGAAtomicIndividual) -> float:
        edges_a = {(route[i], route[i + 1]) for route in a.routes for i in range(len(route) - 1)}
        edges_b = {(route[i], route[i + 1]) for route in b.routes for i in range(len(route) - 1)}
        edge_distance = 1.0 - len(edges_a & edges_b) / max(1, len(edges_a | edges_b))
        boundary_distance = (a.upper_event_index != b.upper_event_index) + (a.lower_event_index != b.lower_event_index)
        return edge_distance + 0.5 * boundary_distance

    def _population_update(self, pool: List[HGAAtomicIndividual]) -> List[HGAAtomicIndividual]:
        unique = {x.phenotype: x for x in sorted(pool, key=lambda i: i.fitness)}
        values = list(unique.values())
        quality = {x.phenotype: rank / max(1, len(values) - 1) for rank, x in enumerate(sorted(values, key=lambda i: i.fitness))}
        scored = []
        for x in values:
            distances = sorted(self._distance(x, y) for y in values if y.phenotype != x.phenotype)
            diversity = sum(distances[:3]) / max(1, min(3, len(distances))) if distances else 0.0
            scored.append((quality[x.phenotype] - 0.15 * diversity, x.fitness, x))
        return [item[2] for item in sorted(scored, key=lambda item: item[:2])[:self.config.population_size]]

    def _run_impl(self) -> HGAAtomicIndividual:
        population: List[HGAAtomicIndividual] = []
        while len(population) < self.config.population_size and self._can_evaluate(): population.append(self.random_individual())
        if self.best is None: raise RuntimeError("no objective evaluation was possible")
        while self._can_evaluate():
            self.generations += 1; offspring = []
            for _ in range(self.config.offspring_size):
                if not self._can_evaluate(): break
                a = min(self.rng.sample(population, min(2, len(population))), key=lambda x: x.fitness)
                b = min(self.rng.sample(population, min(2, len(population))), key=lambda x: x.fitness)
                child = self.crossover(a, b)
                if self._can_evaluate(): child = self.local_search(child)
                offspring.append(child)
            population = self._population_update(population + offspring)
        return self.best

    def run(self) -> HGAAtomicIndividual:
        try:
            return self._run_impl()
        except ObjectiveBudgetExceeded:
            if self.best is None:
                raise
            return self.best

    def metrics(self, best: HGAAtomicIndividual) -> Dict[str, Any]:
        elapsed = time.perf_counter() - self.started; counters = self.evaluator.counters()
        return {"solver": SOLVER_NAME, "solver_version": SOLVER_VERSION, "seed": self.seed, "status": "success",
                "stop_reason": "objective_budget" if self.primary_budget is not None and self.primary_evaluations >= self.primary_budget else "time_limit",
                "requested_primary_budget": self.primary_budget, "primary_evaluations": self.primary_evaluations,
                "algorithm_time_s": elapsed, "atomic_instance_hash": self.instance.atomic_instance_hash,
                "normalization_hash": normalization_hash(self.normalization_spec),
                "upper_event_index": best.upper_event_index, "lower_event_index": best.lower_event_index,
                "upper_boundary_x": self.instance.upper_events[best.upper_event_index].representative_x,
                "lower_boundary_x": self.instance.lower_events[best.lower_event_index].representative_x,
                **best.result.to_dict(), **counters, "phenotype_cache_hits": self.phenotype_cache_hits,
                "route_evaluations_per_primary": counters["route_evaluation_requests"] / max(1, self.primary_evaluations),
                "direction_dp_per_primary": counters["direction_dp_calls"] / max(1, self.primary_evaluations),
                "generations": self.generations, "crossover_primary_requests": self.crossover_requests,
                "local_search_primary_requests": self.local_search_requests, "best_trace": self.best_trace}


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(); parser.add_argument("--instance", required=True); parser.add_argument("--instance-name", required=True)
    parser.add_argument("--normalization", default="atomic_normalization_spec.json"); parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--primary-budget", type=int); parser.add_argument("--time-limit", type=float); parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    if args.primary_budget is None and args.time_limit is None: parser.error("one stopping condition is required")
    instance = load_atomic_instance(args.instance); spec = load_normalization(args.normalization, args.instance_name)
    solver = HGAAtomicSolver(instance, spec, args.seed, args.primary_budget, args.time_limit); best = solver.run(); metrics = solver.metrics(best)
    output = Path(args.output); output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(metrics, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({key: metrics[key] for key in ("status", "fitness", "primary_evaluations", "algorithm_time_s")}, ensure_ascii=False))


if __name__ == "__main__": main()
