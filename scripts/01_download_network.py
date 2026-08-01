"""
第一步：下载华东师大闵行校区步行路网并验证数据质量
用法：cd ECNU-Walk && python scripts/01_download_network.py
"""

import osmnx as ox
import folium
import json
import sys
import io
from pathlib import Path

# 解决 Windows GBK 控制台编码问题
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# 配置 ox 输出
ox.settings.log_console = True
ox.settings.use_cache = True

# ── 1. 定义闵行校区范围 ──────────────────────────────────
# 华东师大闵行校区大致四至边界
# 北: 剑川路以北 ~31.042
# 南: 东川路 ~31.027
# 西: 莲花南路 ~121.442
# 东: 虹梅南路 ~121.469
NORTH = 31.042
SOUTH = 31.027
WEST = 121.442
EAST = 121.469

print("=" * 60)
print("ECNU Minhang Campus - Walk Network Download")
print("=" * 60)
print(f"\nBounding Box: {WEST:.4f}E, {SOUTH:.4f}N -> {EAST:.4f}E, {NORTH:.4f}N")
print(f"Approx 1.7km x 2.7km area")

# ── 2. 下载步行路网 ──────────────────────────────────────
print("\n[1/4] Downloading walk network from OpenStreetMap...")
print("      (first time may take 30-120 seconds)")

# osmnx 2.x API: bbox=(left, bottom, right, top)
G_walk = ox.graph_from_bbox(
    bbox=(WEST, SOUTH, EAST, NORTH),
    network_type="walk"
)
print(f"      [OK] Download successful!")

# ── 3. 路网统计 ──────────────────────────────────────────
print("\n[2/4] Network statistics:")

nodes, edges = ox.graph_to_gdfs(G_walk)

# 转换为 WGS84 (EPSG:4326)
nodes_wgs = nodes.to_crs("EPSG:4326")
edges_wgs = edges.to_crs("EPSG:4326")

# 基本统计
n_nodes = len(nodes_wgs)
n_edges = len(edges_wgs)

# 计算总长度 (米) - 先投影到本地坐标
G_proj = ox.project_graph(G_walk)
edges_proj = ox.graph_to_gdfs(G_proj, nodes=False)
total_length_m = edges_proj["length"].sum()
total_length_km = total_length_m / 1000

# 检查连通性 (osmnx 2.x returns dict: {count: num_nodes})
street_counts = ox.stats.streets_per_node_counts(G_walk)
isolated = street_counts.get(1, 0)  # nodes with only 1 incident street

print(f"  Nodes (intersections):  {n_nodes}")
print(f"  Edges (road segments):  {n_edges}")
print(f"  Total walk network:     {total_length_km:.1f} km")
print(f"  Dead-ends (degree=1):   {isolated}")

# 道路类型分布
print(f"\n  Road type distribution:")
road_types = edges_wgs["highway"].value_counts()
for rt, count in road_types.head(10).items():
    bar = "#" * int(count / road_types.max() * 20)
    print(f"    {rt:<20s} {count:>4d} {bar}")

# ── 4. 导出数据 ──────────────────────────────────────────
print("\n[3/4] Saving data...")

data_dir = Path("data")
data_dir.mkdir(exist_ok=True)

# 保存为 GraphML
graphml_path = data_dir / "ecnu_walk.graphml"
ox.save_graphml(G_walk, graphml_path)
print(f"  [OK] GraphML:   {graphml_path}")

# 保存为 GeoJSON
edges_geojson_path = data_dir / "ecnu_edges.geojson"
edges_wgs.to_file(edges_geojson_path, driver="GeoJSON")
print(f"  [OK] Edges:     {edges_geojson_path}")

nodes_geojson_path = data_dir / "ecnu_nodes.geojson"
nodes_wgs.to_file(nodes_geojson_path, driver="GeoJSON")
print(f"  [OK] Nodes:     {nodes_geojson_path}")

# 保存范围信息
bbox_path = data_dir / "campus_bbox.json"
with open(bbox_path, "w", encoding="utf-8") as f:
    json.dump({
        "north": NORTH, "south": SOUTH,
        "west": WEST, "east": EAST,
        "center": [(NORTH + SOUTH) / 2, (WEST + EAST) / 2],
        "zoom": 15,
        "nodes": n_nodes,
        "edges": n_edges,
        "total_length_km": round(total_length_km, 2)
    }, f, ensure_ascii=False, indent=2)
