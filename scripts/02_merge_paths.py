"""
第二步：将 QGIS 手动绘制的缺失路径合并入主路网
用法：
  1. 在 QGIS 中绘制缺失路径
  2. 导出为 data/manual_paths.geojson
  3. 运行: python scripts/02_merge_paths.py
"""

import osmnx as ox
import geopandas as gpd
import pandas as pd
import networkx as nx
import json
import sys
import io
from pathlib import Path

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ox.settings.log_console = True
ox.settings.use_cache = True

DATA_DIR = Path("data")
ORIG_EDGES = DATA_DIR / "ecnu_edges.geojson"
ORIG_NODES = DATA_DIR / "ecnu_nodes.geojson"
ORIG_GRAPHML = DATA_DIR / "ecnu_walk.graphml"
MANUAL_PATHS = DATA_DIR / "manual_paths.geojson"
MERGED_EDGES = DATA_DIR / "ecnu_edges_merged.geojson"
MERGED_GRAPHML = DATA_DIR / "ecnu_walk_merged.graphml"

print("=" * 60)
print("Merge Manual Paths into Road Network")
print("=" * 60)

# ── 1. 加载原始数据 ──────────────────────────────────────
print("\n[1/5] Loading original network...")
orig_edges = gpd.read_file(ORIG_EDGES)
print(f"  Original edges: {len(orig_edges)}")

# ── 2. 加载手动绘制的路径 ────────────────────────────────
print("\n[2/5] Loading manual paths...")
if not MANUAL_PATHS.exists():
    print(f"  [ERROR] {MANUAL_PATHS} not found!")
    print(f"\n  Please do the following steps in QGIS first:")
    print(f"  1. Open QGIS")
    print(f"  2. Add satellite basemap (XYZ Tiles -> Bing/Google Satellite)")
    print(f"  3. Drag {ORIG_EDGES} into QGIS")
    print(f"  4. Layer -> Create Layer -> New Temporary Scratch Layer")
    print(f"     - Geometry type: LineString")
    print(f"     - CRS: EPSG:4326 (WGS 84)")
    print(f"  5. Toggle Editing (pencil icon)")
    print(f"  6. Click 'Add Line Feature'")
    print(f"  7. Trace missing paths on satellite imagery")
    print(f"  8. In attribute table, add field 'highway' with value 'footway'")
    print(f"  9. Save as GeoJSON: Right-click layer -> Export -> Save Features As")
    print(f"     -> Format: GeoJSON, File: {MANUAL_PATHS}")
    print(f" 10. Run this script again.")
    sys.exit(1)

manual = gpd.read_file(MANUAL_PATHS)
print(f"  Manual paths:  {len(manual)}")

# 确保 coordinate system 一致
if manual.crs != orig_edges.crs:
    print(f"  Converting CRS: {manual.crs} -> {orig_edges.crs}")
    manual = manual.to_crs(orig_edges.crs)

# ── 3. 补全字段 ──────────────────────────────────────────
print("\n[3/5] Normalizing fields...")

# 原始数据有哪些字段
orig_columns = set(orig_edges.columns)

# 手动路径需要的核心字段
required_fields = {
    "highway": "footway",     # 默认标记为步行道
    "geometry": None,
}

# 给手动路径补充缺失字段
for col in orig_columns:
    if col not in manual.columns and col != "geometry":
        manual[col] = None

# 如果手动路径没有 highway 字段，默认填 footway
if "highway" not in manual.columns:
    manual["highway"] = "footway"
else:
    manual["highway"] = manual["highway"].fillna("footway")

# 确保 geometry 类型一致
manual = manual.set_geometry("geometry")

print(f"  Manual paths tagged as: {manual['highway'].value_counts().to_dict()}")

# ── 4. 合并 ──────────────────────────────────────────────
print("\n[4/5] Merging...")

# 只保留两边都有的列
common_cols = list(orig_columns & set(manual.columns))
if "geometry" not in common_cols:
    common_cols.append("geometry")

