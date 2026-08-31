"""
检查候选路网的连通性：
- 统计连通分量（应尽量连通、无大量孤立小团）
- 统计"断头"节点（只有1条相邻边 = 悬空点）
"""
import sys, io, json
from pathlib import Path

import geopandas as gpd
import networkx as nx
from shapely.geometry import LineString, shape
from shapely.ops import unary_union

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

CAND = Path("data") / "_campus_network_candidate.geojson"

cand = gpd.read_file(CAND)
lines = [l for l in cand.geometry if l and not l.is_empty]
print(f"features: {len(lines)}", flush=True)

# 在交点处拆分（union 会产生节点，转换成 graph）
merged = unary_union(lines)
segs = list(merged.geoms) if merged.geom_type == "MultiLineString" else [merged]
print(f"after split at intersections: {len(segs)}", flush=True)

G = nx.Graph()
for seg in segs:
    coords = list(seg.coords)
    for i in range(len(coords) - 1):
        a = (round(coords[i][0], 7), round(coords[i][1], 7))
        b = (round(coords[i+1][0], 7), round(coords[i+1][1], 7))
        G.add_edge(a, b)

print(f"nodes: {G.number_of_nodes()}, edges: {G.number_of_edges()}", flush=True)

# 连通分量
comps = sorted(nx.connected_components(G), key=len, reverse=True)
largest = len(comps[0])
print(f"connected components: {len(comps)}", flush=True)
print(f"largest comp size: {largest} ({largest/G.number_of_nodes()*100:.1f}% of nodes)", flush=True)
print(f"components >=10 nodes: {sum(1 for c in comps if len(c)>=10)}", flush=True)
print(f"in components <10 nodes: {sum(1 for c in comps if len(c)<10)}", flush=True)

# 断头点
deg1 = sum(1 for n in G.nodes if G.degree(n) == 1)
print(f"degree-1 (dead-end) nodes: {deg1}", flush=True)
