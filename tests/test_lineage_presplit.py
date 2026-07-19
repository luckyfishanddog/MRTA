import math

from generate_welds import Weld
from lineage_presplit import build_lineages


def _weld(name, x1, y1, x2, y2):
    return Weld(name, x1, y1, 0.1, x2, y2, 0.1)


def test_horizontal_presplit_conserves_parent_intervals_and_length():
    welds = [_weld("cross", 1, 5, 5, 9), _weld("upper", 2, 8, 7, 8)]
    result = build_lineages(welds)
    children = [item for item in result.lineages if item.parent_weld_id == "cross"]
    assert len(children) == 2
    assert {item.half_region for item in children} == {"upper", "lower"}
    assert math.isclose(sum(item.length for item in children), welds[0].length, abs_tol=1e-12)
    assert sorted((item.parent_s_start, item.parent_s_end) for item in children) == [
        (0.0, 0.25),
        (0.25, 1.0),
    ]
    assert result.length_error <= 1e-12


def test_lineage_ids_are_stable_under_input_reordering():
    a = _weld("a", 0, 8, 4, 8)
    b = _weld("b", 2, 4, 6, 8)
    forward = build_lineages([a, b]).lineages
    reverse = build_lineages([b, a]).lineages
    assert forward == reverse


def test_segment_on_fixed_horizontal_boundary_uses_upper_owner():
    result = build_lineages([_weld("on-y", 1, 6, 4, 6)])
    assert len(result.lineages) == 1
    assert result.lineages[0].half_region == "upper"
