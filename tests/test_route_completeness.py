from continuous_lineage_decoder import CPLRChromosome, LineageSideId, decode_chromosome
from generate_welds import Weld
from lineage_presplit import build_lineages
from reachable_side_keys import apply_reachable_side_index, build_reachable_side_index


def _two_crossing_lineages():
    welds = [
        Weld("i", 1, 8, .1, 19, 8, .1),
        Weld("j", 2, 9, .1, 18, 9, .1),
    ]
    built = build_lineages(welds)
    index = build_reachable_side_index(built.lineages)
    return apply_reachable_side_index(built.lineages, index), index


def test_two_side_keys_express_opposite_left_and_right_orders():
    lineages, index = _two_crossing_lineages()
    keys = {key: .5 for key in index.keys}
    keys[LineageSideId(0, "L")] = .25
    keys[LineageSideId(1, "L")] = .75
    keys[LineageSideId(0, "R")] = .75
    keys[LineageSideId(1, "R")] = .25
    state = decode_chromosome(lineages, CPLRChromosome.from_mapping(.5, .5, keys))
    assert state.route_signatures[0] == (LineageSideId(0, "L"), LineageSideId(1, "L"))
    assert state.route_signatures[1] == (LineageSideId(1, "R"), LineageSideId(0, "R"))


def test_single_parent_key_cannot_express_opposite_orders_counterexample():
    # A single key q_i/q_j imposes the same strict comparison on both sides.
    for qi, qj in ((.25, .75), (.75, .25)):
        left = sorted(("i", "j"), key={"i": qi, "j": qj}.__getitem__)
        right = sorted(("i", "j"), key={"i": qi, "j": qj}.__getitem__)
        assert left == right
        assert not (left == ["i", "j"] and right == ["j", "i"])


def test_constructive_keys_reproduce_any_small_route_permutation():
    lineages, index = _two_crossing_lineages()
    targets = {
        0: (LineageSideId(1, "L"), LineageSideId(0, "L")),
        1: (LineageSideId(0, "R"), LineageSideId(1, "R")),
    }
    keys = {key: .5 for key in index.keys}
    for route in targets.values():
        for position, key in enumerate(route, 1):
            keys[key] = position / (len(route) + 1)
    state = decode_chromosome(lineages, CPLRChromosome.from_mapping(.5, .5, keys))
    assert state.route_signatures[0] == targets[0]
    assert state.route_signatures[1] == targets[1]
