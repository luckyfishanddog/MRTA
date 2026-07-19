"""Event-driven Partition-Routing Random-Key Memetic Algorithm.

Development candidate only. It never mutates or splits weld geometry during
search and is deliberately separate from the frozen formal solver set.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple

from atomic_problem_core import AtomicInstance, event_pair_routes, load_atomic_instance
from atomic_route_evaluator import AtomicRouteEvaluator, SystemResult, normalization_hash


SOLVER_NAME = "EPRK-MA"
SOLVER_VERSION = "EPRK-MA-v0.1-development"


@dataclass(frozen=True)
class EPRKConfig:
    population_size: int = 60
    elite_fraction: float = 0.20
    mutant_fraction: float = 0.15
    inheritance_bias: float = 0.70
    population_count: int = 2
    migration_interval: int = 40
    migration_elites: int = 2
    restart_stagnation: int = 100
    local_search_elite_fraction: float = 0.10
    candidate_list_size: int = 10
    max_local_improvements: int = 30
    enable_event_local_search: bool = True
    enable_route_local_search: bool = True
    enable_zero_continuity_initialization: bool = True
    enable_heuristic_initialization: bool = True
    enable_migration: bool = True
    enable_restart: bool = True

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "EPRKConfig":
        names = cls.__dataclass_fields__
        return cls(**{key: value[key] for key in names if key in value})

    def validate(self) -> None:
        if self.population_size < 4 or self.population_count < 1:
            raise ValueError("invalid population configuration")
        if not 0 < self.elite_fraction < 1 or not 0 <= self.mutant_fraction < 1:
            raise ValueError("invalid elite/mutant fractions")
        if not 0.5 <= self.inheritance_bias <= 1.0:
            raise ValueError("inheritance_bias must be in [0.5,1]")


@dataclass(frozen=True)
class EPRKIndividual:
    genes: Tuple[float, ...]
    upper_event_index: int
    lower_event_index: int
    routes: Tuple[Tuple[str, ...], ...]
    phenotype_key: Tuple[Any, ...]
    result: SystemResult

    @property
    def fitness(self) -> float:
        return self.result.fitness


class ObjectiveBudgetExceeded(RuntimeError):
    pass


class EPRKMASolver:
    def __init__(self, instance: AtomicInstance, normalization_spec: Mapping[str, Any], seed: int,
                 config: EPRKConfig | None = None, primary_budget: int | None = None,
                 time_limit_s: float | None = None):
        self.instance = instance
        self.normalization_spec = dict(normalization_spec)
        if self.normalization_spec.get("atomic_instance_hash") != instance.atomic_instance_hash:
            raise ValueError("normalization and atomic instance hashes differ")
        self.config = config or EPRKConfig()
        self.config.validate()
        self.rng = random.Random(int(seed))
        self.seed = int(seed)
        self.primary_budget = primary_budget
        self.time_limit_s = time_limit_s
        self.started = time.perf_counter()
        self.evaluator = AtomicRouteEvaluator(instance, normalization_spec)
        self.primary_evaluations = 0
        self.phenotype_cache_hits = 0
        self.assignment_cache: Dict[Tuple[int, int], Tuple[Tuple[str, ...], ...]] = {}
        self.phenotype_cache: Dict[Tuple[Any, ...], SystemResult] = {}
        self.id_position = {wid: index for index, wid in enumerate(self.evaluator.ids)}
        self.global_best: EPRKIndividual | None = None
        self.best_trace: List[Dict[str, Any]] = []
        self.generations = 0
        self.local_search_requests = 0
        self.migrations = 0
        self.restarts = 0

    def _time_exhausted(self) -> bool:
        return self.time_limit_s is not None and time.perf_counter() - self.started >= self.time_limit_s

    def _can_evaluate(self) -> bool:
        return (self.primary_budget is None or self.primary_evaluations < self.primary_budget) and not self._time_exhausted()

    @staticmethod
    def _event_index(key: float, count: int) -> int:
        return min(count - 1, max(0, math.floor(min(max(float(key), 0.0), math.nextafter(1.0, 0.0)) * count)))

    def decode(self, genes: Sequence[float]) -> Tuple[int, int, Tuple[Tuple[str, ...], ...], Tuple[Any, ...]]:
        if len(genes) != len(self.evaluator.ids) + 2:
            raise ValueError("chromosome length must be N+2")
        up = self._event_index(genes[0], len(self.instance.upper_events))
        low = self._event_index(genes[1], len(self.instance.lower_events))
        pair = (up, low)
        if pair not in self.assignment_cache:
            self.assignment_cache[pair] = event_pair_routes(self.instance.upper_events[up], self.instance.lower_events[low])
        assigned = self.assignment_cache[pair]
        routes = tuple(tuple(sorted(ids, key=lambda wid: (float(genes[2 + self.id_position[wid]]), wid))) for ids in assigned)
        phenotype = (up, low, *routes)
        return up, low, routes, phenotype

    def evaluate(self, genes: Sequence[float]) -> EPRKIndividual:
        if not self._can_evaluate():
            raise ObjectiveBudgetExceeded
        self.primary_evaluations += 1
        clipped = tuple(min(math.nextafter(1.0, 0.0), max(0.0, float(x))) for x in genes)
        up, low, routes, phenotype = self.decode(clipped)
        if phenotype in self.phenotype_cache:
            self.phenotype_cache_hits += 1
            result = self.phenotype_cache[phenotype]
        else:
            result = self.evaluator.evaluate_system(routes)
            self.phenotype_cache[phenotype] = result
        individual = EPRKIndividual(clipped, up, low, routes, phenotype, result)
        if self.global_best is None or individual.fitness < self.global_best.fitness - 1e-15:
            self.global_best = individual
            self.best_trace.append({"primary_evaluations": self.primary_evaluations, "fitness": individual.fitness,
                                    "elapsed_s": time.perf_counter() - self.started})
        return individual

    def _random_genes(self) -> List[float]:
        return [self.rng.random() for _ in range(len(self.evaluator.ids) + 2)]

    def _event_key(self, index: int, count: int) -> float:
        return (index + 0.5) / count

    def _load_balanced_genes(self) -> List[float]:
        genes = self._random_genes()
        by_id = self.instance.by_id
        for offset, events in enumerate((self.instance.upper_events, self.instance.lower_events)):
            index = min(range(len(events)), key=lambda i: (abs(sum(by_id[x].weld_time_s for x in events[i].left_atomic_ids) -
                                                               sum(by_id[x].weld_time_s for x in events[i].right_atomic_ids)), i))
            genes[offset] = self._event_key(index, len(events))
        return genes

    def _heuristic_order_genes(self, zero_first: bool, farthest: bool = False) -> List[float]:
        genes = self._load_balanced_genes()
        up, low, assigned, _ = self.decode(genes)
        by_id = self.instance.by_id
        for ids in assigned:
            remaining = set(ids); order: List[str] = []
            if remaining:
                current = min(remaining); remaining.remove(current); order.append(current)
            while remaining:
                last = by_id[order[-1]]
                def metric(wid: str):
                    target = by_id[wid]
                    shared = min(math.dist(a, b) for a in (last.start, last.end) for b in (target.start, target.end))
                    same_parent = target.parent_weld_id == last.parent_weld_id
                    if zero_first:
                        return (0 if shared <= 1e-9 or same_parent else 1, shared, wid)
                    return ((-shared if farthest else shared), wid)
                chosen = min(remaining, key=metric); remaining.remove(chosen); order.append(chosen)
            self._encode_route(genes, order)
        return genes

    def _encode_route(self, genes: List[float], route: Sequence[str]) -> None:
        for rank, wid in enumerate(route):
            genes[2 + self.id_position[wid]] = (rank + 1) / (len(route) + 1)

    def _initial_genes(self, index: int) -> List[float]:
        if not self.config.enable_heuristic_initialization:
            return self._random_genes()
        fraction = (index % 20) / 20.0
        if fraction < 0.50:
            return self._random_genes()
        if fraction < 0.70:
            return self._load_balanced_genes()
        if fraction < 0.85:
            return self._heuristic_order_genes(True) if self.config.enable_zero_continuity_initialization else self._load_balanced_genes()
        return self._heuristic_order_genes(False, farthest=(index % 2 == 0))

    def _mutant(self, parent: EPRKIndividual | None = None) -> List[float]:
        if parent is None:
            return self._random_genes()
        genes = list(parent.genes)
        mode = self.rng.randrange(4)
        if mode == 0:  # reset
            for index in self.rng.sample(range(len(genes)), k=max(1, len(genes) // 12)):
                genes[index] = self.rng.random()
        elif mode == 1:  # Gaussian
            for index in self.rng.sample(range(len(genes)), k=max(1, len(genes) // 10)):
                genes[index] = (genes[index] + self.rng.gauss(0.0, 0.08)) % 1.0
        elif mode == 2:  # boundary jump
            boundary = self.rng.randrange(2)
            count = len(self.instance.upper_events) if boundary == 0 else len(self.instance.lower_events)
            old = self._event_index(genes[boundary], count)
            new = min(count - 1, max(0, old + self.rng.choice([-2, -1, 1, 2])))
            genes[boundary] = self._event_key(new, count)
        else:  # contiguous random-key block perturbation
            if len(genes) > 3:
                left = self.rng.randrange(2, len(genes)); right = min(len(genes), left + self.rng.randint(2, 6))
                block = genes[left:right]; self.rng.shuffle(block); genes[left:right] = block
        return genes

    def _crossover(self, elite: EPRKIndividual, other: EPRKIndividual) -> List[float]:
        return [a if self.rng.random() < self.config.inheritance_bias else b for a, b in zip(elite.genes, other.genes)]

    def _route_neighbors(self, individual: EPRKIndividual) -> Iterable[List[float]]:
        limit = self.config.candidate_list_size
        yielded = 0
        critical = max(range(4), key=lambda r: individual.result.robot_total_times[r])
        route = list(individual.routes[critical])
        n = len(route)
        for i in range(n):
            for j in range(i + 1, min(n, i + 1 + limit)):
                candidates = []
                swapped = route[:]; swapped[i], swapped[j] = swapped[j], swapped[i]; candidates.append(swapped)
                candidates.append(route[:i] + list(reversed(route[i:j + 1])) + route[j + 1:])
                moved = route[:]; item = moved.pop(i); moved.insert(j, item); candidates.append(moved)
                for candidate in candidates:
                    genes = list(individual.genes); self._encode_route(genes, candidate)
                    yielded += 1; yield genes
                    if yielded >= limit:
                        return
        # explicit same-parent / zero-continuity chain move
        for i, wid in enumerate(route):
            adjacent = set(self.evaluator.same_parent_adjacent.get(wid, ()))
            if i + 1 < n and route[i + 1] not in adjacent:
                match = next((j for j in range(i + 2, n) if route[j] in adjacent), None)
                if match is not None:
                    candidate = route[:]; item = candidate.pop(match); candidate.insert(i + 1, item)
                    genes = list(individual.genes); self._encode_route(genes, candidate); yield genes
                    return

    def local_search(self, start: EPRKIndividual) -> EPRKIndividual:
        current = start
        improvements = 0
        while improvements < self.config.max_local_improvements and self._can_evaluate():
            accepted = False
            event_moves = ((0, len(self.instance.upper_events), current.upper_event_index),
                           (1, len(self.instance.lower_events), current.lower_event_index)) if self.config.enable_event_local_search else ()
            for boundary, count, old in event_moves:
                for delta in (-1, 1, -2, 2):
                    new = old + delta
                    if not 0 <= new < count or not self._can_evaluate():
                        continue
                    genes = list(current.genes); genes[boundary] = self._event_key(new, count)
                    self.local_search_requests += 1; candidate = self.evaluate(genes)
                    if candidate.fitness < current.fitness - 1e-15:
                        current = candidate; improvements += 1; accepted = True; break
                if accepted: break
            if accepted: continue
            route_moves = self._route_neighbors(current) if self.config.enable_route_local_search else ()
            for genes in route_moves:
                if not self._can_evaluate(): break
                self.local_search_requests += 1; candidate = self.evaluate(genes)
                if candidate.fitness < current.fitness - 1e-15:
                    current = candidate; improvements += 1; accepted = True; break
            if not accepted:
                break
        return current

    def _run_impl(self) -> EPRKIndividual:
        populations: List[List[EPRKIndividual]] = [[] for _ in range(self.config.population_count)]
        for p in range(self.config.population_count):
            for i in range(self.config.population_size):
                if not self._can_evaluate(): break
                populations[p].append(self.evaluate(self._initial_genes(i + p * self.config.population_size)))
        if self.global_best is None:
            raise RuntimeError("no objective evaluation was possible")
        stagnation = 0
        previous_best = self.global_best.fitness
        while self._can_evaluate():
            self.generations += 1
            for p, population in enumerate(populations):
                if not population or not self._can_evaluate(): continue
                population.sort(key=lambda x: (x.fitness, x.phenotype_key))
                elite_n = max(1, round(self.config.elite_fraction * len(population)))
                mutant_n = max(1, round(self.config.mutant_fraction * len(population)))
                elites = population[:elite_n]
                next_pop = elites[:]
                while len(next_pop) < self.config.population_size - mutant_n and self._can_evaluate():
                    elite = self.rng.choice(elites); other = self.rng.choice(population[elite_n:] or population)
                    next_pop.append(self.evaluate(self._crossover(elite, other)))
                while len(next_pop) < self.config.population_size and self._can_evaluate():
                    next_pop.append(self.evaluate(self._mutant(self.rng.choice(elites))))
                next_pop.sort(key=lambda x: (x.fitness, x.phenotype_key))
                ls_n = max(1, round(self.config.local_search_elite_fraction * len(next_pop)))
                # Lightweight LS: current global best and top elite only.
                for i in range(min(ls_n, len(next_pop))):
                    if not self._can_evaluate(): break
                    next_pop[i] = self.local_search(next_pop[i])
                populations[p] = sorted(next_pop, key=lambda x: (x.fitness, x.phenotype_key))[:self.config.population_size]
            if self.global_best.fitness < previous_best - 1e-15:
                previous_best = self.global_best.fitness; stagnation = 0
            else:
                stagnation += 1
            if self.config.enable_migration and self.generations % self.config.migration_interval == 0 and len(populations) > 1:
                migrants = [pop[:self.config.migration_elites] for pop in populations]
                for p in range(len(populations)):
                    populations[(p + 1) % len(populations)][-len(migrants[p]):] = migrants[p]
                self.migrations += 1
            if self.config.enable_restart and stagnation >= self.config.restart_stagnation:
                for p, population in enumerate(populations):
                    keep = population[:max(1, round(self.config.elite_fraction * len(population)))]
                    while len(keep) < self.config.population_size and self._can_evaluate():
                        keep.append(self.evaluate(self._random_genes()))
                    populations[p] = keep
                stagnation = 0; self.restarts += 1
        return self.global_best

    def run(self) -> EPRKIndividual:
        """Run until the exact budget or time boundary; boundary races stop cleanly."""
        try:
            return self._run_impl()
        except ObjectiveBudgetExceeded:
            if self.global_best is None:
                raise
            return self.global_best

    def metrics(self, best: EPRKIndividual) -> Dict[str, Any]:
        elapsed = time.perf_counter() - self.started
        counters = self.evaluator.counters()
        return {
            "solver": SOLVER_NAME, "solver_version": SOLVER_VERSION, "seed": self.seed,
            "status": "success", "stop_reason": "objective_budget" if self.primary_budget is not None and self.primary_evaluations >= self.primary_budget else "time_limit",
            "requested_primary_budget": self.primary_budget, "primary_evaluations": self.primary_evaluations,
            "algorithm_time_s": elapsed, "atomic_instance_hash": self.instance.atomic_instance_hash,
            "normalization_hash": normalization_hash(self.normalization_spec),
            "upper_event_index": best.upper_event_index, "lower_event_index": best.lower_event_index,
            "upper_boundary_x": self.instance.upper_events[best.upper_event_index].representative_x,
            "lower_boundary_x": self.instance.lower_events[best.lower_event_index].representative_x,
            **best.result.to_dict(), **counters,
            "phenotype_cache_hits": self.phenotype_cache_hits, "phenotype_cache_size": len(self.phenotype_cache),
            "assignment_cache_size": len(self.assignment_cache), "generations": self.generations,
            "local_search_primary_requests": self.local_search_requests, "migrations": self.migrations, "restarts": self.restarts,
            "route_evaluations_per_primary": counters["route_evaluation_requests"] / max(1, self.primary_evaluations),
            "direction_dp_per_primary": counters["direction_dp_calls"] / max(1, self.primary_evaluations),
            "best_trace": self.best_trace,
        }


def load_normalization(path: str | Path, instance_name: str) -> Mapping[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    return value["instances"][instance_name] if "instances" in value else value


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--instance", required=True)
    parser.add_argument("--instance-name", required=True)
    parser.add_argument("--normalization", default="config/normalization/atomic_normalization_spec.json")
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--primary-budget", type=int)
    parser.add_argument("--time-limit", type=float)
    parser.add_argument("--config", default="config/profiles/eprk_ma_defaults.json")
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    if args.primary_budget is None and args.time_limit is None:
        parser.error("one stopping condition is required")
    instance = load_atomic_instance(args.instance)
    normalization = load_normalization(args.normalization, args.instance_name)
    config = EPRKConfig.from_mapping(json.loads(Path(args.config).read_text(encoding="utf-8")))
    solver = EPRKMASolver(instance, normalization, args.seed, config, args.primary_budget, args.time_limit)
    best = solver.run(); metrics = solver.metrics(best)
    output = Path(args.output); output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(metrics, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({key: metrics[key] for key in ("status", "fitness", "primary_evaluations", "algorithm_time_s")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
