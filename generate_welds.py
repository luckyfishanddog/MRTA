#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Tuple

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


PLATFORM_W_M = 20.0
PLATFORM_H_M = 12.0
DEFAULT_WELD_Z_M = 0.1
DEFAULT_MIN_WELD_LENGTH_M = 0.5
DEFAULT_SOURCE_EXCEL = r"D:\新建文件夹\小组立qcs\C_lines-change.xlsx"
FROZEN_INSTANCE_GENERATOR_VERSION = "1.0"
HASH_COORDINATE_DECIMALS = 12

COL_VARIANTS_3D = {
    "x1": ["x1", "x_1", "x 1", "start_x", "x_start", "x起点", "xa", "x_a"],
    "y1": ["y1", "y_1", "y 1", "start_y", "y_start", "y起点", "ya", "y_a"],
    "z1": ["z1", "z_1", "z 1", "start_z", "z_start", "z起点", "za", "z_a"],
    "x2": ["x2", "x_2", "x 2", "end_x", "x_end", "x终点", "xb", "x_b"],
    "y2": ["y2", "y_2", "y 2", "end_y", "y_end", "y终点", "yb", "y_b"],
    "z2": ["z2", "z_2", "z 2", "end_z", "z_end", "z终点", "zb", "z_b"],
}
ID_VARIANTS = ["id", "编号", "name", "组号", "group", "group_id", "编号/小组立", "小组立编号"]


def find_columns_3d(df: pd.DataFrame):
    cols = list(df.columns)
    lower = {c: c.lower() for c in cols}
    found = {}
    for key, variants in COL_VARIANTS_3D.items():
        for col, col_lower in lower.items():
            if any(v in col_lower for v in variants):
                found[key] = col
                break
    if len(found) == 6:
        return [found["x1"], found["y1"], found["z1"], found["x2"], found["y2"], found["z2"]]

    numeric = []
    for col in cols:
        sample = pd.to_numeric(df[col].dropna().head(20), errors="coerce")
        if not sample.empty and sample.notna().any():
            numeric.append(col)
    return numeric[:6] if len(numeric) >= 6 else None


def find_id_column(df: pd.DataFrame, provided: str | None):
    if provided:
        for col in df.columns:
            if col.lower() == provided.lower():
                return col
        raise ValueError(f"id column {provided} not found")

    cols = list(df.columns)
    lowered = [c.lower() for c in cols]
    for variant in ID_VARIANTS:
        for i, col_lower in enumerate(lowered):
            if variant in col_lower:
                return cols[i]
    for col in cols:
        if df[col].dtype == object or df[col].dtype == "string":
            return col
    return cols[0] if cols else None


def is_vertical_weld_coords(x1, y1, z1, x2, y2, z2, eps: float = 1e-6) -> bool:
    return (
        abs(float(x2) - float(x1)) <= eps
        and abs(float(y2) - float(y1)) <= eps
        and abs(float(z2) - float(z1)) > eps
    )


def weld_length_m(x1, y1, z1, x2, y2, z2) -> float:
    return math.dist((float(x1), float(y1), float(z1)), (float(x2), float(y2), float(z2)))


def filter_vertical_welds(df: pd.DataFrame, eps: float = 1e-6) -> pd.DataFrame:
    if df.empty:
        return df.copy()
    mask = df.apply(
        lambda r: is_vertical_weld_coords(r["x1"], r["y1"], r["z1"], r["x2"], r["y2"], r["z2"], eps=eps),
        axis=1,
    )
    return df.loc[~mask].copy().reset_index(drop=True)


def filter_short_welds(df: pd.DataFrame, min_length_m: float = DEFAULT_MIN_WELD_LENGTH_M) -> pd.DataFrame:
    if df.empty:
        return df.copy()
    lengths = df.apply(lambda r: math.hypot(float(r["x2"]) - float(r["x1"]), float(r["y2"]) - float(r["y1"])), axis=1)
    return df.loc[lengths >= float(min_length_m)].copy().reset_index(drop=True)