print(f"  [OK] BBox:     {bbox_path}")

# ── 5. 生成可视化地图 ────────────────────────────────────
print("\n[4/4] Generating interactive map...")

center_lat = (NORTH + SOUTH) / 2
center_lng = (WEST + EAST) / 2

m = folium.Map(
    location=[center_lat, center_lng],
    zoom_start=15,
    tiles="OpenStreetMap",
    control_scale=True
)

# 添加范围矩形框
folium.Rectangle(
    bounds=[[SOUTH, WEST], [NORTH, EAST]],
    color="blue", weight=2, fill=False, dash_array="5,5",
    popup="Download BBox"
).add_to(m)

# 将路网边添加到地图（限制数量避免卡顿）
edges_sample = edges_wgs
if len(edges_sample) > 2000:
    print(f"      [WARN] Edge count ({len(edges_sample)}) > 2000, sampling for display")
    edges_sample = edges_sample.sample(2000)

for _, row in edges_sample.iterrows():
    if row.geometry is not None:
        coords = [(y, x) for x, y in row.geometry.coords]
        is_foot = row.get("highway") in ["footway", "path", "pedestrian", "steps"]
        folium.PolyLine(
            coords,
            color="#e74c3c" if is_foot else "#3498db",
            weight=2,
            opacity=0.7
        ).add_to(m)

# 添加校园标注
folium.Marker(
    [center_lat, center_lng],
    popup="ECNU Minhang Campus",
    icon=folium.Icon(color="red", icon="university", prefix="fa")
).add_to(m)

map_path = data_dir / "ecnu_network_map.html"
m.save(map_path)
print(f"  [OK] Map:      {map_path}")
print(f"\n  -> Open data/ecnu_network_map.html in browser to view road network\n")

# ── 6. 质量评估 ──────────────────────────────────────────
print("=" * 60)
print("Data Quality Assessment")
print("=" * 60)

issues = []

# 检查路网密度
# 纬度 1度 ~111.32km, 经度 1度 ~111.32*cos(lat)km
import math
avg_lat = (NORTH + SOUTH) / 2
width_km = (EAST - WEST) * 111.32 * math.cos(math.radians(avg_lat))
height_km = (NORTH - SOUTH) * 111.32
area_km2 = width_km * height_km
density = total_length_km / area_km2 if area_km2 > 0 else 0

print(f"\n  Area:        {area_km2:.2f} km2 ({width_km:.2f} x {height_km:.2f} km)")
print(f"  Density:     {density:.1f} km/km2")

if density < 5:
    issues.append(f"Low density ({density:.1f} km/km2) - campus paths likely incomplete")
elif density < 10:
    issues.append(f"Moderate density ({density:.1f} km/km2) - some paths may be missing")
else:
    print(f"  -> Density looks good")

# 检查步行专用道比例
foot_paths = edges_wgs[edges_wgs["highway"].isin([
    "footway", "path", "pedestrian", "steps", "track", "living_street"
])]
foot_pct = len(foot_paths) / n_edges * 100 if n_edges > 0 else 0
print(f"  Foot paths:  {foot_pct:.1f}%")

if foot_pct < 15:
    issues.append(f"Low foot path ratio ({foot_pct:.1f}%) - campus internal paths likely missing")

print(f"\n{'─' * 60}")
if issues:
    print(f"Issues found ({len(issues)}):")
    for i, issue in enumerate(issues, 1):
        print(f"  {i}. [WARN] {issue}")
    print(f"\n  Recommendation: Open data/ecnu_edges.geojson in QGIS")
    print(f"  and manually add missing campus paths using satellite imagery.")
else:
    print("Road network data quality looks good! Ready for next step.")

print(f"\nNext steps:")
print(f"  1. Review the map: data/ecnu_network_map.html")
print(f"  2. If data needs editing, use QGIS on data/ecnu_edges.geojson")
print(f"  3. Then: python scripts/02_backend_setup.py")
