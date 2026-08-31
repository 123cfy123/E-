"""
生成"校园边界内、现有路网未覆盖"的 OSM 可步行缺失路段 GeoJSON。
- 过滤：段中心必须在校园边界内
- 去重：按端点对归一化，去掉反向重复
- 覆盖：整线覆盖 < 0.85 才算缺
输出: data/_candidate_missing.geojson
"""
import sys, io, json
from pathlib import Path

import osmnx as ox
import geopandas as gpd
from shapely.geometry import LineString, shape, mapping
from shapely.ops import unary_union

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

CELL = Path("data") / "ecnu_edges_merged.geojson"
BOUND = Path("data") / "campus_boundary.geojson"
OUT = Path("data") / "_candidate_missing.geojson"

# 校园边界多边形
with open(BOUND, encoding="utf-8") as f:
    bound_gj = json.load(f)
boundary = shape(bound_gj["features"][0]["geometry"])

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

TOL = 0.00006
existing_buf = unary_union([l.buffer(TOL) for l in existing_lines])

def coverage(line):
    L = line.length
    if L <= 0:
        return 1.0
    n = max(8, int(L / 0.00002) + 1)
    hits = 0
    for i in range(n + 1):
        if existing_buf.contains(line.interpolate(i / n, normalized=True)):
            hits += 1
    return hits / (n + 1)

seen = set()
missing = []
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
        # 必须在校园边界内（中心点在边界内）
        mid = line.interpolate(0.5, normalized=True)
        if not boundary.contains(mid):
            continue
        # 去重：端点为 key（规范化顺序）
        c = list(line.coords)
        key = tuple(sorted(( (round(c[0][0],6),round(c[0][1],6)), (round(c[-1][0],6),round(c[-1][1],6)) )))
        if key in seen:
            continue
        seen.add(key)
        cov = coverage(line)
        if cov < 0.85:
            missing.append(line)

print(f"\n=== RESULT ===", flush=True)
print(f"missing inside campus (deduped): {len(missing)}", flush=True)

# 写 GeoJSON
features = []
for line in missing:
    features.append({
        "type": "Feature",
        "properties": {"h": "f", "src": "osm_add"},
        "geometry": mapping(line),
    })
OUT.write_text(json.dumps({"type": "FeatureCollection", "features": features}, ensure_ascii=False), encoding="utf-8")
print(f"[OK] {OUT}  ({len(features)} features)", flush=True)

# 汇总缺失范围
if missing:
    m = unary_union(missing)
    print(f"missing bbox: {[round(v,5) for v in m.bounds]}", flush=True)
