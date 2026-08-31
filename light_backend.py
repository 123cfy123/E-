"""
E刻校园 轻量桌面后端
只依赖 flask + networkx + shapely（无 osmnx / geopandas / gdal / pandas）
提供与 app.py 相同的 API 接口。
启动后由 pywebview 加载。
"""
import json
import math
import os
import sys
from pathlib import Path

import networkx as nx
from flask import Flask, request, jsonify, send_from_directory
from shapely.geometry import Point, LineString
from shapely import wkt

# ── 路径定位（兼容 PyInstaller 资源打包） ─────────────────
if getattr(sys, "frozen", False):
    _BASE = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
else:
    _BASE = Path(__file__).parent

DATA_DIR = _BASE / "data"
STATIC_DIR = _BASE / "static"

app = Flask(__name__, static_folder=None)

# ── 全局 ─────────────────────────────────────────────────
G = None
places = []


def _load_graphml(path):
    """用 networkx 读 GraphML，几何用 shapely 解析 WKT，长度/坐标转 float。"""
    g = nx.read_graphml(path)
    for _, d in g.nodes(data=True):
        if "x" in d:
            d["x"] = float(d["x"])
        if "y" in d:
            d["y"] = float(d["y"])
    for _, _, d in g.edges(data=True):
        if "geometry" in d and d["geometry"]:
            try:
                d["geometry"] = wkt.loads(d["geometry"])
            except Exception:
                d["geometry"] = None
        if "length" in d:
            try:
                d["length"] = float(d["length"])
            except Exception:
                d["length"] = 0.0
    if "crs" not in g.graph:
        g.graph["crs"] = "EPSG:4326"
    return g


def init():
    global G, places
    graph_path = DATA_DIR / "ecnu_walk_merged.graphml"
    print(f"[1/2] 加载路网: {graph_path}")
    G = _load_graphml(graph_path)
    print(f"      节点: {G.number_of_nodes()}, 边: {G.number_of_edges()}")

    places_path = DATA_DIR / "campus_places.json"
    print(f"[2/2] 加载地点: {places_path}")
    with open(places_path, "r", encoding="utf-8") as f:
        places = json.load(f)
    print(f"      地点: {len(places)}")
    print("[OK] 轻量后端初始化完成")


# ── API: 搜索 ───────────────────────────────────────────
@app.route("/api/search")
def search():
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify({"results": places})
    results = []
    for p in places:
        score = 0
        if q in p["name"]:
            score = 10
        for kw in p.get("keywords", []):
            if q in kw:
                score = max(score, 5)
            elif kw in q:
                score = max(score, 3)
        if score > 0:
            pc = dict(p)
            pc["_score"] = score
            results.append(pc)
    results.sort(key=lambda x: x["_score"], reverse=True)
    return jsonify({"results": results[:8], "query": q})


# ── API: 路由 ───────────────────────────────────────────
@app.route("/api/route")
def route():
    try:
        from_lat = float(request.args.get("from_lat"))
        from_lng = float(request.args.get("from_lng"))
        to_lat = float(request.args.get("to_lat"))
        to_lng = float(request.args.get("to_lng"))
    except (TypeError, ValueError):
        return jsonify({"error": "缺少或无效的坐标参数"}), 400

    def snap(lng, lat):
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
                best_proj = (proj_pt.y, proj_pt.x)
                best_u, best_v = u, v
        if best_proj is None:
            # 回退：取最近的节点
            node = min(G.nodes, key=lambda n_: (G.nodes[n_]["x"] - lng) ** 2 + (G.nodes[n_]["y"] - lat) ** 2)
            return (G.nodes[node]["y"], G.nodes[node]["x"]), node
        dy_u = G.nodes[best_u]["y"] - best_proj[0]
        dx_u = G.nodes[best_u]["x"] - best_proj[1]
        dy_v = G.nodes[best_v]["y"] - best_proj[0]
        dx_v = G.nodes[best_v]["x"] - best_proj[1]
        entry = best_u if (dy_u ** 2 + dx_u ** 2) < (dy_v ** 2 + dx_v ** 2) else best_v
        return best_proj, entry

    from_proj, from_entry = snap(from_lng, from_lat)
    to_proj, to_entry = snap(to_lng, to_lat)
    if from_entry == to_entry:
        return jsonify({"error": "起点和终点太近"}), 400

    path_nodes = None
    total_length = 0.0
    route_coords = [[from_proj[0], from_proj[1]]]
    try:
        path_nodes = nx.shortest_path(G, from_entry, to_entry, weight="length")
    except nx.NetworkXNoPath:
        UG = G.to_undirected()
        try:
            path_nodes = nx.shortest_path(UG, from_entry, to_entry, weight="length")
        except nx.NetworkXNoPath:
            return jsonify({"error": "找不到可达路径"}), 404

    dy = (G.nodes[from_entry]["y"] - from_proj[0]) * 111320
    dx = (G.nodes[from_entry]["x"] - from_proj[1]) * 111320 * math.cos(math.radians(from_proj[0]))
    total_length += math.sqrt(dx ** 2 + dy ** 2)

    for i in range(len(path_nodes) - 1):
        u, v = path_nodes[i], path_nodes[i + 1]
        edge_data = G.get_edge_data(u, v)
        if edge_data is None:
            edge_data = G.get_edge_data(v, u)
        if edge_data is None:
            continue
        # 兼容两种情况：
        #   1) MultiDiGraph: {key: {length, geometry, ...}}
        #   2) DiGraph 单边: {length, geometry, ...}
        if "length" in edge_data:
            best = edge_data
        else:
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

    dy = (to_proj[0] - G.nodes[to_entry]["y"]) * 111320
    dx = (to_proj[1] - G.nodes[to_entry]["x"]) * 111320 * math.cos(math.radians(to_proj[0]))
    total_length += math.sqrt(dx ** 2 + dy ** 2)
    route_coords.append([to_proj[0], to_proj[1]])

    deduped = []
    for c in route_coords:
        if not deduped or c != deduped[-1]:
            deduped.append(c)

    walk_speed = 1.2
    return jsonify({
        "route": {"type": "LineString", "coordinates": [[c[1], c[0]] for c in deduped]},
        "distance_m": round(total_length, 1),
        "distance_km": round(total_length / 1000, 2),
        "time_min": round(total_length / walk_speed / 60, 1),
        "nodes_count": len(path_nodes),
    })


