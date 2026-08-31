"""
最终分析：以"整线覆盖率"判别 OSM 候选路段是否已被现有路网覆盖。
统计缺失路段总数和总长度，并输出疑似真实大缺口的详细坐标。
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

print("downloading OSM all network...", flush=True)
G = ox.graph_from_bbox(bbox=(121.442, 31.027, 121.469, 31.042), network_type="all")
gdf_edges = ox.graph_to_gdfs(G, nodes=False)

WALKABLE = {
    "footway", "path", "pedestrian", "steps", "track", "service",
    "residential", "living_street", "unclassified", "tertiary", "secondary",
    "primary", "cycleway", "bridleway",
}

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

def coverage(line):
    """返回 [0,1]：整线落在现有缓冲内的比例（采样多点）。"""
    L = line.length
    if L <= 0:
        return 1.0
    n = max(8, int(L / 0.00002) + 1)
    hits = 0
    for i in range(n + 1):
        p = line.interpolate(i / n, normalized=True)
        if existing_buf.contains(p):
            hits += 1
    return hits / (n + 1)

missing_segs = []
for line in cand_lines:
    cov = coverage(line)
    if cov < 0.85:  # 覆盖不足 85% 才算缺
        missing_segs.append((line, cov))

missing_segs.sort(key=lambda t: -t[0].length)

total_missing_len = sum(l.length for l, _ in missing_segs)
print(f"\n=== RESULT ===", flush=True)
print(f"candidates           : {len(cand_lines)}", flush=True)
print(f"MISSING (cov<0.85)   : {len(missing_segs)}", flush=True)
print(f"approx missing len(deg): {total_missing_len:.5f}", flush=True)

print("\n--- top missing segments (long) ---", flush=True)
for i, (line, cov) in enumerate(missing_segs[:25]):
    c = list(line.coords)
    print(f"[{i}] cov={cov:.2f} pts={len(c)} start={ (round(c[0][0],5),round(c[0][1],5)) } end={ (round(c[-1][0],5),round(c[-1][1],5)) }", flush=True)
