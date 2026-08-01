"""
E刻校园 (ECNU Walk) - 后端服务
Flask API: 地点搜索 + 步行路径规划
启动: python app.py
"""

import osmnx as ox
import networkx as nx
import json
import sys
import io
import os
import math
from pathlib import Path
from flask import Flask, request, jsonify, send_from_directory
from shapely.geometry import Point, LineString

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

app = Flask(__name__, static_folder="static")

# ── 全局变量：启动时加载 ──────────────────────────────────
G = None          # 路网图 (networkx MultiDiGraph)
places = []       # 校园地点列表

# ── 初始化 ────────────────────────────────────────────────

def init():
    """加载路网图和地点数据"""
    global G, places

    print("=" * 50)
    print("E刻校园 - 后端初始化")
    print("=" * 50)

    # 加载路网图
    graph_path = Path("data/ecnu_walk_merged.graphml")
    print(f"\n[1/2] 加载路网图: {graph_path}")
    G = ox.load_graphml(graph_path)
    if "crs" not in G.graph:
        G.graph["crs"] = "EPSG:4326"
    print(f"      节点: {G.number_of_nodes()}, 边: {G.number_of_edges()}")

    # 加载地点数据
    places_path = Path("data/campus_places.json")
    print(f"[2/2] 加载地点数据: {places_path}")
    with open(places_path, "r", encoding="utf-8") as f:
        places = json.load(f)
    print(f"      地点数: {len(places)}")

    print(f"\n[OK] 初始化完成，等待请求...\n")

# ── API: 搜索地点 ─────────────────────────────────────────

@app.route("/api/search")
def search():
    """根据关键词搜索校园地点"""
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify({"results": places})  # 无关键词返回全部

    results = []
    for p in places:
        # 搜索名称和关键词列表
        score = 0
        if q in p["name"]:
            score = 10  # 名称匹配优先
        for kw in p["keywords"]:
            if q in kw:
                score = max(score, 5)  # 关键词匹配
            elif kw in q:
                score = max(score, 3)  # 反向匹配（输入更长）
        if score > 0:
            p_copy = dict(p)
            p_copy["_score"] = score
            results.append(p_copy)

    # 按匹配度排序
    results.sort(key=lambda x: x["_score"], reverse=True)

    # 只返回前 8 个结果
    return jsonify({"results": results[:8], "query": q})

# ── API: 计算路径 ─────────────────────────────────────────

