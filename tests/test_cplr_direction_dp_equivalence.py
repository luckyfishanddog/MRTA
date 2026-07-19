import itertools
import math

from continuous_lineage_decoder import CPLRChromosome, decode_chromosome
from generate_welds import Weld
from lineage_presplit import build_lineages
from mrta_problem_core import DirectedWeld, travel_time, weld_time
from reachable_side_keys import apply_reachable_side_index, build_reachable_side_index


def test_cplr_direction_dp_matches_exhaustive_orientation_enumeration():
    welds = [
        Weld("a", 1, 8, .1, 4, 9, .1),
        Weld("b", 5, 8, .1, 8, 10, .1),
        Weld("c", 9, 11, .1, 12, 8, .1),
    ]
    built = build_lineages(welds)
    index = build_reachable_side_index(built.lineages)
    lineages = apply_reachable_side_index(built.lineages, index)
    chromosome = CPLRChromosome.from_mapping(1.0, .5, {key: key.lineage_id / 10 for key in index.keys})
    state = decode_chromosome(lineages, chromosome)
    route_tasks = [task for task in state.active_tasks if task.robot_id == 0]
    key_map = chromosome.key_mapping()
    route_tasks.sort(key=lambda task: (key_map[task.lineage_side_id], task.lineage_side_id))
    route = [task.to_weld() for task in route_tasks]
    totals = []
    for flags in itertools.product((False, True), repeat=len(route)):
        directed = [DirectedWeld(task, flag) for task, flag in zip(route, flags)]
        total = sum(weld_time(task) for task in route)
        total += sum(
            travel_time(left.end_point(), right.start_point())
            for left, right in zip(directed[:-1], directed[1:])
        )
        totals.append(total)
    assert math.isclose(state.route_statistics[0].total_time, min(totals), abs_tol=1e-10)
