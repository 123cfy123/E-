"""
E刻校园 Qt 原生版 — 界面和功能对齐网页版
纯 Python + PySide6 + 瓦片手工渲染
"""

import sys, math, json
from pathlib import Path
from PySide6.QtWidgets import *
from PySide6.QtCore import Qt, QPoint, QPointF, QTimer, QThread, Signal, QRectF
from PySide6.QtGui import (QPainter, QPixmap, QColor, QPen, QFont,
                            QFontMetrics, QPainterPath, QPolygonF, QBrush)

# ── 配置 ──
TILE_DIR, DATA_DIR = Path("static/tiles"), Path("data")
CENTER_LAT, CENTER_LNG = 31.0345, 121.4555
MIN_ZOOM, MAX_ZOOM, TILE_SIZE = 15, 18, 256

# ── 后端 ──
import osmnx as ox, networkx as nx
from shapely.geometry import Point, LineString, Polygon
from shapely.ops import unary_union

print("Loading...")
G = ox.load_graphml(DATA_DIR / "ecnu_walk_merged.graphml")
if "crs" not in G.graph: G.graph["crs"] = "EPSG:4326"
print(f"  Graph: {G.number_of_nodes()} nodes, {G.number_of_edges()} edges")

with open(DATA_DIR / "campus_places.json", encoding="utf-8") as f:
    PLACES = json.load(f)
print(f"  Places: {len(PLACES)}")

with open(DATA_DIR / "campus_boundary.geojson") as f:
    BOUNDARY_COORDS = json.load(f)["features"][0]["geometry"]["coordinates"][0]
    BOUNDARY_POLY = Polygon(BOUNDARY_COORDS)
print(f"  Boundary: {len(BOUNDARY_COORDS)} points")

# ── 坐标工具 ──
def ll2tile(lat, lng, zoom):
    n = 2.0**zoom
    return ((lng+180)/360*n,
            (1-math.asinh(math.tan(math.radians(lat)))/math.pi)/2*n)

def tile2ll(tx, ty, zoom):
    n = 2.0**zoom
    return (math.degrees(math.atan(math.sinh(math.pi*(1-2*ty/n)))),
            tx/n*360-180)

# ── 路由线程 ──
class RouteWorker(QThread):
    done = Signal(object)
    def __init__(self, fla, fln, tla, tln):
        super().__init__()
        self.a, self.b, self.c, self.d = fla, fln, tla, tln
    def run(self):
        self.done.emit(_route(self.a, self.b, self.c, self.d))

def _route(fla, fln, tla, tln):
    def snap(lng, lat):
        pt = Point(lng, lat)
        bd, bp, bu, bv = float("inf"), None, None, None
        for u, v, d in G.edges(data=True):
            g = d.get("geometry")
            if g is None:
                g = LineString([(G.nodes[u]["x"],G.nodes[u]["y"]),
                                (G.nodes[v]["x"],G.nodes[v]["y"])])
            dist = g.distance(pt)
            if dist < bd:
                bd = dist; p = g.interpolate(g.project(pt))
                bp, bu, bv = (p.y, p.x), u, v
        if bp is None:
            n = ox.distance.nearest_nodes(G, lng, lat)
            return (G.nodes[n]["y"], G.nodes[n]["x"]), n
        du = (G.nodes[bu]["y"]-bp[0])**2+(G.nodes[bu]["x"]-bp[1])**2
        dv = (G.nodes[bv]["y"]-bp[0])**2+(G.nodes[bv]["x"]-bp[1])**2
        return bp, (bu if du<dv else bv)

    fp, fe = snap(fln, fla)
    tp, te = snap(tln, tla)
    if fe == te: return None
    try: path = nx.shortest_path(G, fe, te, weight="length")
    except:
        try: path = nx.shortest_path(G.to_undirected(), fe, te, weight="length")
        except: return None

    coords, total = [[fp[0],fp[1]]], 0.0
    al = lambda dy,dx: math.sqrt((dy*111320)**2+(dx*111320*0.85)**2)
    total += al(G.nodes[fe]["y"]-fp[0], G.nodes[fe]["x"]-fp[1])
    for i in range(len(path)-1):
        u, v = path[i], path[i+1]
        ed = G.get_edge_data(u,v) or G.get_edge_data(v,u)
        if not ed: continue
        d = min(ed.values(), key=lambda x: x.get("length",1e9))
        total += d.get("length",0)
        g = d.get("geometry")
        if g: coords.extend([[c[1],c[0]] for c in g.coords])
        else:
            coords.append([G.nodes[u]["y"],G.nodes[u]["x"]])
            if i==len(path)-2: coords.append([G.nodes[v]["y"],G.nodes[v]["x"]])
    coords.append([tp[0],tp[1]])
    total += al(tp[0]-G.nodes[te]["y"], tp[1]-G.nodes[te]["x"])
    ded = [coords[0]]
    for c in coords[1:]:
        if c != ded[-1]: ded.append(c)
    return {"coords":ded,"dist_m":round(total,1),"dist_km":round(total/1e3,2),
            "time_m":round(total/1.2/60,1)}