@app.route("/api/route")
def route():
    """计算从起点到终点的最短步行路径"""
    try:
        from_lat = float(request.args.get("from_lat"))
        from_lng = float(request.args.get("from_lng"))
        to_lat   = float(request.args.get("to_lat"))
        to_lng   = float(request.args.get("to_lng"))
    except (TypeError, ValueError):
        return jsonify({"error": "缺少或无效的坐标参数"}), 400

    # 1. 投影到最近路段
    def snap(lng, lat):
        """找到最近路段，将点投影到路上，返回 (投影lat, 投影lng), 入口节点"""
        pt = Point(lng, lat)
        best_dist = float("inf")
        best_proj = None
        best_u = best_v = None

        for u, v, data in G.edges(data=True):
            geom = data.get("geometry")
            if geom is None:
                ux, uy = G.nodes[u]["x"], G.nodes[u]["y"]
                vx, vy = G.nodes[v]["x"], G.nodes[v]["y"]
                geom = LineString([(ux, uy), (vx, vy)])

            dist = geom.distance(pt)
            if dist < best_dist:
                best_dist = dist
                proj_pt = geom.interpolate(geom.project(pt))
                best_proj = (proj_pt.y, proj_pt.x)  # (lat, lng)
                best_u, best_v = u, v

        if best_proj is None:
            node = ox.distance.nearest_nodes(G, lng, lat)
            return (G.nodes[node]["y"], G.nodes[node]["x"]), node

        # 选最近的端点
        dy_u = G.nodes[best_u]["y"] - best_proj[0]
        dx_u = G.nodes[best_u]["x"] - best_proj[1]
        dy_v = G.nodes[best_v]["y"] - best_proj[0]
        dx_v = G.nodes[best_v]["x"] - best_proj[1]
        entry = best_u if (dy_u**2 + dx_u**2) < (dy_v**2 + dx_v**2) else best_v

        return best_proj, entry

    from_proj, from_entry = snap(from_lng, from_lat)
    to_proj, to_entry = snap(to_lng, to_lat)

    if from_entry == to_entry:
        return jsonify({"error": "起点和终点太近"}), 400

    # 2. 最短路径（先有向图，失败回退无向图）
    path_nodes = None
    total_length = 0.0
    route_coords = [[from_proj[0], from_proj[1]]]

    try:
        path_nodes = nx.shortest_path(G, from_entry, to_entry, weight="length")
    except nx.NetworkXNoPath:
        # 回退无向图
        UG = G.to_undirected()
        try:
            path_nodes = nx.shortest_path(UG, from_entry, to_entry, weight="length")
        except nx.NetworkXNoPath:
            return jsonify({"error": "找不到可达路径"}), 404

    # 投影点到入口节点
    dy = (G.nodes[from_entry]["y"] - from_proj[0]) * 111320
    dx = (G.nodes[from_entry]["x"] - from_proj[1]) * 111320 * math.cos(math.radians(from_proj[0]))
    total_length += math.sqrt(dx**2 + dy**2)

    # 3. 重建路径：选最小权重边（修复并行边不确定问题）
    for i in range(len(path_nodes) - 1):
        u, v = path_nodes[i], path_nodes[i + 1]
        edge_data = G.get_edge_data(u, v)
        if edge_data is None:
            # 无向图路径中可能存在反向边
            edge_data = G.get_edge_data(v, u)
        if edge_data is None:
            continue

        # 选长度最短的并行边
        best = min(edge_data.values(), key=lambda d: d.get("length", float("inf")))
        total_length += best.get("length", 0)

        geom = best.get("geometry")
        if geom:
            for c in geom.coords:
                route_coords.append([c[1], c[0]])
        else:
            route_coords.append([G.nodes[u]["y"], G.nodes[u]["x"]])
            if i == len(path_nodes) - 2:
                route_coords.append([G.nodes[v]["y"], G.nodes[v]["x"]])

    # 出口投影
    dy = (to_proj[0] - G.nodes[to_entry]["y"]) * 111320
    dx = (to_proj[1] - G.nodes[to_entry]["x"]) * 111320 * math.cos(math.radians(to_proj[0]))
    total_length += math.sqrt(dx**2 + dy**2)
    route_coords.append([to_proj[0], to_proj[1]])

    # 去重
    deduped = []
    for c in route_coords:
        if not deduped or c != deduped[-1]:
            deduped.append(c)

    # 4. 响应
    walk_speed_mps = 1.2
    return jsonify({
        "route": {
            "type": "LineString",
            "coordinates": [[c[1], c[0]] for c in deduped]
        },
        "distance_m": round(total_length, 1),
        "distance_km": round(total_length / 1000, 2),
        "time_min": round(total_length / walk_speed_mps / 60, 1),
        "nodes_count": len(path_nodes)
    })

# ── API: 地点列表 ─────────────────────────────────────────

@app.route("/api/places")
def list_places():
    """返回所有地点"""
    return jsonify({"places": places})

# ── API: 更新地点坐标 ─────────────────────────────────────

def _valid_coord(lat, lng):
    """验证坐标在合理范围"""
    try:
        lat, lng = float(lat), float(lng)
        return -90 <= lat <= 90 and -180 <= lng <= 180
    except (ValueError, TypeError):
        return False