def read_and_prepare_groups(
    path: str,
    id_col: str | None,
    input_units: str,
    thickness_m: float,
    min_weld_length_m: float = DEFAULT_MIN_WELD_LENGTH_M,
):
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    df = pd.read_excel(path, engine="openpyxl")
    if df.empty:
        raise ValueError("Excel has no data")

    cols = find_columns_3d(df)
    if cols is None:
        raise ValueError("Could not identify x1,y1,z1,x2,y2,z2 columns")
    x1c, y1c, z1c, x2c, y2c, z2c = cols

    idc = find_id_column(df, id_col)
    if idc is None:
        raise ValueError("Could not identify group id column")

    conv = 0.001 if input_units.lower() == "mm" else 1.0
    base = pd.DataFrame({
        "_gid": df[idc].astype(str).fillna("").values,
        "x1": pd.to_numeric(df[x1c], errors="coerce") * conv,
        "y1": pd.to_numeric(df[y1c], errors="coerce") * conv,
        "z1": pd.to_numeric(df[z1c], errors="coerce") * conv,
        "x2": pd.to_numeric(df[x2c], errors="coerce") * conv,
        "y2": pd.to_numeric(df[y2c], errors="coerce") * conv,
        "z2": pd.to_numeric(df[z2c], errors="coerce") * conv,
    })

    before = len(base)
    base = base.dropna(how="any")
    if base.empty:
        raise ValueError("No valid endpoint rows")
    if len(base) < before:
        print(f"警告：丢弃 {before - len(base)} 行含非数值/缺失", file=sys.stderr)

    base = filter_vertical_welds(base)
    base = filter_short_welds(base, min_length_m=min_weld_length_m)
    if base.empty:
        raise ValueError("No usable welds after vertical/short weld filtering")

    groups: Dict[str, pd.DataFrame] = {}
    for gid, gdf in base.groupby("_gid"):
        g = gdf.reset_index(drop=True).copy()
        xs = np.concatenate([g["x1"].values, g["x2"].values])
        ys = np.concatenate([g["y1"].values, g["y2"].values])
        zs = np.concatenate([g["z1"].values, g["z2"].values])
        xmin, ymin = float(np.nanmin(xs)), float(np.nanmin(ys))
        zmin = float(np.nanmin(zs)) if zs.size else 0.0
        g["x1_local"] = g["x1"] - xmin
        g["x2_local"] = g["x2"] - xmin
        g["y1_local"] = g["y1"] - ymin
        g["y2_local"] = g["y2"] - ymin
        g["z1_local"] = (g["z1"] - zmin).clip(lower=0.0) + thickness_m
        g["z2_local"] = (g["z2"] - zmin).clip(lower=0.0) + thickness_m
        groups[str(gid)] = g
    return groups, (x1c, y1c, z1c, x2c, y2c, z2c), idc


def group_local_bbox(gdf: pd.DataFrame) -> Tuple[float, float, float, float]:
    xs = np.concatenate([gdf["x1_local"].values, gdf["x2_local"].values])
    ys = np.concatenate([gdf["y1_local"].values, gdf["y2_local"].values])
    return float(np.nanmin(xs)), float(np.nanmin(ys)), float(np.nanmax(xs)), float(np.nanmax(ys))


def rects_overlap(r1, r2, spacing=0.0):
    s = spacing / 2.0
    ax1, ay1, ax2, ay2 = r1
    bx1, by1, bx2, by2 = r2
    ax1 -= s
    ay1 -= s
    ax2 += s
    ay2 += s
    bx1 -= s
    by1 -= s
    bx2 += s
    by2 += s
    return not (ax2 <= bx1 or ax1 >= bx2 or ay2 <= by1 or ay1 >= by2)


def layout_packing(
    groups: Dict[str, pd.DataFrame],
    count: int,
    spacing_m: float,
    platform_w: float = PLATFORM_W_M,
    platform_h: float = PLATFORM_H_M,
    allow_rotate: bool = True,
    seed: int | None = None,
    max_attempts_per_instance: int = 2000,
):
    rng = random.Random(seed)
    gids = list(groups.keys())
    ginfo = {}
    for gid, gdf in groups.items():
        xmin, ymin, xmax, ymax = group_local_bbox(gdf)
        ginfo[gid] = {"w": max(xmax - xmin, 1e-6), "h": max(ymax - ymin, 1e-6)}

    placed = []
    placed_rects = []
    for inst in range(count):
        ok = False
        for _ in range(max_attempts_per_instance):
            gid = rng.choice(gids)
            w, h = ginfo[gid]["w"], ginfo[gid]["h"]
            rot = False
            if allow_rotate and rng.choice([False, True]):
                rot = True
                w, h = h, w
            if w > platform_w or h > platform_h:
                continue
            px = rng.uniform(0.0, platform_w - w)
            py = rng.uniform(0.0, platform_h - h)
            rect = (px, py, px + w, py + h)
            if not any(rects_overlap(rect, old, spacing=spacing_m) for old in placed_rects):
                placed.append({"instance_index": inst, "group_id": gid, "pos": (px, py), "rot": rot})
                placed_rects.append(rect)
                ok = True
                break
        if not ok:
            print(f"警告：实例 {inst} 放置失败，已放置 {len(placed)} 个实例。", file=sys.stderr)
            break
    return placed, placed_rects