def search_places(q):
    if not q: return PLACES[:8]
    r = []
    for p in PLACES:
        s = 0
        if q in p["name"]: s = 10
        for kw in p.get("keywords",[]):
            if q in kw: s = max(s,5)
            elif kw in q: s = max(s,3)
        if s>0: pc=dict(p); pc["_s"]=s; r.append(pc)
    r.sort(key=lambda x:x["_s"], reverse=True)
    return r[:8]

# ── 地图画布 ──
class MapCanvas(QWidget):
    clicked = Signal(float, float)  # lat, lng

    def __init__(self):
        super().__init__()
        self.zoom, self.cx, self.cy = 16, CENTER_LNG, CENTER_LAT
        self.drag = False; self.last_pos = None
        self.cache = {}
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        # 图层数据
        self.usr = None      # (lat,lng)
        self.dst_marker = None
        self.route = None    # [[lat,lng],...]
        self.route_text = ""
        self._rnet = []      # 路网
        self._mask_poly = None  # 校园遮罩路径

    def txy(self, lng): return (lng+180)/360*(2**self.zoom)
    def tyy(self, lat): return (1-math.asinh(math.tan(math.radians(lat)))/math.pi)/2*(2**self.zoom)

    def ll2px(self, lat, lng):
        w, h = self.width(), self.height()
        tx, ty = ll2tile(lat, lng, self.zoom)
        return (int((tx-self.txy(self.cx))*TILE_SIZE+w/2),
                int((ty-self.tyy(self.cy))*TILE_SIZE+h/2))

    def px2ll(self, px, py):
        w, h = self.width(), self.height()
        return tile2ll(self.txy(self.cx)+(px-w/2)/TILE_SIZE,
                       self.tyy(self.cy)+(py-h/2)/TILE_SIZE, self.zoom)

    def load_tile(self, z, x, y):
        k = (z,x,y)
        if k in self.cache: return self.cache[k]
        p = TILE_DIR/str(z)/str(x)/f"{y}.png"
        pm = QPixmap(str(p)) if p.exists() else QPixmap(TILE_SIZE,TILE_SIZE)
        if not p.exists(): pm.fill(QColor(240,240,240))
        self.cache[k] = pm
        if len(self.cache)>300: self.cache.pop(next(iter(self.cache)))
        return pm

    def load_roadnet(self):
        try:
            import geopandas as gpd
            e = gpd.read_file(DATA_DIR/"ecnu_edges_merged.geojson")
            self._rnet = [[(c[1],c[0]) for c in g.coords]
                          for g in e.geometry if g and not g.is_empty]
        except:
            self._rnet = []

    def fit_campus(self):
        """自适应校园边界"""
        xs = [c[0] for c in BOUNDARY_COORDS]
        ys = [c[1] for c in BOUNDARY_COORDS]
        self.cx = (min(xs)+max(xs))/2
        self.cy = (min(ys)+max(ys))/2
        # 找合适缩放级别
        for z in range(MAX_ZOOM, MIN_ZOOM-1, -1):
            tx1, ty1 = ll2tile(max(ys), min(xs), z)
            tx2, ty2 = ll2tile(min(ys), max(xs), z)
            pw = abs(tx2-tx1)*TILE_SIZE
            ph = abs(ty2-ty1)*TILE_SIZE
            if pw < self.width()*0.8 and ph < self.height()*0.8:
                self.zoom = max(z, MIN_ZOOM)
                break
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()

        # 1. 瓦片
        ctx, cty = self.txy(self.cx), self.tyy(self.cy)
        bx, by = int(ctx), int(cty)
        ox, oy = int((ctx-bx)*TILE_SIZE), int((cty-by)*TILE_SIZE)
        mz = 2**self.zoom
        for dx in range(-2, w//TILE_SIZE+3):
            for dy in range(-2, h//TILE_SIZE+3):
                tx, ty = bx+dx, by+dy
                if 0<=tx<mz and 0<=ty<mz:
                    p.drawPixmap(w//2+dx*TILE_SIZE-ox, h//2+dy*TILE_SIZE-oy,
                                 self.load_tile(self.zoom,tx,ty))

        # 2. 路网
        if self.zoom>=15 and self._rnet:
            p.setPen(QPen(QColor(248,113,113,100), 1))
            for line in self._rnet:
                pts = [QPoint(*self.ll2px(*c)) for c in line]
                for i in range(len(pts)-1): p.drawLine(pts[i],pts[i+1])

        # 3. POI 标记
        font = QFont("Microsoft YaHei", 9)
        p.setFont(font)
        for pl in PLACES:
            px, py = self.ll2px(pl["lat"], pl["lng"])
            # 检查是否在屏幕内
            if -50<px<w+50 and -50<py<h+50:
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(245,158,11))
                p.drawEllipse(QPoint(px,py), 5, 5)
                p.setPen(QColor(30,41,59))
                p.drawText(px+8, py+4, pl["name"][:6])

        # 4. 起点
        if self.usr:
            x, y = self.ll2px(*self.usr)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(37,99,235))
            p.drawEllipse(QPoint(x,y), 8, 8)
            p.setBrush(Qt.white)
            p.drawEllipse(QPoint(x,y), 4, 4)

        # 5. 目的地
        if self.dst_marker:
            x, y = self.ll2px(*self.dst_marker)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(231,76,60))
            p.drawEllipse(QPoint(x,y), 10, 10)
            p.setBrush(Qt.white)
            p.drawEllipse(QPoint(x,y), 5, 5)

        # 6. 路线
        if self.route:
            p.setPen(QPen(QColor(37,99,235), 4))
            pts = [QPoint(*self.ll2px(*c)) for c in self.route]
            for i in range(len(pts)-1): p.drawLine(pts[i],pts[i+1])

        # 7. 遮罩 + 边界
        if BOUNDARY_COORDS:
            hole = QPolygonF()
            for lng, lat in BOUNDARY_COORDS:
                hole.append(QPointF(*self.ll2px(lat, lng)))
            mask_path = QPainterPath()
            mask_path.addRect(QRectF(-5000, -5000, w+10000, h+10000))
            mask_path.addPolygon(hole)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(0,0,0))
            p.drawPath(mask_path)
            # 边界线
            p.setPen(QPen(QColor(37,99,235), 2, Qt.DashLine))
            p.setBrush(Qt.NoBrush)
            p.drawPolygon(hole)

        # 9. 路线信息卡片
        if self.route_text:
            font2 = QFont("Microsoft YaHei", 12)
            p.setFont(font2)
            fm = QFontMetrics(font2)
            dist, time = self.route_text.split("·")
            tw = max(fm.horizontalAdvance(dist), fm.horizontalAdvance(time))+30
            th = fm.height()*2+20
            rx, ry = 12, h-th-20
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(255,255,255,230))
            p.drawRoundedRect(rx, ry, tw, th, 10, 10)
            p.setPen(QColor(37,99,235))
            fbig = QFont("Microsoft YaHei", 16)
            fbig.setBold(True)
            p.setFont(fbig)
            p.drawText(rx+15, ry+fm.ascent()+5, dist.strip())
            p.setFont(font2)
            p.setPen(QColor(100,116,139))
            p.drawText(rx+15, ry+fm.height()+fm.ascent()+8, time.strip())

    def mousePressEvent(self, e):
        if e.button()==Qt.LeftButton:
            self.drag = True; self.last_pos = e.position()
            self.setCursor(Qt.ClosedHandCursor)

    def mouseMoveEvent(self, e):
        if self.drag and self.last_pos is not None:
            pos = e.position()
            dx, dy = pos.x()-self.last_pos.x(), pos.y()-self.last_pos.y()
            self.last_pos = pos
            n = 2.0**self.zoom
            self.cx -= dx*360.0/(TILE_SIZE*n)
            self.cy += dy*180.0/(TILE_SIZE*n*math.cos(math.radians(self.cy)))
            self.update()

    def mouseReleaseEvent(self, e):
        if e.button()==Qt.LeftButton and self.drag:
            self.drag = False; self.setCursor(Qt.ArrowCursor)
            if self.last_pos is not None and (e.position()-self.last_pos).manhattanLength()<5:
                lat, lng = self.px2ll(e.position().x(), e.position().y())
                self.clicked.emit(lat, lng)

    def wheelEvent(self, e):
        d = e.angleDelta().y()
        if d>0 and self.zoom<MAX_ZOOM: self.zoom+=1
        elif d<0 and self.zoom>MIN_ZOOM: self.zoom-=1
        self.update()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.update()

