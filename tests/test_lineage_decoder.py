import pytest

from continuous_lineage_decoder import (
    CPLRChromosome,
    CPLRDecoderConfig,
    decode_chromosome,
)
from cplr_standard_certifier import certify_candidate
from generate_welds import Weld
from lineage_presplit import build_lineages
from reachable_side_keys import apply_reachable_side_index, build_reachable_side_index
import math
from mrta_problem_core import assign_welds_to_robots_split


def _instance():
    return [
        Weld("upper-cross", 1, 8, .1, 17, 9, .1),
        Weld("lower-cross", 18, 3, .1, 2, 4, .1),
        Weld("y-cross", 4, 5, .1, 14, 9, .1),
        Weld("vertical-x", 10, 7, .1, 10, 11, .1),
    ]


@pytest.mark.parametrize("upper,lower", [(0.0, 0.0), (0.5, 0.5), (1.0, 1.0), (0.2, 0.8)])
def test_decoder_matches_standard_dynamic_splitter(upper, lower):
    welds = _instance()
    config = CPLRDecoderConfig()
    built = build_lineages(welds)
    index = build_reachable_side_index(built.lineages, config)
    lineages = apply_reachable_side_index(built.lineages, index)
    chromosome = CPLRChromosome.from_mapping(
        upper, lower, {key: ((key.lineage_id * 2 + (key.side == "R")) % 7) / 7 for key in index.keys}
    )
    decoded = decode_chromosome(lineages, chromosome, config)
    certification = certify_candidate(welds, chromosome, config)
    assert decoded.structural_validation.passed
    assert certification.passed, certification.failures


def test_chromosome_rejects_missing_active_key():
    welds = _instance()
    built = build_lineages(welds)
    index = build_reachable_side_index(built.lineages)
    lineages = apply_reachable_side_index(built.lineages, index)
    chromosome = CPLRChromosome.from_mapping(.5, .5, {key: .5 for key in index.keys[:-1]})
    with pytest.raises(ValueError, match="key set mismatch"):
        decode_chromosome(lineages, chromosome)


def test_endpoint_ulp_absorption_matches_frozen_parameter_deduplication():
    welds = [Weld("descending", 1.394981858311, 2.2, .1, .411999436436, 1.5, .1)]
    built = build_lineages(welds)
    index = build_reachable_side_index(built.lineages)
    lineages = apply_reachable_side_index(built.lineages, index)
    boundary = math.nextafter(welds[0].x1, -math.inf)
    chromosome = CPLRChromosome.from_mapping(
        .5, boundary / 20.0, {key: .5 for key in index.keys}
    )
    certification = certify_candidate(welds, chromosome)
    assert certification.passed, certification.failures
    assert certification.decoded_state.route_signatures[2]
    assert not certification.decoded_state.route_signatures[3]


def test_known_standard_splitter_parameter_eps_gap_is_rejected():
    welds = [
        Weld(
            "gap",
            4.198308820222,
            4.322467866854,
            .1,
            1.239168195222,
            4.322467866854,
            .1,
        )
    ]
    boundary = 4.198308818221999
    robots, stats = assign_welds_to_robots_split(welds, boundary, boundary)
    assert sum(map(len, robots)) == 0
    assert stats["unassigned_subweld_count"] == 1
    built = build_lineages(welds)
    index = build_reachable_side_index(built.lineages)
    lineages = apply_reachable_side_index(built.lineages, index)
    chromosome = CPLRChromosome.from_mapping(
        .5, boundary / 20.0, {key: .5 for key in index.keys}
    )
    with pytest.raises(ValueError, match="unassigned"):
        decode_chromosome(lineages, chromosome)