def generate_placed_welds(groups: Dict[str, pd.DataFrame], placed, output_units="m", weld_z_m: float = DEFAULT_WELD_Z_M):
    rows = []
    for p in placed:
        inst = p["instance_index"]
        gid = p["group_id"]
        px, py = p["pos"]
        rot = p.get("rot", False)
        for i, row in groups[gid].iterrows():
            lx1, lx2 = float(row["x1_local"]), float(row["x2_local"])
            ly1, ly2 = float(row["y1_local"]), float(row["y2_local"])
            if rot:
                nx1, ny1 = px + ly1, py + lx1
                nx2, ny2 = px + ly2, py + lx2
            else:
                nx1, ny1 = px + lx1, py + ly1
                nx2, ny2 = px + lx2, py + ly2
            rows.append({
                "instance_index": inst,
                "group_id": gid,
                "orig_index": i,
                "x1": nx1,
                "y1": ny1,
                "z1": float(weld_z_m),
                "x2": nx2,
                "y2": ny2,
                "z2": float(weld_z_m),
            })
    df = pd.DataFrame(rows)
    if output_units.lower() == "mm" and not df.empty:
        df[["x1", "y1", "z1", "x2", "y2", "z2"]] *= 1000.0
    return df


def safe_set_limits(ax, xmin, xmax, ymin, ymax, zmin, zmax):
    scale = max(abs(xmax - xmin), abs(ymax - ymin), abs(zmax - zmin), 1.0)
    eps = max(1e-6, scale * 1e-6, 1e-3)
    if xmin == xmax:
        xmin, xmax = xmin - eps, xmax + eps
    if ymin == ymax:
        ymin, ymax = ymin - eps, ymax + eps
    if zmin == zmax:
        zmin, zmax = zmin - eps, zmax + eps
    ax.set_xlim(xmin, xmax)
    ax.set_ylim(ymin, ymax)
    ax.set_zlim(zmin, zmax)
    try:
        ax.set_box_aspect((max(xmax - xmin, eps), max(ymax - ymin, eps), max(zmax - zmin, eps)))
    except Exception:
        pass


def plot_results(df_placed, placed_rects, outpath=None, annotate=False, output_units="mm", figsize=(12, 6), elev=20, azim=-60, thickness_m=0.01):
    if df_placed.empty:
        print("无数据可绘图", file=sys.stderr)
        return
    fig = plt.figure(figsize=figsize)
    ax = fig.add_subplot(111, projection="3d")
    cmap = plt.get_cmap("tab20")
    for i, inst in enumerate(sorted(df_placed["instance_index"].unique())):
        sub = df_placed[df_placed["instance_index"] == inst]
        color = cmap(i % 20)
        for _, row in sub.iterrows():
            ax.plot([row["x1"], row["x2"]], [row["y1"], row["y2"]], [row["z1"], row["z2"]], marker="o", color=color, linewidth=1.2)
            if annotate:
                mx = (row["x1"] + row["x2"]) / 2.0
                my = (row["y1"] + row["y2"]) / 2.0
                mz = (row["z1"] + row["z2"]) / 2.0
                ax.text(mx, my, mz, f"{int(row['instance_index'])}:{int(row['orig_index'])}", fontsize=7)

    if output_units.lower() == "mm":
        plat_w, plat_h = PLATFORM_W_M * 1000.0, PLATFORM_H_M * 1000.0
        rects = [(a * 1000.0, b * 1000.0, c * 1000.0, d * 1000.0) for a, b, c, d in placed_rects]
        z_plane = thickness_m * 1000.0
    else:
        plat_w, plat_h = PLATFORM_W_M, PLATFORM_H_M
        rects = placed_rects
        z_plane = thickness_m

    xs = np.concatenate([df_placed["x1"].values, df_placed["x2"].values])
    ys = np.concatenate([df_placed["y1"].values, df_placed["y2"].values])
    zs = np.concatenate([df_placed["z1"].values, df_placed["z2"].values])
    z_level = z_plane - (1.0 if output_units.lower() == "mm" else 0.001)

    for ex, ey in [([0.0, plat_w], [0.0, 0.0]), ([plat_w, plat_w], [0.0, plat_h]), ([plat_w, 0.0], [plat_h, plat_h]), ([0.0, 0.0], [plat_h, 0.0])]:
        ax.plot(ex, ey, [z_level, z_level], color="k", linewidth=1.0)
    for rx1, ry1, rx2, ry2 in rects:
        ax.plot([rx1, rx2, rx2, rx1, rx1], [ry1, ry1, ry2, ry2, ry1], [z_level] * 5, color="gray", linewidth=0.8, alpha=0.7)

    safe_set_limits(ax, 0.0, max(plat_w, float(np.nanmax(xs))), 0.0, max(plat_h, float(np.nanmax(ys))), float(np.nanmin(zs)), float(np.nanmax(zs)))
    ax.set_xlabel(f"X ({output_units})")
    ax.set_ylabel(f"Y ({output_units})")
    ax.set_zlabel(f"Z ({output_units})")
    ax.view_init(elev=elev, azim=azim)
    ax.grid(True, linestyle="--", alpha=0.4)
    plt.tight_layout()
    try:
        if outpath:
            plt.savefig(outpath, dpi=300)
            print(f"图片已保存: {outpath}")
        else:
            plt.show()
    finally:
        plt.close(fig)


