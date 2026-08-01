"""
第五步：从编辑后的 GeoJSON 重建路由图
用法：在 QGIS 中编辑 data/ecnu_edges_merged.geojson 后运行
"""

import osmnx as ox, networkx as nx, math, sys, io
import geopandas as gpd
from shapely.ops import unary_union
from shapely.geometry import LineString, MultiLineString

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

print("Rebuilding routable graph from edited edges...")

edges = gpd.read_file("data/ecnu_edges_merged.geojson")
print(f"  Loaded {len(edges)} edges")

# Extract lines
lines = []
for _, row in edges.iterrows():
    g = row.geometry
    if g and not g.is_empty:
        if g.geom_type == "LineString":
            lines.append(g)
        elif g.geom_type == "MultiLineString":
            lines.extend(list(g.geoms))

# Split at intersections
merged = unary_union(lines)
if merged.geom_type == "MultiLineString":
    split = list(merged.geoms)
else:
    split = [merged]

print(f"  {len(split)} segments after splitting intersections")

# Build bidirectional graph
node_index = {}
G = nx.MultiDiGraph()
nid = 0

for line in split:
    coords = list(line.coords)
    s = (round(coords[0][0], 7), round(coords[0][1], 7))
    e = (round(coords[-1][0], 7), round(coords[-1][1], 7))

    for pt in [s, e]:
        if pt not in node_index:
            node_index[pt] = nid
            G.add_node(nid, x=pt[0], y=pt[1])
            nid += 1

    u, v = node_index[s], node_index[e]

    length = 0
    for i in range(len(coords) - 1):
        dx = (coords[i+1][0] - coords[i][0]) * 111320 * 0.85
        dy = (coords[i+1][1] - coords[i][1]) * 111320
        length += math.sqrt(dx**2 + dy**2)

    G.add_edge(u, v, length=length, geometry=line, highway="footway")
    G.add_edge(v, u, length=length,
               geometry=LineString(list(reversed(coords))), highway="footway")

G.graph["crs"] = "EPSG:4326"

# Prune small components
UG = G.to_undirected()
comps = list(nx.connected_components(UG))
largest = max(comps, key=len)
if len(comps) > 1:
    remove = set(G.nodes()) - largest
    G.remove_nodes_from(remove)
    print(f"  Removed {len(comps)-1} isolated components")

print(f"  Graph: {G.number_of_nodes()} nodes, {G.number_of_edges()} edges")

ox.save_graphml(G, "data/ecnu_walk_merged.graphml")
print(f"  [OK] data/ecnu_walk_merged.graphml")

# Also update GeoJSON with split version
gdf = gpd.GeoDataFrame({"geometry": split, "highway": "footway"}, crs="EPSG:4326")
gdf.to_file("data/ecnu_edges_merged.geojson", driver="GeoJSON")
print(f"  [OK] data/ecnu_edges_merged.geojson ({len(gdf)} edges)")

print(f"\nDone! Restart server to apply changes.")
