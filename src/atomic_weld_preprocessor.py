"""Preprocess frozen selected instances into immutable atomic weld sets."""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence, Tuple

from atomic_problem_core import (
    ATOMIC_MODEL_ID, FIXED_Y_CUT_M, AtomicWeld, atomic_payload_hash,
    generate_boundary_events, sha256_file, validate_event_constancy,
)
from generate_welds import Weld, load_frozen_weld_instance
from mrta_problem_core import DEFAULT_MOTION_MODEL


LMAX_M = 5.0
LMIN_M = 1.0


def _point_at(weld: Weld, t: float) -> Tuple[float, float, float]:
    return tuple(float(a + t * (b - a)) for a, b in zip(weld.start_point(), weld.end_point()))  # type: ignore[return-value]


def split_at_y6(weld: Weld) -> List[Tuple[str, Tuple[float, float, float], Tuple[float, float, float], bool]]:
    p1, p2 = weld.start_point(), weld.end_point()
    y1, y2 = float(p1[1]), float(p2[1])
    if min(y1, y2) < FIXED_Y_CUT_M < max(y1, y2):
        t = (FIXED_Y_CUT_M - y1) / (y2 - y1)
        middle = _point_at(weld, t)
        pieces = [(p1, middle), (middle, p2)]
        return [("upper" if (a[1] + b[1]) / 2.0 >= FIXED_Y_CUT_M else "lower", a, b, True) for a, b in pieces]
    half = "upper" if (y1 + y2) / 2.0 >= FIXED_Y_CUT_M else "lower"
    return [(half, p1, p2, False)]


def preprocess_welds(welds: Sequence[Weld], source_instance_hash: str) -> List[AtomicWeld]:
    result: List[AtomicWeld] = []
    for weld in sorted(welds, key=lambda item: str(item.id)):
        pieces = split_at_y6(weld)
        for half_index, (half, start, end, ysplit) in enumerate(pieces):
            horizontal = abs(float(end[0]) - float(start[0]))
            count = max(2, math.floor(horizontal / LMIN_M)) if horizontal > LMAX_M + 1e-12 else 1
            parent = f"{weld.id}|half={half}|part={half_index + 1}of{len(pieces)}"
            for index in range(count):
                a = tuple(start[d] + (end[d] - start[d]) * index / count for d in range(3))
                b = tuple(start[d] + (end[d] - start[d]) * (index + 1) / count for d in range(3))
                length = math.dist(a, b)
                hx = abs(b[0] - a[0])
                if hx > LMAX_M + 1e-9:
                    raise ValueError("long-weld split left an infeasible atomic segment")
                if count > 1 and hx < LMIN_M - 1e-9:
                    raise ValueError("long-weld split produced segment below lmin")
                atomic_id = f"{parent}|seg={index + 1}of{count}"
                result.append(AtomicWeld(
                    atomic_weld_id=atomic_id, parent_weld_id=parent,
                    parent_original_id=str(weld.id), half_region=half,
                    segment_index=index + 1, segment_count=count,
                    start=tuple(round(float(v), 12) for v in a),
                    end=tuple(round(float(v), 12) for v in b),
                    euclidean_length_m=length, horizontal_length_m=hx,
                    weld_time_s=length / DEFAULT_MOTION_MODEL.weld_speed,
                    is_y6_split=ysplit, is_long_weld_split=count > 1,
                    source_instance_hash=source_instance_hash,
                ))
    if len({w.id for w in result}) != len(result):
        raise ValueError("atomic weld IDs are not unique")
    return result


def preprocess_instance(name: str, xlsx_path: Path, output_dir: Path) -> Dict[str, Any]:
    welds, source_meta = load_frozen_weld_instance(str(xlsx_path))
    source_hash = str(source_meta["instance_hash"])
    atomic = preprocess_welds(welds, source_hash)
    upper = generate_boundary_events(atomic, "upper")
    lower = generate_boundary_events(atomic, "lower")
    if not all(validate_event_constancy(e, atomic) for e in (*upper, *lower)):
        raise AssertionError("event equivalence interval proof failed")
    metadata = {
        "model_id": ATOMIC_MODEL_ID, "model_version": "1.0.0",
        "source_instance_path": str(xlsx_path.resolve()),
        "source_instance_file_sha256": sha256_file(xlsx_path),
        "source_instance_hash": source_hash, "source_weld_count": len(welds),
        "atomic_weld_count": len(atomic), "fixed_y_cut_m": FIXED_Y_CUT_M,
        "lmax_m": LMAX_M, "lmin_m": LMIN_M,
        "search_time_splitting": False, "setup_time_s": 0.0, "post_time_s": 0.0,
    }
    atomic_hash = atomic_payload_hash(atomic, metadata)
    payload = {
        "name": name, "metadata": metadata, "atomic_instance_hash": atomic_hash,
        "atomic_welds": [w.to_dict() for w in atomic],
        "boundary_events": {"upper": [e.to_dict() for e in upper], "lower": [e.to_dict() for e in lower]},
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"instance_{name}_atomic.json"
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    (output_path.with_suffix(output_path.suffix + ".sha256")).write_text(sha256_file(output_path) + "\n", encoding="utf-8")
    return {
        "instance": name, "source_weld_count": len(welds), "atomic_weld_count": len(atomic),
        "y6_split_atomic_count": sum(w.is_y6_split for w in atomic),
        "long_split_atomic_count": sum(w.is_long_weld_split for w in atomic),
        "upper_event_count": len(upper), "lower_event_count": len(lower),
        "source_instance_hash": source_hash, "atomic_instance_hash": atomic_hash,
        "atomic_json": str(output_path),
    }


def run_all(output_dir: Path = Path("data/instances/atomic_l5_l1")) -> List[Dict[str, Any]]:
    selected = Path("data/instances/selected")
    rows = [preprocess_instance(name, selected / f"instance_{name}.xlsx", output_dir) for name in ("w30", "w45", "w60")]
    manifest = {"model_id": ATOMIC_MODEL_ID, "instances": rows}
    manifest_path = output_dir / "atomic_instances_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    with Path("atomic_preprocessing_summary.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader(); writer.writerows(rows)
    event_rows = [{"instance": r["instance"], "half_region": h, "event_count": r[f"{h}_event_count"], "atomic_instance_hash": r["atomic_instance_hash"]} for r in rows for h in ("upper", "lower")]
    with Path("atomic_boundary_events_summary.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(event_rows[0])); writer.writeheader(); writer.writerows(event_rows)
    parent_path = output_dir / "atomic_parent_map.csv"
    with parent_path.open("w", newline="", encoding="utf-8-sig") as handle:
        fields = ["instance", "atomic_weld_id", "parent_weld_id", "parent_original_id", "half_region", "segment_index", "segment_count"]
        writer = csv.DictWriter(handle, fieldnames=fields); writer.writeheader()
        for row in rows:
            value = json.loads(Path(row["atomic_json"]).read_text(encoding="utf-8"))
            for weld in value["atomic_welds"]:
                writer.writerow({"instance": row["instance"], **{key: weld[key] for key in fields[1:]}})
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default="data/instances/atomic_l5_l1")
    args = parser.parse_args()
    print(json.dumps(run_all(Path(args.output_dir)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