class Weld:
    def __init__(
        self,
        wid,
        x1, y1, z1,
        x2, y2, z2,
        parent_id=None,
        segment_id=0,
        is_split_segment=False,
        original_start=None,
        original_end=None,
        source_weld_id=None,
    ):
        self.id = wid
        self.x1, self.y1, self.z1 = x1, y1, z1
        self.x2, self.y2, self.z2 = x2, y2, z2
        self.length = math.dist((x1, y1, z1), (x2, y2, z2))
        self.is_vertical = abs(x2 - x1) < 1e-6 and abs(y2 - y1) < 1e-6 and abs(z2 - z1) > 1e-6
        if self.is_vertical and z2 < z1:
            self.x1, self.x2 = x2, x1
            self.y1, self.y2 = y2, y1
            self.z1, self.z2 = z2, z1
        self.parent_id = wid if parent_id is None else parent_id
        self.segment_id = segment_id
        self.is_split_segment = is_split_segment
        self.original_start = self.start_point() if original_start is None else tuple(original_start)
        self.original_end = self.end_point() if original_end is None else tuple(original_end)
        self.source_weld_id = self.parent_id if source_weld_id is None else source_weld_id

    def midpoint(self):
        return ((self.x1 + self.x2) / 2, (self.y1 + self.y2) / 2, (self.z1 + self.z2) / 2)

    def start_point(self):
        return (self.x1, self.y1, self.z1)

    def end_point(self):
        return (self.x2, self.y2, self.z2)

    @property
    def wid(self):
        return self.id

    @property
    def centroid(self):
        return self.midpoint()

    def endpoints(self):
        return (self.start_point(), self.end_point())


def _sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _stable_weld_id(instance_index: int, group_id: str, orig_index: int) -> str:
    """Return an ID that is stable across generation, XLSX and reload."""
    return f"inst{int(instance_index):04d}|group={group_id}|row={int(orig_index):04d}"


def _canonical_coordinate(value: float) -> float:
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(f"non-finite weld coordinate: {value}")
    return round(value, HASH_COORDINATE_DECIMALS)


def compute_weld_instance_hash(
    welds: List[Weld],
    metadata: Dict | None = None,
) -> str:
    """Compute the deterministic SHA-256 identity of a frozen instance."""
    metadata = metadata or {}
    weld_z_m = float(
        metadata.get(
            "weld_z_m",
            welds[0].z1 if welds else DEFAULT_WELD_Z_M,
        )
    )
    min_length_m = float(
        metadata.get("min_weld_length_m", DEFAULT_MIN_WELD_LENGTH_M)
    )
    lines = [
        f"platform_w_m={PLATFORM_W_M:.12f}",
        f"platform_h_m={PLATFORM_H_M:.12f}",
        f"weld_z_m={weld_z_m:.12f}",
        f"min_weld_length_m={min_length_m:.12f}",
    ]
    seen_ids = set()
    for weld in sorted(welds, key=lambda item: str(item.id)):
        weld_id = str(weld.id)
        if weld_id in seen_ids:
            raise ValueError(f"duplicate weld ID in frozen instance: {weld_id}")
        seen_ids.add(weld_id)
        coordinates = [
            _canonical_coordinate(value)
            for value in (
                weld.x1,
                weld.y1,
                weld.z1,
                weld.x2,
                weld.y2,
                weld.z2,
            )
        ]
        lines.append(
            "|".join(
                [weld_id] + [f"{value:.12f}" for value in coordinates]
            )
        )
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def _metadata_value_for_excel(value):
    if value is None:
        return ""
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return value


def _metadata_value_from_excel(value):
    if pd.isna(value):
        return None
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith(("[", "{")):
            try:
                return json.loads(stripped)
            except json.JSONDecodeError:
                pass
    return value


