from continuous_lineage_decoder import CPLRChromosome, decode_chromosome
from generate_welds import Weld
from lineage_presplit import build_lineages
from reachable_side_keys import apply_reachable_side_index, build_reachable_side_index


def test_repeated_decode_is_fieldwise_identical_and_ties_use_side_id():
    welds = [
        Weld("b", 1, 8, .1, 19, 8, .1),
        Weld("a", 2, 9, .1, 18, 9, .1),
    ]
    built = build_lineages(welds)
    index = build_reachable_side_index(built.lineages)
    lineages = apply_reachable_side_index(built.lineages, index)
    chromosome = CPLRChromosome.from_mapping(.5, .5, {key: .5 for key in index.keys})
    first = decode_chromosome(lineages, chromosome)
    second = decode_chromosome(lineages, chromosome)
    assert first == second
    for route in first.route_signatures:
        assert route == tuple(sorted(route))
