"""
深入分析：用 OSM all 类型，识别校园内所有"可步行"候选路段，
对比现有路网，找出缺失路段并报告位置 + highway 类型。
只分析不写入。
"""
import sys, io
from pathlib import Path

import osmnx as ox
import geopandas as gpd
from shapely.geometry import LineString
from shapely.ops import unary_union

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

CELL = Path("data") / "ecnu_edges_merged.geojson"

print("loading existing network...", flush=True)
existing = gpd.read_file(CELL)
existing_lines = [g for g in existing.geometry if g and not g.is_empty]
print(f"  existing edges: {len(existing_lines)}", flush=True)

# 用 'all' 下载，涵盖 drive/service/residential/footway/path 等
print("downloading OSM all network...", flush=True)
G = ox.graph_from_bbox(bbox=(121.442, 31.027, 121.469, 31.042), network_type="all")
gdf_edges = ox.graph_to_gdfs(G, nodes=False)

# 定义"可步行"的 highway 类型（白名单）
WALKABLE = {
    "footway", "path", "pedestrian", "steps", "track", "service",
    "residential", "living_street", "unclassified", "tertiary", "secondary",
    "primary", "cycleway", "bridleway",
}
hws = gdf_edges["highway"].astype(str)
# osm edges may have list highway; flatten heuristic
cand_lines = []
for _, row in gdf_edges.iterrows():
    g = row.geometry
    if g is None or g.is_empty:
        continue
    geoms = list(g.geoms) if g.geom_type == "MultiLineString" else [g]
    hw = row.get("highway")
    hw_set = set(hw) if isinstance(hw, (list, tuple, set)) else {hw}
    if not (hw_set & WALKABLE):
        continue
    cand_lines.extend(geoms)

print(f"  osm walkable candidates: {len(cand_lines)}", flush=True)

TOL = 0.00006  # ~6m
existing_buf = unary_union([l.buffer(TOL) for l in existing_lines])

missing = []
for line in cand_lines:
    pts = [line.interpolate(t, normalized=True) for t in (0.2, 0.5, 0.8)]
    if not all(existing_buf.contains(p) for p in pts):
        missing.append(line)

print(f"\n=== RESULT ===", flush=True)
print(f"walkable OSM candidates : {len(cand_lines)}", flush=True)
print(f"MISSING (not covered)   : {len(missing)}", flush=True)

if missing:
    m = unary_union(missing)
    print(f"missing bbox: {[round(v,5) for v in m.bounds]}", flush=True)
    # 输出前几条缺失的坐标
    for i, line in enumerate(missing[:20]):
        c = list(line.coords)
        print(f"  [{i}] {len(c)} pts {[ (round(p[0],5),round(p[1],5)) for p in c[:3] ]}...", flush=True)
