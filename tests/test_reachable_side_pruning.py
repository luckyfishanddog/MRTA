import random

from continuous_lineage_decoder import CPLRChromosome, CPLRDecoderConfig, LineageSideId, decode_chromosome
from generate_welds import Weld
from lineage_presplit import build_lineages
from reachable_side_keys import apply_reachable_side_index, build_reachable_side_index


def test_unreachable_left_side_is_pruned_for_limited_domain():
    welds = [Weld("far-right", 15, 8, .1, 18, 8, .1)]
    config = CPLRDecoderConfig(upper_boundary_min=0, upper_boundary_max=5)
    built = build_lineages(welds)
    index = build_reachable_side_index(built.lineages, config)
    assert index.keys == (LineageSideId(0, "R"),)
    assert index.pruned_key_count == 1


def test_pruned_and_unpruned_decoders_have_identical_active_routes():
    welds = [
        Weld("far-right", 15, 8, .1, 18, 8, .1),
        Weld("cross", 1, 8, .1, 4, 8, .1),
    ]
    config = CPLRDecoderConfig(upper_boundary_min=0, upper_boundary_max=5)
    built = build_lineages(welds)
    index = build_reachable_side_index(built.lineages, config)
    pruned = apply_reachable_side_index(built.lineages, index)
    unpruned_keys = {
        LineageSideId(item.lineage_id, side): 0.25 + 0.5 * (side == "R")
        for item in built.lineages for side in ("L", "R")
    }
    pruned_keys = {key: unpruned_keys[key] for key in index.keys}
    rng = random.Random(91)
    for _ in range(50):
        gene = rng.random()
        full_state = decode_chromosome(
            built.lineages, CPLRChromosome.from_mapping(gene, .5, unpruned_keys), config
        )
        pruned_state = decode_chromosome(
            pruned, CPLRChromosome.from_mapping(gene, .5, pruned_keys), config
        )
        assert full_state.route_signatures == pruned_state.route_signatures
        assert full_state.route_statistics == pruned_state.route_statistics
