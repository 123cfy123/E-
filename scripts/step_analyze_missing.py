"""
分析：对比 OSM 步行网与现有路网，找出 OSM 里存在但现有路网未覆盖的缺失路段。
只分析、不写入。输出 count 和统计。
"""
import sys, io, math
from pathlib import Path

import osmnx as ox
import geopandas as gpd
import numpy as np
from shapely.geometry import Point, LineString

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

CELL = Path("data") / "ecnu_edges_merged.geojson"

print("loading existing network...", flush=True)
existing = gpd.read_file(CELL)
# 现有路网线段几何（合并为 shapely 几何列表）
existing_lines = [g for g in existing.geometry if g and not g.is_empty]
print(f"  existing edges: {len(existing_lines)}", flush=True)

# 下载 OSM 步行网
print("downloading OSM walk network...", flush=True)
G = ox.graph_from_bbox(bbox=(121.442, 31.027, 121.469, 31.042), network_type="walk")
gdf_edges = ox.graph_to_gdfs(G, nodes=False)
print(f"  osm edges: {len(gdf_edges)}", flush=True)

# 构建 existing 的缓冲 union，加速覆盖率判断：把现有线段都以固定容差缓冲
TOL_deg = 0.00006  # ~6m 容差(0.00006 deg lat ~ 6.7m)
from shapely.ops import unary_union
print("building existing coverage buffer...", flush=True)
existing_buf = unary_union([l.buffer(TOL_deg) for l in existing_lines])

# 对每条 OSM 边，取其中点，判断是否被 existing 覆盖
osm_lines = []
for _, row in gdf_edges.iterrows():
    g = row.geometry
    if g and not g.is_empty:
        if g.geom_type == "LineString":
            osm_lines.append(g)
        elif g.geom_type == "MultiLineString":
            osm_lines.extend(list(g.geoms))
print(f"  osm linestrings: {len(osm_lines)}", flush=True)

missing = []
covered = 0
for line in osm_lines:
    # 取样中点与几个内点
    pts = [line.interpolate(t, normalized=True) for t in (0.2, 0.5, 0.8)]
    is_covered = all(existing_buf.contains(p) for p in pts)
    if is_covered:
        covered += 1
    else:
        missing.append(line)

print(f"\n=== RESULT ===", flush=True)
print(f"OSM segments tested : {len(osm_lines)}", flush=True)
print(f"already covered     : {covered}", flush=True)
print(f"MISSING (to add)    : {len(missing)}", flush=True)

# 缺失路段的粗略范围
if missing:
    m = unary_union(missing)
    print(f"missing bbox: {m.bounds}", flush=True)