def generate_weld_instance_from_source(
    source_excel: str,
    requested_group_count: int,
    instance_seed: int,
    *,
    input_units: str = "mm",
    thickness_m: float = 0.01,
    spacing_m: float = 0.03,
    allow_rotate: bool = True,
    min_weld_length_m: float = DEFAULT_MIN_WELD_LENGTH_M,
    weld_z_m: float = DEFAULT_WELD_Z_M,
    target_weld_count: int | None = None,
    max_attempts_per_instance: int = 2000,
) -> tuple[List[Weld], Dict]:
    """Generate one random platform layout from the raw assembly workbook.

    ``requested_group_count`` controls assembly instances.  It never means
    weld count.  ``instance_seed`` is used only by layout generation.
    """
    if requested_group_count < 0:
        raise ValueError("requested_group_count must be non-negative")
    source_excel = str(Path(source_excel).resolve())
    groups, used_columns, id_column = read_and_prepare_groups(
        source_excel,
        None,
        input_units,
        thickness_m,
        min_weld_length_m=min_weld_length_m,
    )
    placed, placed_rects = layout_packing(
        groups,
        requested_group_count,
        spacing_m,
        platform_w=PLATFORM_W_M,
        platform_h=PLATFORM_H_M,
        allow_rotate=allow_rotate,
        seed=instance_seed,
        max_attempts_per_instance=max_attempts_per_instance,
    )
    frame = generate_placed_welds(
        groups, placed, output_units="m", weld_z_m=weld_z_m
    )
    welds: List[Weld] = []
    for _, row in frame.sort_values(
        ["instance_index", "group_id", "orig_index"], kind="stable"
    ).iterrows():
        weld_id = _stable_weld_id(
            int(row["instance_index"]), str(row["group_id"]), int(row["orig_index"])
        )
        coordinates = [
            _canonical_coordinate(row[name])
            for name in ("x1", "y1", "z1", "x2", "y2", "z2")
        ]
        weld = Weld(weld_id, *coordinates)
        weld.instance_index = int(row["instance_index"])
        weld.group_id = str(row["group_id"])
        weld.orig_index = int(row["orig_index"])
        if weld.is_vertical:
            raise ValueError(f"vertical weld survived source filtering: {weld_id}")
        if weld.length + 1e-12 < min_weld_length_m:
            raise ValueError(f"short weld survived source filtering: {weld_id}")
        welds.append(weld)

    metadata: Dict = {
        "source_excel": source_excel,
        "source_excel_sha256": _sha256_file(source_excel),
        "platform_w_m": PLATFORM_W_M,
        "platform_h_m": PLATFORM_H_M,
        "target_weld_count": (
            int(target_weld_count) if target_weld_count is not None else len(welds)
        ),
        "requested_group_count": int(requested_group_count),
        "placed_group_count": len(placed),
        "actual_weld_count": len(welds),
        "instance_seed": int(instance_seed),
        "spacing_m": float(spacing_m),
        "allow_rotate": bool(allow_rotate),
        "input_units": input_units,
        "output_units": "m",
        "thickness_m": float(thickness_m),
        "weld_z_m": float(weld_z_m),
        "min_weld_length_m": float(min_weld_length_m),
        "generator_version": FROZEN_INSTANCE_GENERATOR_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_coordinate_columns": list(used_columns),
        "source_group_id_column": str(id_column),
        "placed_rectangles_m": [list(map(float, rect)) for rect in placed_rects],
        "placed_groups": [
            {
                "instance_index": int(item["instance_index"]),
                "group_id": str(item["group_id"]),
                "x_m": float(item["pos"][0]),
                "y_m": float(item["pos"][1]),
                "rotated_90_deg": bool(item.get("rot", False)),
            }
            for item in placed
        ],
    }
    metadata["total_original_weld_length"] = sum(weld.length for weld in welds)
    metadata["instance_hash"] = compute_weld_instance_hash(welds, metadata)
    return welds, metadata


def _frozen_weld_rows(welds: List[Weld]) -> List[Dict]:
    rows = []
    for weld in sorted(welds, key=lambda item: str(item.id)):
        rows.append(
            {
                "weld_id": str(weld.id),
                "instance_index": getattr(weld, "instance_index", ""),
                "group_id": getattr(weld, "group_id", ""),
                "orig_index": getattr(weld, "orig_index", ""),
                "x1": _canonical_coordinate(weld.x1),
                "y1": _canonical_coordinate(weld.y1),
                "z1": _canonical_coordinate(weld.z1),
                "x2": _canonical_coordinate(weld.x2),
                "y2": _canonical_coordinate(weld.y2),
                "z2": _canonical_coordinate(weld.z2),
                "length_m": float(weld.length),
            }
        )
    return rows


def save_frozen_weld_instance(
    welds: List[Weld],
    metadata: Dict,
    output_xlsx: str,
    output_json: str | None = None,
) -> None:
    """Save a frozen layout without regenerating or moving any weld."""
    metadata = dict(metadata)
    metadata["actual_weld_count"] = len(welds)
    metadata["total_original_weld_length"] = sum(weld.length for weld in welds)
    metadata["instance_hash"] = compute_weld_instance_hash(welds, metadata)
    output_path = Path(output_xlsx)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    weld_frame = pd.DataFrame(_frozen_weld_rows(welds))
    meta_frame = pd.DataFrame(
        [
            {"key": key, "value": _metadata_value_for_excel(value)}
            for key, value in metadata.items()
        ]
    )
    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        weld_frame.to_excel(writer, sheet_name="placed_welds", index=False)
        meta_frame.to_excel(writer, sheet_name="meta", index=False)
    if output_json:
        json_path = Path(output_json)
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )


def load_frozen_weld_instance(instance_xlsx: str) -> tuple[List[Weld], Dict]:
    """Load and validate a frozen layout; never call random placement."""
    path = Path(instance_xlsx).resolve()
    if not path.exists():
        raise FileNotFoundError(path)
    sheets = pd.read_excel(path, sheet_name=None, engine="openpyxl")
    if "placed_welds" not in sheets or "meta" not in sheets:
        raise ValueError("frozen instance must contain placed_welds and meta sheets")
    frame = sheets["placed_welds"]
    required = {"weld_id", "x1", "y1", "z1", "x2", "y2", "z2"}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"frozen instance missing columns: {sorted(missing)}")
    meta_frame = sheets["meta"]
    if not {"key", "value"}.issubset(meta_frame.columns):
        raise ValueError("meta sheet must contain key/value columns")
    metadata = {
        str(row["key"]): _metadata_value_from_excel(row["value"])
        for _, row in meta_frame.iterrows()
        if not pd.isna(row["key"])
    }
    expected_platform = (float(metadata.get("platform_w_m", -1)), float(metadata.get("platform_h_m", -1)))
    if expected_platform != (PLATFORM_W_M, PLATFORM_H_M):
        raise ValueError(f"platform mismatch: {expected_platform}")
    min_length_m = float(metadata.get("min_weld_length_m", DEFAULT_MIN_WELD_LENGTH_M))
    welds: List[Weld] = []
    for _, row in frame.sort_values("weld_id", kind="stable").iterrows():
        coordinates = [
            _canonical_coordinate(row[name])
            for name in ("x1", "y1", "z1", "x2", "y2", "z2")
        ]
        x1, y1, z1, x2, y2, z2 = coordinates
        for x in (x1, x2):
            if x < -1e-9 or x > PLATFORM_W_M + 1e-9:
                raise ValueError(f"weld outside platform x range: {row['weld_id']}")
        for y in (y1, y2):
            if y < -1e-9 or y > PLATFORM_H_M + 1e-9:
                raise ValueError(f"weld outside platform y range: {row['weld_id']}")
        weld = Weld(str(row["weld_id"]), *coordinates)
        if weld.length + 1e-9 < min_length_m:
            raise ValueError(f"weld below minimum length: {weld.id}")
        for name in ("instance_index", "orig_index"):
            if name in row and not pd.isna(row[name]):
                setattr(weld, name, int(row[name]))
        if "group_id" in row and not pd.isna(row["group_id"]):
            weld.group_id = str(row["group_id"])
        welds.append(weld)
    actual = int(metadata.get("actual_weld_count", -1))
    if actual != len(welds):
        raise ValueError(
            f"actual_weld_count mismatch: metadata={actual}, loaded={len(welds)}"
        )
    computed_hash = compute_weld_instance_hash(welds, metadata)
    expected_hash = str(metadata.get("instance_hash", ""))
    if computed_hash != expected_hash:
        raise ValueError(
            f"instance_hash mismatch: expected={expected_hash}, computed={computed_hash}"
        )
    computed_length = sum(weld.length for weld in welds)
    recorded_length = float(metadata.get("total_original_weld_length", computed_length))
    if not math.isclose(computed_length, recorded_length, rel_tol=0.0, abs_tol=1e-9):
        raise ValueError("total_original_weld_length mismatch")
    metadata["instance_path"] = str(path)
    return welds, metadata


def resolve_solver_seed(solver_seed: int | None, legacy_seed: int | None) -> int:
    if solver_seed is not None and legacy_seed is not None:
        raise ValueError("use --solver-seed or deprecated --seed, not both")
    if legacy_seed is not None:
        print("Warning: --seed is deprecated; it now means solver seed only.", file=sys.stderr)
        return int(legacy_seed)
    return 42 if solver_seed is None else int(solver_seed)


