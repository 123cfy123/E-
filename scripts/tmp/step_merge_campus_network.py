"""
合并脚本：
1. 将现有路网 ecnu_edges_merged 的每条线裁到校园边界内（跨界切断，校外删除）
2. 补充校园内、OSM 存在但现有路网未覆盖的可步行段
3. 去重，输出候选路网 _campus_network_candidate.geojson
"""
import sys, io, json
from pathlib import Path

import osmnx as ox
import geopandas as gpd
import pandas as pd
from shapely.geometry import LineString, shape, mapping
from shapely.ops import unary_union

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

CELL = Path("data") / "ecnu_edges_merged.geojson"
BOUND = Path("data") / "campus_boundary.geojson"
OUT = Path("data") / "_campus_network_candidate.geojson"

with open(BOUND, encoding="utf-8") as f:
    boundary = shape(json.load(f)["features"][0]["geometry"])

# ── 1. 裁剪现有路网 ──────────────────────────────────────
print("[1/3] clipping existing network to campus boundary...", flush=True)
existing = gpd.read_file(CELL)
clipped_lines = []
for g in existing.geometry:
    if g is None or g.is_empty:
        continue
    geoms = list(g.geoms) if g.geom_type == "MultiLineString" else [g]
    for line in geoms:
        inter = line.intersection(boundary)
        if inter.is_empty:
            continue  # 完全在校外
        if inter.geom_type == "LineString":
            clipped_lines.append(inter)
        elif inter.geom_type == "MultiLineString":
            clipped_lines.extend(list(inter.geoms))
print(f"  after clip: {len(clipped_lines)} lines (orig 1301)", flush=True)

# ── 2. 补充 OSM 缺失段 ───────────────────────────────────
print("[2/3] finding missing OSM walkable segments inside campus...", flush=True)
G = ox.graph_from_bbox(bbox=(121.442, 31.027, 121.469, 31.042), network_type="all")
gdf_edges = ox.graph_to_gdfs(G, nodes=False)

WALKABLE = {
    "footway", "path", "pedestrian", "steps", "track", "service",
    "residential", "living_street", "unclassified", "tertiary", "secondary",
    "primary", "cycleway", "bridleway",
}

# 用裁剪后的路网做覆盖缓冲
TOL = 0.00006
covered_buf = unary_union([l.buffer(TOL) for l in clipped_lines])

def coverage(line):
    L = line.length
    if L <= 0:
        return 1.0
    n = max(8, int(L / 0.00002) + 1)
    hits = 0
    for i in range(n + 1):
        if covered_buf.contains(line.interpolate(i / n, normalized=True)):
            hits += 1
    return hits / (n + 1)

# 收集所有候选 + 现有段，统一归一化去重
# 用端点 pair 作去重键
def line_key(line):
    c = list(line.coords)
    a = (round(c[0][0], 6), round(c[0][1], 6))
    b = (round(c[-1][0], 6), round(c[-1][1], 6))
    return tuple(sorted((a, b)))

all_lines = []
seen = set()

# 先加裁剪后的现有段
for line in clipped_lines:
    k = line_key(line)
    if k in seen:
        continue
    seen.add(k)
    all_lines.append(line)

added_osm = 0
for _, row in gdf_edges.iterrows():
    g = row.geometry
    if g is None or g.is_empty:
        continue
    geoms = list(g.geoms) if g.geom_type == "MultiLineString" else [g]
    hw = row.get("highway")
    hw_set = set(hw) if isinstance(hw, (list, tuple, set)) else {hw}
    if not (hw_set & WALKABLE):
        continue
    for line in geoms:
        mid = line.interpolate(0.5, normalized=True)
        if not boundary.contains(mid):
            continue
        k = line_key(line)
        if k in seen:
            continue
        if coverage(line) >= 0.85:
            continue
        seen.add(k)
        all_lines.append(line)
        added_osm += 1

print(f"  added OSM missing segments: {added_osm}", flush=True)
print(f"  total candidate lines: {len(all_lines)}", flush=True)

# ── 3. 写出 ─────────────────────────────────────────────
print("[3/3] writing candidate geojson...", flush=True)
features = []
for line in all_lines:
    features.append({
        "type": "Feature",
        "properties": {"h": "f"},
        "geometry": mapping(line),
    })
OUT.write_text(json.dumps({"type": "FeatureCollection", "features": features}, ensure_ascii=False), encoding="utf-8")
print(f"[OK] {OUT}  {len(features)} features", flush=True)
