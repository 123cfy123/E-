"""
E刻校园 Qt 原生版 — 零浏览器依赖
纯 Python + PySide6 + 瓦片手工渲染
"""

import sys, math, json
from pathlib import Path
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QLineEdit,
    QListWidget, QListWidgetItem, QLabel, QVBoxLayout, QHBoxLayout,
    QFrame, QPushButton, QSizePolicy)
from PySide6.QtCore import Qt, QPoint, QTimer, QThread, Signal
from PySide6.QtGui import QPainter, QPixmap, QColor, QPen, QFont, QFontMetrics

# ── 配置 ──────────────────────────────────────────────────
TILE_DIR = Path("static/tiles")
DATA_DIR = Path("data")
CENTER_LAT, CENTER_LNG = 31.0345, 121.4555
MIN_ZOOM, MAX_ZOOM = 14, 18
TILE_SIZE = 256

# ── 后端逻辑 ──────────────────────────────────────────────
import osmnx as ox
import networkx as nx
from shapely.geometry import Point, LineString

print("Loading road network...")
G = ox.load_graphml(DATA_DIR / "ecnu_walk_merged.graphml")
if "crs" not in G.graph:
    G.graph["crs"] = "EPSG:4326"
print(f"  {G.number_of_nodes()} nodes, {G.number_of_edges()} edges")

with open(DATA_DIR / "campus_places.json", encoding="utf-8") as f:
    PLACES = json.load(f)
print(f"  {len(PLACES)} places")

# ── 坐标 ──────────────────────────────────────────────────

def latlng_to_tile(lat, lng, zoom):
    n = 2.0 ** zoom
    x = (lng + 180.0) / 360.0 * n
    lat_rad = math.radians(lat)
    y = (1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n
    return x, y

def tile_to_latlng(tx, ty, zoom):
    n = 2.0 ** zoom
    lng = tx / n * 360.0 - 180.0
    lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * ty / n))))
    return lat, lng

# ── 路径计算（后台线程）───────────────────────────────────

class RouteWorker(QThread):
    finished = Signal(object)

    def __init__(self, flat, flng, tlat, tlng):
        super().__init__()
        self.flat, self.flng = flat, flng
        self.tlat, self.tlng = tlat, tlng

    def run(self):
        self.finished.emit(_find_route(self.flat, self.flng, self.tlat, self.tlng))

def _find_route(from_lat, from_lng, to_lat, to_lng):
    def snap(lng, lat):
        pt = Point(lng, lat)
        best_dist, best_proj, best_u, best_v = float("inf"), None, None, None
        for u, v, data in G.edges(data=True):
            geom = data.get("geometry")
            if geom is None:
                ux, uy = G.nodes[u]["x"], G.nodes[u]["y"]
                vx, vy = G.nodes[v]["x"], G.nodes[v]["y"]
                geom = LineString([(ux, uy), (vx, vy)])
            dist = geom.distance(pt)
            if dist < best_dist:
                best_dist = dist
                proj = geom.interpolate(geom.project(pt))
                best_proj = (proj.y, proj.x)
                best_u, best_v = u, v
        if best_proj is None:
            node = ox.distance.nearest_nodes(G, lng, lat)
            return (G.nodes[node]["y"], G.nodes[node]["x"]), node
        du = (G.nodes[best_u]["y"]-best_proj[0])**2 + (G.nodes[best_u]["x"]-best_proj[1])**2
        dv = (G.nodes[best_v]["y"]-best_proj[0])**2 + (G.nodes[best_v]["x"]-best_proj[1])**2
        return best_proj, (best_u if du < dv else best_v)

    from_proj, from_entry = snap(from_lng, from_lat)
    to_proj, to_entry = snap(to_lng, to_lat)
    if from_entry == to_entry:
        return None

    try:
        path = nx.shortest_path(G, from_entry, to_entry, weight="length")
    except nx.NetworkXNoPath:
        try:
            path = nx.shortest_path(G.to_undirected(), from_entry, to_entry, weight="length")
        except nx.NetworkXNoPath:
            return None

    coords, total = [[from_proj[0], from_proj[1]]], 0.0
    al = lambda dy, dx: math.sqrt((dy*111320)**2 + (dx*111320*0.85)**2)
    total += al(G.nodes[from_entry]["y"]-from_proj[0], G.nodes[from_entry]["x"]-from_proj[1])

    for i in range(len(path)-1):
        u, v = path[i], path[i+1]
        ed = G.get_edge_data(u, v) or G.get_edge_data(v, u)
        if not ed: continue
        d = min(ed.values(), key=lambda x: x.get("length", 1e9))
        total += d.get("length", 0)
        g = d.get("geometry")
        if g: coords.extend([[c[1],c[0]] for c in g.coords])
        else:
            coords.append([G.nodes[u]["y"], G.nodes[u]["x"]])
            if i == len(path)-2: coords.append([G.nodes[v]["y"], G.nodes[v]["x"]])

    coords.append([to_proj[0], to_proj[1]])
    total += al(to_proj[0]-G.nodes[to_entry]["y"], to_proj[1]-G.nodes[to_entry]["x"])

    dedup = [coords[0]]
    for c in coords[1:]:
        if c != dedup[-1]: dedup.append(c)

    return {"coordinates": dedup, "distance_m": round(total,1),
            "distance_km": round(total/1000,2), "time_min": round(total/1.2/60,1)}