# ── API: 地点 ───────────────────────────────────────────
@app.route("/api/places")
def list_places():
    return jsonify({"places": places})


def _valid_coord(lat, lng):
    try:
        lat, lng = float(lat), float(lng)
        return -90 <= lat <= 90 and -180 <= lng <= 180
    except (ValueError, TypeError):
        return False


@app.route("/api/places/<int:place_id>/move", methods=["POST"])
def move_place(place_id):
    data = request.get_json()
    lat, lng = data.get("lat"), data.get("lng")
    if not _valid_coord(lat, lng):
        return jsonify({"error": "无效坐标"}), 400
    for p in places:
        if p["id"] == place_id:
            p["lat"], p["lng"] = float(lat), float(lng)
            _save_places()
            return jsonify({"ok": True, "place": p})
    return jsonify({"error": "地点不存在"}), 404


@app.route("/api/places", methods=["POST"])
def add_place():
    data = request.get_json()
    name = (data.get("name") or "").strip()
    lat, lng = data.get("lat"), data.get("lng")
    if not name or not _valid_coord(lat, lng):
        return jsonify({"error": "名称和有效坐标不能为空"}), 400
    new_id = max(p["id"] for p in places) + 1 if places else 1
    new_place = {
        "id": new_id, "name": name,
        "keywords": data.get("keywords", []) or [name],
        "lat": float(lat), "lng": float(lng),
        "detail": data.get("detail", ""),
    }
    places.append(new_place)
    _save_places()
    return jsonify({"ok": True, "place": new_place}), 201


@app.route("/api/places/<int:place_id>", methods=["PUT"])
def update_place(place_id):
    data = request.get_json()
    for p in places:
        if p["id"] == place_id:
            if "name" in data:
                p["name"] = data["name"].strip()
            if "keywords" in data:
                p["keywords"] = data["keywords"]
            if "detail" in data:
                p["detail"] = data["detail"]
            if "lat" in data and "lng" in data and _valid_coord(data["lat"], data["lng"]):
                p["lat"], p["lng"] = float(data["lat"]), float(data["lng"])
            _save_places()
            return jsonify({"ok": True, "place": p})
    return jsonify({"error": "地点不存在"}), 404


@app.route("/api/places/<int:place_id>", methods=["DELETE"])
def delete_place(place_id):
    global places
    before = len(places)
    places = [p for p in places if p["id"] != place_id]
    if len(places) == before:
        return jsonify({"error": "地点不存在"}), 404
    _save_places()
    return jsonify({"ok": True})


def _save_places():
    tmp = DATA_DIR / "campus_places.json.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(places, f, ensure_ascii=False, indent=2)
    os.replace(tmp, DATA_DIR / "campus_places.json")


@app.route("/api/health")
def health():
    return jsonify({
        "status": "ok",
        "nodes": G.number_of_nodes(),
        "edges": G.number_of_edges(),
        "places": len(places),
    })


# ── 静态文件 ────────────────────────────────────────────
@app.route("/")
def index():
    return send_from_directory(STATIC_DIR, "index.html")


@app.route("/<path:path>")
def static_files(path):
    return send_from_directory(STATIC_DIR, path)


@app.route("/data/<path:path>")
def data_files(path):
    return send_from_directory(DATA_DIR, path)


@app.route("/tiles/<int:z>/<int:x>/<int:y>.png")
def serve_tile(z, x, y):
    tile_dir = STATIC_DIR / "tiles" / str(z) / str(x)
    return send_from_directory(tile_dir, f"{y}.png")


if __name__ == "__main__":
    init()
    print("轻量后端: http://127.0.0.1:5000")
    app.run(host="0.0.0.0", port=5000, debug=False, use_reloader=False)