def load_solver_weld_input(
    *,
    instance_path: str | None,
    source_excel: str | None,
    group_count: int | None,
    instance_seed: int | None,
    input_units: str = "mm",
    thickness_m: float = 0.01,
    spacing_m: float = 0.03,
    allow_rotate: bool = True,
    min_weld_length_m: float = DEFAULT_MIN_WELD_LENGTH_M,
    weld_z_m: float = DEFAULT_WELD_Z_M,
    expected_weld_count: int | None = None,
) -> tuple[List[Weld], Dict]:
    """Resolve the mutually exclusive frozen-input and debug-generation modes."""
    if instance_path:
        if source_excel or group_count is not None or instance_seed is not None:
            raise ValueError("--instance-path cannot be combined with source generation arguments")
        welds, metadata = load_frozen_weld_instance(instance_path)
    else:
        if not source_excel or group_count is None or instance_seed is None:
            raise ValueError(
                "use --instance-path, or provide --source-excel, --group-count and --instance-seed"
            )
        print(
            "Warning: on-the-fly source generation is debug-only; formal experiments must use --instance-path.",
            file=sys.stderr,
        )
        welds, metadata = generate_weld_instance_from_source(
            source_excel,
            group_count,
            instance_seed,
            input_units=input_units,
            thickness_m=thickness_m,
            spacing_m=spacing_m,
            allow_rotate=allow_rotate,
            min_weld_length_m=min_weld_length_m,
            weld_z_m=weld_z_m,
            target_weld_count=expected_weld_count,
        )
        metadata["instance_path"] = ""
    if expected_weld_count is not None and len(welds) != expected_weld_count:
        raise ValueError(
            f"expected actual weld count {expected_weld_count}, got {len(welds)}"
        )
    return welds, metadata


def instance_metrics(
    metadata: Dict,
    welds: List[Weld],
    solver_seed: int,
) -> Dict:
    """Fields shared by every solver output for input identity auditing."""
    return {
        "instance_path": metadata.get("instance_path", ""),
        "instance_seed": int(metadata.get("instance_seed", -1)),
        "solver_seed": int(solver_seed),
        "instance_hash": metadata.get("instance_hash", compute_weld_instance_hash(welds, metadata)),
        "target_weld_count": int(metadata.get("target_weld_count", len(welds))),
        "requested_group_count": int(metadata.get("requested_group_count", -1)),
        "placed_group_count": int(metadata.get("placed_group_count", -1)),
        "actual_weld_count": len(welds),
        "original_weld_count": len(welds),
        "total_original_weld_length": sum(weld.length for weld in welds),
    }


def make_subweld(parent_weld: Weld, segment_id, start_point, end_point, wid=None) -> Weld:
    parent_id = getattr(parent_weld, "parent_id", parent_weld.id)
    if wid is None:
        wid = f"{parent_id}_seg{segment_id}"
    x1, y1, z1 = start_point
    x2, y2, z2 = end_point
    return Weld(
        wid,
        x1, y1, z1,
        x2, y2, z2,
        parent_id=parent_id,
        segment_id=segment_id,
        is_split_segment=True,
        original_start=getattr(parent_weld, "original_start", parent_weld.start_point()),
        original_end=getattr(parent_weld, "original_end", parent_weld.end_point()),
        source_weld_id=parent_id,
    )


def get_welds_from_excel(
    excel_path: str,
    input_units: str = "mm",
    thickness_m: float = 0.01,
    count: int = 30,
    spacing_m: float = 0.03,
    allow_rotate: bool = True,
    seed: int = None,
    min_weld_length_m: float = DEFAULT_MIN_WELD_LENGTH_M,
    weld_z_m: float = DEFAULT_WELD_Z_M,
) -> List[Weld]:
    groups, _, _ = read_and_prepare_groups(excel_path, None, input_units, thickness_m, min_weld_length_m=min_weld_length_m)
    placed, _ = layout_packing(groups, count, spacing_m, allow_rotate=allow_rotate, seed=seed)
    df = generate_placed_welds(groups, placed, output_units="m", weld_z_m=weld_z_m)
    welds = []
    for idx, row in df.iterrows():
        weld = Weld(idx, row["x1"], row["y1"], row["z1"], row["x2"], row["y2"], row["z2"])
        if not weld.is_vertical and weld.length >= min_weld_length_m:
            welds.append(weld)
    return welds