merged = pd.concat([
    orig_edges[common_cols],
    manual[common_cols]
], ignore_index=True)

merged = gpd.GeoDataFrame(merged, geometry="geometry", crs=orig_edges.crs)

print(f"  Original: {len(orig_edges)} edges")
print(f"  Added:    {len(manual)} edges")
print(f"  Merged:   {len(merged)} edges")

# ── 5. 保存 ──────────────────────────────────────────────
print("\n[5/5] Saving...")

# 保存合并后的 GeoJSON
merged.to_file(MERGED_EDGES, driver="GeoJSON")
print(f"  [OK] {MERGED_EDGES}")

# 同时重建可路由的 networkx 图
print(f"  Building routable graph...")

# 方案：用 osmnx 从合并后的 GeoDataFrame 创建图
# 先转换回 osmnx 能识别的格式
try:
    # 使用 osmnx 的 graph_from_gdfs
    G_merged = ox.graph_from_gdfs(
        gpd.GeoDataFrame(geometry=gpd.points_from_xy(
            merged.geometry.apply(lambda g: g.centroid.x if g else None),
            merged.geometry.apply(lambda g: g.centroid.y if g else None)
        ), crs=merged.crs),
        merged
    )
    print(f"  [WARN] graph_from_gdfs may not be ideal for this. Using alternative...")
except Exception:
    pass

# 更好的方法：从原始图开始，手动添加边
print(f"  Loading original graph from GraphML...")
G = ox.load_graphml(ORIG_GRAPHML)

# 对于每条手动路径，找到最近的图节点，添加新边
print(f"  Adding {len(manual)} manual paths to graph...")
import numpy as np

nodes_gdf, edges_gdf = ox.graph_to_gdfs(G)
nodes_xy = np.array([(p.x, p.y) for p in nodes_gdf.geometry])

added_edges = 0
for _, row in manual.iterrows():
    if row.geometry is None or row.geometry.is_empty:
        continue

    coords = list(row.geometry.coords)

    # 为路径的每个端点找到最近的路网节点
    for i in range(len(coords) - 1):
        p1 = coords[i]
        p2 = coords[i + 1]

        # 找最近的路网节点
        dist1 = np.sqrt((nodes_xy[:, 0] - p1[0])**2 + (nodes_xy[:, 1] - p1[1])**2)
        dist2 = np.sqrt((nodes_xy[:, 0] - p2[0])**2 + (nodes_xy[:, 1] - p2[1])**2)

        n1 = nodes_gdf.index[dist1.argmin()]
        n2 = nodes_gdf.index[dist2.argmin()]

        # 如果两个端点映射到同一节点，跳过
        if n1 == n2:
            continue

        # 计算距离 (度 -> 近似米)
        dx = (p2[0] - p1[0]) * 111320 * 0.85  # cos(lat)
        dy = (p2[1] - p1[1]) * 111320
        length = np.sqrt(dx**2 + dy**2)

        # 添加边
        G.add_edge(n1, n2, length=length, highway="footway", manual=True)
        added_edges += 1

print(f"  Added {added_edges} edges to graph")

ox.save_graphml(G, MERGED_GRAPHML)
print(f"  [OK] {MERGED_GRAPHML}")

# ── 汇总 ──────────────────────────────────────────────────
print(f"\n{'=' * 60}")
print(f"Merge Complete!")
print(f"{'=' * 60}")
print(f"  Original edges: {len(orig_edges)}")
print(f"  Manual paths:   {len(manual)}")
print(f"  Merged total:   {len(merged)}")
print(f"  Graph edges:    {G.number_of_edges()}")
print(f"\nFiles ready:")
print(f"  {MERGED_EDGES}  <- For Leaflet/QGIS")
print(f"  {MERGED_GRAPHML}  <- For routing engine")
print(f"\nNext step: python scripts/03_backend.py")