@app.route("/api/places/<int:place_id>/move", methods=["POST"])
def move_place(place_id):
    """拖拽标记更新地点坐标"""
    data = request.get_json()
    lat = data.get("lat")
    lng = data.get("lng")

    if not _valid_coord(lat, lng):
        return jsonify({"error": "无效坐标"}), 400

    for p in places:
        if p["id"] == place_id:
            p["lat"] = float(lat)
            p["lng"] = float(lng)
            _save_places()
            return jsonify({"ok": True, "place": p})

    return jsonify({"error": "地点不存在"}), 404

# ── API: 新增地点 ─────────────────────────────────────────

@app.route("/api/places", methods=["POST"])
def add_place():
    """新增校园地点"""
    data = request.get_json()
    name = data.get("name", "").strip()
    lat = data.get("lat")
    lng = data.get("lng")

    if not name or not _valid_coord(lat, lng):
        return jsonify({"error": "名称和有效坐标不能为空"}), 400

    new_id = max(p["id"] for p in places) + 1 if places else 1
    new_place = {
        "id": new_id,
        "name": name,
        "keywords": data.get("keywords", []) or [name],
        "lat": float(lat),
        "lng": float(lng),
        "detail": data.get("detail", "")
    }
    places.append(new_place)
    _save_places()
    return jsonify({"ok": True, "place": new_place}), 201

# ── API: 更新地点全部信息 ─────────────────────────────────

@app.route("/api/places/<int:place_id>", methods=["PUT"])
def update_place(place_id):
    """更新地点全部字段"""
    data = request.get_json()
    for p in places:
        if p["id"] == place_id:
            if "name" in data: p["name"] = data["name"].strip()
            if "keywords" in data: p["keywords"] = data["keywords"]
            if "detail" in data: p["detail"] = data["detail"]
            if "lat" in data and "lng" in data:
                if _valid_coord(data["lat"], data["lng"]):
                    p["lat"] = float(data["lat"])
                    p["lng"] = float(data["lng"])
            _save_places()
            return jsonify({"ok": True, "place": p})
    return jsonify({"error": "地点不存在"}), 404

# ── API: 删除地点 ─────────────────────────────────────────

@app.route("/api/places/<int:place_id>", methods=["DELETE"])
def delete_place(place_id):
    """删除地点"""
    global places
    before = len(places)
    places = [p for p in places if p["id"] != place_id]
    if len(places) == before:
        return jsonify({"error": "地点不存在"}), 404
    _save_places()
    return jsonify({"ok": True})

# ── 持久化保存 ────────────────────────────────────────────

def _save_places():
    """原子写入：先写临时文件再替换，防止数据损坏"""
    tmp = "data/campus_places.json.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(places, f, ensure_ascii=False, indent=2)
    os.replace(tmp, "data/campus_places.json")

# ── API: 健康检查 ─────────────────────────────────────────

@app.route("/api/health")
def health():
    return jsonify({
        "status": "ok",
        "nodes": G.number_of_nodes(),
        "edges": G.number_of_edges(),
        "places": len(places)
    })

# ── 离线瓦片 ──────────────────────────────────────────────

@app.route("/tiles/<int:z>/<int:x>/<int:y>.png")
def serve_tile(z, x, y):
    """仅返回预下载的本地瓦片，无远程备用"""
    tile_dir = Path(f"static/tiles/{z}/{x}")
    tile_dir.mkdir(parents=True, exist_ok=True)
    return send_from_directory(tile_dir, f"{y}.png")

# ── 前端静态文件 ──────────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory("static", "index.html")

@app.route("/<path:path>")
def static_files(path):
    return send_from_directory("static", path)

@app.route("/data/<path:path>")
def data_files(path):
    return send_from_directory("data", path)

# ── 启动 ──────────────────────────────────────────────────

if __name__ == "__main__":
    init()
    print("启动服务器: http://127.0.0.1:5000\n")
    app.run(host="0.0.0.0", port=5000, debug=False)