def get_welds(
    input_units: str = "mm",
    thickness_m: float = 0.01,
    count: int = 30,
    spacing_m: float = 0.03,
    allow_rotate: bool = True,
    seed: int = None,
    excel_path: str | None = None,
    min_weld_length_m: float = DEFAULT_MIN_WELD_LENGTH_M,
    weld_z_m: float = DEFAULT_WELD_Z_M,
) -> List[Weld]:
    return get_welds_from_excel(
        excel_path or DEFAULT_SOURCE_EXCEL,
        input_units=input_units,
        thickness_m=thickness_m,
        count=count,
        spacing_m=spacing_m,
        allow_rotate=allow_rotate,
        seed=seed,
        min_weld_length_m=min_weld_length_m,
        weld_z_m=weld_z_m,
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("excel", nargs="?", default=DEFAULT_SOURCE_EXCEL)
    parser.add_argument("--group-count", type=int, default=None, help="Requested assembly/group count.")
    parser.add_argument("-n", "--count", type=int, default=None, help="Deprecated alias for --group-count.")
    parser.add_argument("--id-col", type=str, default=None)
    parser.add_argument("--input-units", choices=["mm", "m"], default="mm")
    parser.add_argument("--output-units", choices=["mm", "m"], default="mm")
    parser.add_argument("--spacing", type=float, default=30.0, help="unit follows input-units")
    parser.add_argument("--thickness", type=float, default=10.0, help="unit follows input-units")
    parser.add_argument("--allow-rotate", action="store_true")
    parser.add_argument("--instance-seed", type=int, default=None)
    parser.add_argument("--seed", type=int, default=None, help="Deprecated alias for --instance-seed in this generator only.")
    parser.add_argument("--output-image", type=str, default=None)
    parser.add_argument("--output-xlsx", type=str, default=None)
    parser.add_argument("--weld-z-mm", type=float, default=DEFAULT_WELD_Z_M * 1000.0)
    parser.add_argument("--min-weld-length-mm", type=float, default=DEFAULT_MIN_WELD_LENGTH_M * 1000.0)
    parser.add_argument("--annotate", action="store_true")
    parser.add_argument("--figsize", type=str, default="12,6")
    args = parser.parse_args()

    if args.group_count is not None and args.count is not None:
        parser.error("use --group-count or deprecated --count, not both")
    group_count = args.group_count if args.group_count is not None else (args.count if args.count is not None else 30)
    if args.count is not None:
        print("Warning: --count is deprecated; use --group-count.", file=sys.stderr)
    if args.instance_seed is not None and args.seed is not None:
        parser.error("use --instance-seed or deprecated --seed, not both")
    instance_seed = args.instance_seed if args.instance_seed is not None else args.seed
    if args.seed is not None:
        print("Warning: generator --seed is deprecated; use --instance-seed.", file=sys.stderr)

    if not os.path.exists(args.excel):
        print("Excel 文件不存在", file=sys.stderr)
        sys.exit(1)

    out_img = args.output_image or os.path.splitext(args.excel)[0] + "_platform_final_v2.png"
    out_xlsx = args.output_xlsx or os.path.splitext(args.excel)[0] + "_platform_final_v2.xlsx"
    try:
        figsize = tuple(float(x) for x in args.figsize.split(","))
        if len(figsize) != 2:
            raise ValueError
    except Exception:
        figsize = (12, 6)

    conv_in = 0.001 if args.input_units == "mm" else 1.0
    spacing_m = args.spacing * conv_in
    thickness_m = args.thickness * conv_in
    weld_z_m = args.weld_z_mm / 1000.0
    min_weld_length_m = args.min_weld_length_mm / 1000.0

    groups, used_cols, idc = read_and_prepare_groups(
        args.excel,
        args.id_col,
        args.input_units,
        thickness_m,
        min_weld_length_m=min_weld_length_m,
    )
    print(f"识别到 {len(groups)} 个小组立；端点列: {used_cols}；ID 列: {idc}")
    placed, placed_rects = layout_packing(
        groups,
        group_count,
        spacing_m,
        platform_w=PLATFORM_W_M,
        platform_h=PLATFORM_H_M,
        allow_rotate=args.allow_rotate,
        seed=instance_seed,
    )
    print(f"请求放置 {group_count} 个实例，实际放置 {len(placed)} 个实例。")

    df_placed = generate_placed_welds(groups, placed, output_units=args.output_units, weld_z_m=weld_z_m)
    meta = {
        "platform_w_m": PLATFORM_W_M,
        "platform_h_m": PLATFORM_H_M,
        "input_units": args.input_units,
        "output_units": args.output_units,
        "spacing_m": spacing_m,
        "thickness_m": thickness_m,
        "weld_z_m": weld_z_m,
        "min_weld_length_m": min_weld_length_m,
        "requested_group_count": group_count,
        "placed_count": len(placed),
        "instance_seed": instance_seed,
    }
    with pd.ExcelWriter(out_xlsx, engine="openpyxl") as writer:
        df_placed.to_excel(writer, sheet_name="placed_welds", index=False)
        pd.DataFrame([meta]).to_excel(writer, sheet_name="meta", index=False)
    print(f"已保存放置后的焊缝坐标: {out_xlsx} （单位: {args.output_units}）")

    try:
        plot_results(df_placed, placed_rects, outpath=out_img, annotate=args.annotate, output_units=args.output_units, figsize=figsize, thickness_m=weld_z_m)
        print(f"可视化图片已保存: {out_img}")
    except Exception as exc:
        print("绘图异常（已捕获）:", exc, file=sys.stderr)
    print("完成。")


if __name__ == "__main__":
    main()