def search_places(q):
    if not q: return PLACES[:8]
    results = []
    for p in PLACES:
        score = 0
        if q in p["name"]: score = 10
        for kw in p.get("keywords", []):
            if q in kw: score = max(score, 5)
            elif kw in q: score = max(score, 3)
        if score > 0:
            pc = dict(p); pc["_score"] = score; results.append(pc)
    results.sort(key=lambda x: x["_score"], reverse=True)
    return results[:8]

# ── 地图画布 ──────────────────────────────────────────────

class MapCanvas(QWidget):
    def __init__(self):
        super().__init__()
        self.zoom = 16
        self.cx, self.cy = CENTER_LNG, CENTER_LAT
        self.dragging = False
        self.last_mouse = None
        self.tile_cache = {}
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        self.user_marker = None
        self.dest_marker = None
        self.route_coords = None
        self.route_info = ""

    def txy(self, lng):
        return (lng + 180) / 360 * (2 ** self.zoom)

    def tyy(self, lat):
        r = math.radians(lat)
        return (1 - math.asinh(math.tan(r)) / math.pi) / 2 * (2 ** self.zoom)

    def pixel_to_latlng(self, px, py):
        w, h = self.width(), self.height()
        tx = self.txy(self.cx) + (px - w/2) / TILE_SIZE
        ty = self.tyy(self.cy) + (py - h/2) / TILE_SIZE
        return tile_to_latlng(tx, ty, self.zoom)

    def latlng_to_pixel(self, lat, lng):
        w, h = self.width(), self.height()
        tx, ty = latlng_to_tile(lat, lng, self.zoom)
        return (int((tx - self.txy(self.cx)) * TILE_SIZE + w/2),
                int((ty - self.tyy(self.cy)) * TILE_SIZE + h/2))

    def load_tile(self, z, x, y):
        key = (z, x, y)
        if key in self.tile_cache:
            return self.tile_cache[key]
        path = TILE_DIR / str(z) / str(x) / f"{y}.png"
        pm = QPixmap(str(path)) if path.exists() else QPixmap(TILE_SIZE, TILE_SIZE)
        if not path.exists():
            pm.fill(QColor(240, 240, 240))
        self.tile_cache[key] = pm
        if len(self.tile_cache) > 300:
            self.tile_cache.pop(next(iter(self.tile_cache)))
        return pm

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()

        # 瓦片
        ctx = self.txy(self.cx)
        cty = self.tyy(self.cy)
        base_tx, base_ty = int(ctx), int(cty)
        ox = int((ctx - base_tx) * TILE_SIZE)
        oy = int((cty - base_ty) * TILE_SIZE)

        for dx in range(-2, w // TILE_SIZE + 3):
            for dy in range(-2, h // TILE_SIZE + 3):
                tx, ty = base_tx + dx, base_ty + dy
                if 0 <= tx < 2**self.zoom and 0 <= ty < 2**self.zoom:
                    pm = self.load_tile(self.zoom, tx, ty)
                    painter.drawPixmap(w//2 + dx*TILE_SIZE - ox, h//2 + dy*TILE_SIZE - oy, pm)

        # 路网
        if self.zoom >= 15 and hasattr(self, '_rnet'):
            painter.setPen(QPen(QColor(248, 113, 113, 100), 1))
            for line in self._rnet:
                pts = [QPoint(*self.latlng_to_pixel(*c)) for c in line]
                for i in range(len(pts)-1):
                    painter.drawLine(pts[i], pts[i+1])

        # 用户标记
        if self.user_marker:
            x, y = self.latlng_to_pixel(*self.user_marker)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(37, 99, 235))
            painter.drawEllipse(QPoint(x, y), 8, 8)
            painter.setBrush(Qt.white)
            painter.drawEllipse(QPoint(x, y), 4, 4)

        # 目的地标记
        if self.dest_marker:
            x, y = self.latlng_to_pixel(*self.dest_marker)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(231, 76, 60))
            painter.drawEllipse(QPoint(x, y), 10, 10)
            painter.setBrush(Qt.white)
            painter.drawEllipse(QPoint(x, y), 5, 5)

        # 路线
        if self.route_coords:
            painter.setPen(QPen(QColor(37, 99, 235), 4))
            pts = [QPoint(*self.latlng_to_pixel(*c)) for c in self.route_coords]
            for i in range(len(pts)-1):
                painter.drawLine(pts[i], pts[i+1])

        # 路线信息
        if self.route_info:
            font = QFont("Microsoft YaHei", 11)
            painter.setFont(font)
            fm = QFontMetrics(font)
            tw, th = fm.horizontalAdvance(self.route_info) + 20, fm.height() + 12
            rx, ry = 10, h - th - 20
            painter.setBrush(QColor(255, 255, 255, 220))
            painter.setPen(Qt.NoPen)
            painter.drawRoundedRect(rx, ry, tw, th, 8, 8)
            painter.setPen(QColor(37, 99, 235))
            painter.drawText(rx + 10, ry + fm.ascent() + 6, self.route_info)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.dragging = True
            self.last_mouse = event.position()
            self.setCursor(Qt.ClosedHandCursor)

    def mouseMoveEvent(self, event):
        if self.dragging and self.last_mouse is not None:
            pos = event.position()
            dx = pos.x() - self.last_mouse.x()
            dy = pos.y() - self.last_mouse.y()
            self.last_mouse = pos
            n = 2.0 ** self.zoom
            self.cx -= dx * 360.0 / (TILE_SIZE * n)
            # Mercator 校正：纬度变化需要考虑当前纬度
            self.cy += dy * 180.0 / (TILE_SIZE * n * math.cos(math.radians(self.cy)))
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self.dragging:
            self.dragging = False
            self.setCursor(Qt.ArrowCursor)
            if self.last_mouse is not None and (event.position() - self.last_mouse).manhattanLength() < 5:
                lat, lng = self.pixel_to_latlng(event.position().x(), event.position().y())
                self.on_map_click(lat, lng)

    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        if delta > 0 and self.zoom < MAX_ZOOM:
            self.zoom += 1
        elif delta < 0 and self.zoom > MIN_ZOOM:
            self.zoom -= 1
        self.update()

    def on_map_click(self, lat, lng):
        pass

    def load_roadnet(self):
        try:
            import geopandas as gpd
            edges = gpd.read_file(DATA_DIR / "ecnu_edges_merged.geojson")
            self._rnet = [[(c[1], c[0]) for c in g.coords]
                          for g in edges.geometry if g and not g.is_empty]
            print(f"Loaded {len(self._rnet)} road segments")
        except Exception as e:
            print(f"Roadnet load failed: {e}")
            self._rnet = []

# ── 主窗口 ────────────────────────────────────────────────

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("E刻校园 - 华师大闵行校区智能导航")
        self.resize(1200, 800)

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 搜索栏
        sf = QFrame()
        sf.setStyleSheet("background: white; border-bottom: 1px solid #e2e8f0;")
        sl = QHBoxLayout(sf)
        sl.setContentsMargins(100, 8, 100, 8)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("搜一搜：打印成绩单 / 打篮球 / 补办校园卡...")
        self.search_input.setStyleSheet("""
            QLineEdit { padding:10px 16px; border:1px solid #e2e8f0; border-radius:8px;
                        font-size:15px; background:#f8fafc; }
            QLineEdit:focus { border-color:#2563eb; background:white; }
        """)
        self.search_input.returnPressed.connect(self.do_search)

        sb = QPushButton("搜索")
        sb.setStyleSheet("""
            QPushButton { background:#2563eb; color:white; border:none; border-radius:8px;
                          padding:10px 20px; font-size:15px; }
            QPushButton:hover { background:#1d4ed8; }
        """)
        sb.clicked.connect(self.do_search)

        clear_btn = QPushButton("清除")
        clear_btn.setStyleSheet("""
            QPushButton { background:#f1f5f9; color:#64748b; border:none; border-radius:8px;
                          padding:10px 16px; font-size:14px; }
            QPushButton:hover { background:#e2e8f0; }
        """)
        clear_btn.clicked.connect(self.clear_all)

        sl.addWidget(self.search_input)
        sl.addWidget(sb)
        sl.addWidget(clear_btn)
        layout.addWidget(sf)

        # 地图
        ma = QWidget()
        ml = QVBoxLayout(ma)
        ml.setContentsMargins(0, 0, 0, 0)
        ml.setSpacing(0)

        self.canvas = MapCanvas()
        self.canvas.on_map_click = self.on_map_click

        self.result_list = QListWidget()
        self.result_list.hide()
        self.result_list.setMaximumHeight(250)
        self.result_list.setStyleSheet("""
            QListWidget { border:1px solid #e2e8f0; border-radius:8px; font-size:14px; }
            QListWidget::item { padding:10px 16px; border-bottom:1px solid #f0f0f0; }
            QListWidget::item:hover { background:#eff6ff; }
        """)
        self.result_list.itemClicked.connect(self.on_result_clicked)

        ml.addWidget(self.canvas, 1)
        ml.addWidget(self.result_list)
        layout.addWidget(ma, 1)

        # 状态栏
        self.status_label = QLabel("📍 点击地图选起点 | 🔍 搜索目的地 | 🖱 滚轮缩放 | 拖拽平移 | Esc 清除")
        self.status_label.setStyleSheet(
            "padding:6px 12px; background:#1e293b; color:#94a3b8; font-size:12px;")
        layout.addWidget(self.status_label)

        self.user_latlng = None
        self.dest_place = None
        self._route_worker = None

        QTimer.singleShot(100, self.load_roadnet)

    def load_roadnet(self):
        self.canvas.load_roadnet()
        self.canvas.update()

    def do_search(self):
        q = self.search_input.text().strip()
        results = search_places(q)
        self.result_list.clear()
        if results:
            for p in results:
                item = QListWidgetItem(f"{p['name']}  —  {p.get('detail', '')}")
                item.setData(Qt.UserRole, p)
                self.result_list.addItem(item)
            self.result_list.show()
        else:
            self.result_list.hide()

    def on_result_clicked(self, item):
        place = item.data(Qt.UserRole)
        self.result_list.hide()
        self.search_input.setText(place["name"])
        self.dest_place = place
        self.canvas.dest_marker = (place["lat"], place["lng"])
        if self.user_latlng:
            self.calc_route()
        else:
            self.canvas.update()
            self.status_label.setText(f"🎯 目的地: {place['name']} | 请点击地图设置起点")

    def on_map_click(self, lat, lng):
        self.user_latlng = (lat, lng)
        self.canvas.user_marker = (lat, lng)
        # 清除旧路线
        self.canvas.route_coords = None
        self.canvas.route_info = ""
        if self.dest_place:
            self.calc_route()
        else:
            self.canvas.update()
            self.status_label.setText(f"📍 起点: ({lat:.5f}, {lng:.5f}) | 搜索目的地")

    def calc_route(self):
        if not self.user_latlng or not self.dest_place:
            return
        self.status_label.setText("计算路线中...")
        QApplication.processEvents()

        # 后台线程计算
        self._route_worker = RouteWorker(
            self.user_latlng[0], self.user_latlng[1],
            self.dest_place["lat"], self.dest_place["lng"])
        self._route_worker.finished.connect(self.on_route_ready)
        self._route_worker.start()

    def on_route_ready(self, result):
        if result:
            self.canvas.route_coords = result["coordinates"]
            self.canvas.route_info = f"{result['distance_km']} km · {result['time_min']} 分钟"
            self.status_label.setText(
                f"✅ {self.dest_place['name']} | {result['distance_km']} km | 约 {result['time_min']} 分钟")
        else:
            self.canvas.route_coords = None
            self.canvas.route_info = ""
            self.status_label.setText("❌ 找不到可达路径")
        self.canvas.update()

    def clear_all(self):
        self.user_latlng = None
        self.dest_place = None
        self.canvas.user_marker = None
        self.canvas.dest_marker = None
        self.canvas.route_coords = None
        self.canvas.route_info = ""
        self.search_input.clear()
        self.result_list.hide()
        self.canvas.update()
        self.status_label.setText("📍 点击地图选起点 | 🔍 搜索目的地 | 🖱 滚轮缩放 | 拖拽平移 | Esc 清除")

# ── 启动 ──────────────────────────────────────────────────

if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
