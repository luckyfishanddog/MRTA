import random

from continuous_lineage_decoder import CPLRChromosome, decode_chromosome
from generate_welds import Weld
from lineage_presplit import build_lineages
from reachable_side_keys import apply_reachable_side_index, build_reachable_side_index


def test_random_candidates_have_unique_lineage_side_ownership():
    welds = [
        Weld(str(i), i, 8 if i % 2 else 4, .1, 19 - i, 9 if i % 2 else 3, .1)
        for i in range(8)
    ]
    built = build_lineages(welds)
    index = build_reachable_side_index(built.lineages)
    lineages = apply_reachable_side_index(built.lineages, index)
    rng = random.Random(20260719)
    for _ in range(100):
        chromosome = CPLRChromosome.from_mapping(
            rng.random(), rng.random(), {key: rng.random() for key in index.keys}
        )
        state = decode_chromosome(lineages, chromosome)
        keys = [task.lineage_side_id for task in state.active_tasks]
        assert len(keys) == len(set(keys))
        assert state.structural_validation.unique_assignment
        for robot_id in range(4):
            robot_lineages = [
                task.lineage_side_id.lineage_id
                for task in state.active_tasks
                if task.robot_id == robot_id
            ]
            assert len(robot_lineages) == len(set(robot_lineages))