# ── 主窗口 ──
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("E刻校园 - 华师大闵行校区智能导航")
        self.resize(1200, 800)

        cw = QWidget(); self.setCentralWidget(cw)
        layout = QVBoxLayout(cw)
        layout.setContentsMargins(0,0,0,0); layout.setSpacing(0)

        # 搜索栏
        sf = QFrame()
        sf.setStyleSheet("background:white; border-bottom:1px solid #e2e8f0;")
        sl = QHBoxLayout(sf); sl.setContentsMargins(100,8,100,8)

        self.si = QLineEdit()
        self.si.setPlaceholderText("搜一搜：打印成绩单 / 打篮球 / 补办校园卡...")
        self.si.setStyleSheet("QLineEdit{padding:10px 16px;border:1px solid #e2e8f0;border-radius:8px;font-size:15px;background:#f8fafc;}QLineEdit:focus{border-color:#2563eb;background:white;}")
        self.si.returnPressed.connect(self.do_search)

        sb = QPushButton("搜索")
        sb.setStyleSheet("QPushButton{background:#2563eb;color:white;border:none;border-radius:8px;padding:10px 20px;font-size:15px;}QPushButton:hover{background:#1d4ed8;}")
        sb.clicked.connect(self.do_search)

        clr = QPushButton("清除")
        clr.setStyleSheet("QPushButton{background:#f1f5f9;color:#64748b;border:none;border-radius:8px;padding:10px 16px;font-size:14px;}QPushButton:hover{background:#e2e8f0;}")
        clr.clicked.connect(self.clear_all)

        sl.addWidget(self.si); sl.addWidget(sb); sl.addWidget(clr)
        layout.addWidget(sf)

        # 地图
        self.canvas = MapCanvas()
        self.canvas.clicked.connect(self.on_click)

        # 结果列表
        self.rl = QListWidget()
        self.rl.hide(); self.rl.setMaximumHeight(250)
        self.rl.setStyleSheet("QListWidget{border:1px solid #e2e8f0;border-radius:8px;font-size:14px;}QListWidget::item{padding:10px 16px;border-bottom:1px solid #f0f0f0;}QListWidget::item:hover{background:#eff6ff;}")
        self.rl.itemClicked.connect(self.on_result)

        layout.addWidget(self.canvas, 1)
        layout.addWidget(self.rl)

        # 状态栏
        self.slbl = QLabel("📍 点击地图/标记 选起点 | 🔍 搜索目的地 | 🖱 滚轮缩放 | 右键平移 | Esc 清除")
        self.slbl.setStyleSheet("padding:6px 12px;background:#1e293b;color:#94a3b8;font-size:12px;")
        layout.addWidget(self.slbl)

        self.usr = None; self.dst = None; self._worker = None

        # 加载数据
        QTimer.singleShot(200, self.init_data)

    def init_data(self):
        self.canvas.load_roadnet()
        QTimer.singleShot(500, self.canvas.fit_campus)
        self.canvas.update()

    def do_search(self):
        q = self.si.text().strip()
        results = search_places(q)
        self.rl.clear()
        if results:
            for p in results:
                it = QListWidgetItem(f"{p['name']}  —  {p.get('detail','')}")
                it.setData(Qt.UserRole, p); self.rl.addItem(it)
            self.rl.show()
        else:
            self.rl.hide()

    def on_result(self, item):
        place = item.data(Qt.UserRole)
        self.rl.hide(); self.si.setText(place["name"])
        self.dst = place
        self.canvas.dst_marker = (place["lat"], place["lng"])
        if self.usr: self.do_route()
        else: self.canvas.update(); self.slbl.setText(f"🎯 目的地: {place['name']} | 请点击地图/POI标记选择起点")

    def on_click(self, lat, lng):
        self.usr = (lat, lng)
        self.canvas.usr = (lat, lng)
        self.canvas.route = None; self.canvas.route_text = ""
        if self.dst: self.do_route()
        else: self.canvas.update(); self.slbl.setText(f"📍 起点: ({lat:.5f},{lng:.5f}) | 搜索目的地")

    def do_route(self):
        if not self.usr or not self.dst: return
        self.slbl.setText("计算路线中...")
        QApplication.processEvents()
        self._worker = RouteWorker(self.usr[0],self.usr[1],self.dst["lat"],self.dst["lng"])
        self._worker.done.connect(self.on_route); self._worker.start()

    def on_route(self, r):
        if r:
            self.canvas.route = r["coords"]
            self.canvas.route_text = f"  {r['dist_km']} km  ·  {r['time_m']} 分钟"
            self.slbl.setText(f"✅ {self.dst['name']} | {r['dist_km']}km | 约{r['time_m']}分钟")
        else:
            self.canvas.route = None; self.canvas.route_text = ""
            self.slbl.setText("❌ 找不到可达路径")
        self.canvas.update()

    def clear_all(self):
        self.usr = None; self.dst = None
        self.canvas.usr = None; self.canvas.dst_marker = None
        self.canvas.route = None; self.canvas.route_text = ""
        self.si.clear(); self.rl.hide(); self.canvas.update()
        self.slbl.setText("📍 点击地图/标记 选起点 | 🔍 搜索目的地 | 🖱 滚轮缩放 | 右键平移 | Esc 清除")

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape: self.clear_all()

if __name__ == "__main__":
    app = QApplication(sys.argv); app.setStyle("Fusion")
    w = MainWindow(); w.show()
    sys.exit(app.exec())
