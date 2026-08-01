"""
E刻校园 Qt 原生版 — 界面和功能对齐网页版
"""

import sys, math, json
from pathlib import Path
from PySide6.QtWidgets import *
from PySide6.QtCore import Qt, QPoint, QPointF, QTimer, QThread, Signal, QRectF
from PySide6.QtGui import (QPainter, QPixmap, QColor, QPen, QFont,
                            QFontMetrics, QPainterPath, QPolygonF, QBrush, QAction)

# ── 配置 ──
TILE_DIR, DATA_DIR = Path("static/tiles"), Path("data")
CENTER_LAT, CENTER_LNG = 31.0345, 121.4555
MIN_ZOOM, MAX_ZOOM, TILE_SIZE = 15, 18, 256

# ── 后端数据 ──
import osmnx as ox, networkx as nx
from shapely.geometry import Point, LineString, Polygon

print("Loading...")
G = ox.load_graphml(DATA_DIR / "ecnu_walk_merged.graphml")
if "crs" not in G.graph: G.graph["crs"] = "EPSG:4326"
with open(DATA_DIR / "campus_places.json", encoding="utf-8") as f:
    PLACES = json.load(f)
with open(DATA_DIR / "campus_boundary.geojson") as f:
    BOUNDARY_COORDS = json.load(f)["features"][0]["geometry"]["coordinates"][0]
print(f"  Graph:{G.number_of_nodes()}n/{G.number_of_edges()}e  Places:{len(PLACES)}  Boundary:{len(BOUNDARY_COORDS)}pts")

# ── 坐标 ──
def ll2tile(lat, lng, zoom):
    n=2**zoom; return ((lng+180)/360*n, (1-math.asinh(math.tan(math.radians(lat)))/math.pi)/2*n)
def tile2ll(tx,ty,z):
    n=2**z; return (math.degrees(math.atan(math.sinh(math.pi*(1-2*ty/n)))), tx/n*360-180)

# ── 路由线程 ──
class RouteWorker(QThread):
    done=Signal(object)
    def __init__(self,a,b,c,d): super().__init__(); self.a,self.b,self.c,self.d=a,b,c,d
    def run(self): self.done.emit(_route(self.a,self.b,self.c,self.d))

def _route(fla,fln,tla,tln):
    def snap(lng,lat):
        pt=Point(lng,lat); bd=float("inf"); bp=bu=bv=None
        for u,v,d in G.edges(data=True):
            g=d.get("geometry")
            if g is None: g=LineString([(G.nodes[u]["x"],G.nodes[u]["y"]),(G.nodes[v]["x"],G.nodes[v]["y"])])
            dist=g.distance(pt)
            if dist<bd: bd=dist; p=g.interpolate(g.project(pt)); bp=(p.y,p.x); bu,bv=u,v
        if bp is None: n=ox.distance.nearest_nodes(G,lng,lat); return (G.nodes[n]["y"],G.nodes[n]["x"]),n
        du=(G.nodes[bu]["y"]-bp[0])**2+(G.nodes[bu]["x"]-bp[1])**2
        dv=(G.nodes[bv]["y"]-bp[0])**2+(G.nodes[bv]["x"]-bp[1])**2
        return bp,(bu if du<dv else bv)
    fp,fe=snap(fln,fla); tp,te=snap(tln,tla)
    if fe==te: return None
    try: path=nx.shortest_path(G,fe,te,weight="length")
    except:
        try: path=nx.shortest_path(G.to_undirected(),fe,te,weight="length")
        except: return None
    coords,total=[[fp[0],fp[1]]],0.0
    al=lambda dy,dx: math.sqrt((dy*111320)**2+(dx*111320*0.85)**2)
    total+=al(G.nodes[fe]["y"]-fp[0],G.nodes[fe]["x"]-fp[1])
    for i in range(len(path)-1):
        u,v=path[i],path[i+1]; ed=G.get_edge_data(u,v) or G.get_edge_data(v,u)
        if not ed: continue
        d=min(ed.values(),key=lambda x:x.get("length",1e9)); total+=d.get("length",0)
        g=d.get("geometry")
        if g: coords.extend([[c[1],c[0]] for c in g.coords])
        else:
            coords.append([G.nodes[u]["y"],G.nodes[u]["x"]])
            if i==len(path)-2: coords.append([G.nodes[v]["y"],G.nodes[v]["x"]])
    coords.append([tp[0],tp[1]]); total+=al(tp[0]-G.nodes[te]["y"],tp[1]-G.nodes[te]["x"])
    ded=[coords[0]]
    for c in coords[1:]:
        if c!=ded[-1]: ded.append(c)
    return {"coords":ded,"dist_m":round(total,1),"dist_km":round(total/1e3,2),"time_m":round(total/1.2/60,1)}

def search_places(q):
    if not q: return PLACES[:8]
    r=[]
    for p in PLACES:
        s=0
        if q in p["name"]: s=10
        for kw in p.get("keywords",[]):
            if q in kw: s=max(s,5)
            elif kw in q: s=max(s,3)
        if s>0: pc=dict(p); pc["_s"]=s; r.append(pc)
    r.sort(key=lambda x:x["_s"],reverse=True); return r[:8]

def save_places():
    with open(DATA_DIR/"campus_places.json","w",encoding="utf-8") as f:
        json.dump(PLACES,f,ensure_ascii=False,indent=2)

# ── 地图画布 ──
class MapCanvas(QWidget):
    clicked=Signal(float,float)
    poi_clicked=Signal(int)  # place id

    def __init__(self):
        super().__init__()
        self.zoom,self.cx,self.cy=16,CENTER_LNG,CENTER_LAT
        self.drag=False; self.last_pos=None
        self.cache={}
        self.setMouseTracking(True); self.setFocusPolicy(Qt.StrongFocus)
        self.setSizePolicy(QSizePolicy.Expanding,QSizePolicy.Expanding)
        self.usr=None; self.dst_marker=None; self.route=None; self.route_text=""
        self._rnet=[]; self.edit_mode=False; self._drag_poi=None

    def txy(self,lng): return (lng+180)/360*(2**self.zoom)
    def tyy(self,lat): return (1-math.asinh(math.tan(math.radians(lat)))/math.pi)/2*(2**self.zoom)
    def ll2px(self,lat,lng):
        w,h=self.width(),self.height(); tx,ty=ll2tile(lat,lng,self.zoom)
        return (int((tx-self.txy(self.cx))*TILE_SIZE+w/2), int((ty-self.tyy(self.cy))*TILE_SIZE+h/2))
    def px2ll(self,px,py):
        w,h=self.width(),self.height()
        return tile2ll(self.txy(self.cx)+(px-w/2)/TILE_SIZE, self.tyy(self.cy)+(py-h/2)/TILE_SIZE, self.zoom)

    def load_tile(self,z,x,y):
        k=(z,x,y)
        if k in self.cache: return self.cache[k]
        p=TILE_DIR/str(z)/str(x)/f"{y}.png"
        pm=QPixmap(str(p)) if p.exists() else QPixmap(TILE_SIZE,TILE_SIZE)
        if not p.exists(): pm.fill(QColor(240,240,240))
        self.cache[k]=pm
        if len(self.cache)>300: self.cache.pop(next(iter(self.cache)))
        return pm

    def load_roadnet(self):
        try:
            import geopandas as gpd
            e=gpd.read_file(DATA_DIR/"ecnu_edges_merged.geojson")
            self._rnet=[[(c[1],c[0]) for c in g.coords] for g in e.geometry if g and not g.is_empty]
        except: self._rnet=[]

    def fit_campus(self):
        xs=[c[0] for c in BOUNDARY_COORDS]; ys=[c[1] for c in BOUNDARY_COORDS]
        self.cx=(min(xs)+max(xs))/2; self.cy=(min(ys)+max(ys))/2
        for z in range(MAX_ZOOM,MIN_ZOOM-1,-1):
            tx1,ty1=ll2tile(max(ys),min(xs),z); tx2,ty2=ll2tile(min(ys),max(xs),z)
            if abs(tx2-tx1)*TILE_SIZE<self.width()*0.8 and abs(ty2-ty1)*TILE_SIZE<self.height()*0.8:
                self.zoom=max(z,MIN_ZOOM); break
        self.update()

    def paintEvent(self,event):
        p=QPainter(self); p.setRenderHint(QPainter.Antialiasing); w,h=self.width(),self.height()

        # 瓦片
        ctx,cty=self.txy(self.cx),self.tyy(self.cy); bx,by=int(ctx),int(cty)
        ox,oy=int((ctx-bx)*TILE_SIZE),int((cty-by)*TILE_SIZE); mz=2**self.zoom
        for dx in range(-2,w//TILE_SIZE+3):
            for dy in range(-2,h//TILE_SIZE+3):
                tx,ty=bx+dx,by+dy
                if 0<=tx<mz and 0<=ty<mz:
                    p.drawPixmap(w//2+dx*TILE_SIZE-ox,h//2+dy*TILE_SIZE-oy,self.load_tile(self.zoom,tx,ty))

        # 路网
        if self.zoom>=15 and self._rnet:
            p.setPen(QPen(QColor(248,113,113,100),1))
            for line in self._rnet:
                pts=[QPoint(*self.ll2px(*c)) for c in line]
                for i in range(len(pts)-1): p.drawLine(pts[i],pts[i+1])

        # POI
        for pl in PLACES:
            px,py=self.ll2px(pl["lat"],pl["lng"])
            if -50<px<w+50 and -50<py<h+50:
                c=QColor(34,197,94) if self.edit_mode else QColor(245,158,11)
                p.setPen(QPen(Qt.white,2)); p.setBrush(c)
                p.drawEllipse(QPoint(px,py),6,6)
                if self.edit_mode or self.zoom>=16:
                    f=QFont("Microsoft YaHei",8); p.setFont(f)
                    p.setPen(QColor(50,50,50)); p.drawText(px+9,py+4,pl["name"][:8])

        # 起点
        if self.usr:
            x,y=self.ll2px(*self.usr)
            p.setPen(Qt.NoPen); p.setBrush(QColor(37,99,235))
            p.drawEllipse(QPoint(x,y),8,8); p.setBrush(Qt.white); p.drawEllipse(QPoint(x,y),4,4)

        # 目的地
        if self.dst_marker:
            x,y=self.ll2px(*self.dst_marker)
            p.setPen(Qt.NoPen); p.setBrush(QColor(231,76,60))
            p.drawEllipse(QPoint(x,y),10,10); p.setBrush(Qt.white); p.drawEllipse(QPoint(x,y),5,5)

        # 路线
        if self.route:
            p.setPen(QPen(QColor(37,99,235),4))
            pts=[QPoint(*self.ll2px(*c)) for c in self.route]
            for i in range(len(pts)-1): p.drawLine(pts[i],pts[i+1])

        # 遮罩+边界
        if BOUNDARY_COORDS:
            hole=QPolygonF()
            for lng,lat in BOUNDARY_COORDS: hole.append(QPointF(*self.ll2px(lat,lng)))
            mp=QPainterPath(); mp.addRect(QRectF(-5000,-5000,w+10000,h+10000)); mp.addPolygon(hole)
            p.setPen(Qt.NoPen); p.setBrush(QColor(0,0,0)); p.drawPath(mp)
            p.setPen(QPen(QColor(37,99,235),2,Qt.DashLine)); p.setBrush(Qt.NoBrush); p.drawPolygon(hole)

        # 路线信息
        if self.route_text:
            fbig=QFont("Microsoft YaHei",16); fbig.setBold(True)
            f2=QFont("Microsoft YaHei",12); fm=QFontMetrics(f2)
            parts=self.route_text.split("·"); tw=max(fm.horizontalAdvance(parts[0]),fm.horizontalAdvance(parts[1]))+30
            th=fm.height()*2+20; rx,ry=12,h-th-20
            p.setPen(Qt.NoPen); p.setBrush(QColor(255,255,255,230)); p.drawRoundedRect(rx,ry,tw,th,10,10)
            p.setFont(fbig); p.setPen(QColor(37,99,235)); p.drawText(rx+15,ry+fm.ascent()+5,parts[0].strip())
            p.setFont(f2); p.setPen(QColor(100,116,139)); p.drawText(rx+15,ry+fm.height()+fm.ascent()+8,parts[1].strip())

        # 编辑模式提示
        if self.edit_mode:
            p.setPen(QColor(30,41,59)); f3=QFont("Microsoft YaHei",10)
            p.setFont(f3); fm3=QFontMetrics(f3)
            tw3=fm3.horizontalAdvance("编辑模式：拖拽POI移动 · 双击POI修改信息 · 按Esc退出")+20
            p.setBrush(QColor(254,240,138,200)); p.setPen(Qt.NoPen)
            p.drawRoundedRect(w//2-tw3//2,8,tw3,28,6,6)
            p.setPen(QColor(133,77,14)); p.drawText(w//2-tw3//2+10,8+fm3.ascent()+4,"编辑模式：拖拽POI移动 · 双击POI修改信息 · 按Esc退出")

    def mousePressEvent(self,e):
        if e.button()==Qt.LeftButton:
            # 编辑模式下检查是否点到POI
            if self.edit_mode:
                mx,my=e.position().x(),e.position().y()
                for pl in PLACES:
                    px,py=self.ll2px(pl["lat"],pl["lng"])
                    if abs(mx-px)<15 and abs(my-py)<15:
                        self._drag_poi=pl; self.setCursor(Qt.ClosedHandCursor); return
            self.drag=True; self.last_pos=e.position(); self.setCursor(Qt.ClosedHandCursor)
        elif e.button()==Qt.RightButton:
            self.clicked.emit(None,None)  # 右键=清除

    def mouseMoveEvent(self,e):
        if self._drag_poi is not None:
            lat,lng=self.px2ll(e.position().x(),e.position().y())
            self._drag_poi["lat"]=round(lat,6); self._drag_poi["lng"]=round(lng,6)
            self.update(); return
        # 限制经纬度范围
        xs=[c[0] for c in BOUNDARY_COORDS]; ys=[c[1] for c in BOUNDARY_COORDS]
        min_lng,max_lng=min(xs)-0.001,max(xs)+0.001
        min_lat,max_lat=min(ys)-0.001,max(ys)+0.001
        self.cx=max(min_lng,min(max_lng,self.cx))
        self.cy=max(min_lat,min(max_lat,self.cy))

        if self.drag and self.last_pos is not None:
            pos=e.position(); dx,dy=pos.x()-self.last_pos.x(),pos.y()-self.last_pos.y()
            self.last_pos=pos; n=2.0**self.zoom
            self.cx-=dx*360.0/(TILE_SIZE*n)
            self.cy+=dy*180.0/(TILE_SIZE*n*math.cos(math.radians(self.cy)))
            self.update()

    def mouseReleaseEvent(self,e):
        if self._drag_poi is not None:
            save_places(); self._drag_poi=None; self.setCursor(Qt.ArrowCursor); return
        if e.button()==Qt.LeftButton and self.drag:
            self.drag=False; self.setCursor(Qt.ArrowCursor)
            if self.last_pos is not None and (e.position()-self.last_pos).manhattanLength()<3:
                lat,lng=self.px2ll(e.position().x(),e.position().y())
                self.clicked.emit(lat,lng)

    def mouseDoubleClickEvent(self,e):
        if self.edit_mode:
            mx,my=e.position().x(),e.position().y()
            for pl in PLACES:
                px,py=self.ll2px(pl["lat"],pl["lng"])
                if abs(mx-px)<15 and abs(my-py)<15:
                    self.poi_clicked.emit(pl["id"]); return

    def wheelEvent(self,e):
        d=e.angleDelta().y()
        if d>0 and self.zoom<MAX_ZOOM: self.zoom+=1
        elif d<0 and self.zoom>MIN_ZOOM: self.zoom-=1
        self.update()

    def resizeEvent(self,e): super().resizeEvent(e); self.update()

# ── 主窗口 ──
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("E刻校园 - 华师大闵行校区智能导航")
        self.resize(1200,800)

        cw=QWidget(); self.setCentralWidget(cw)
        layout=QVBoxLayout(cw); layout.setContentsMargins(0,0,0,0); layout.setSpacing(0)

        # 搜索栏
        sf=QFrame(); sf.setStyleSheet("background:white; border-bottom:1px solid #e2e8f0;")
        sl=QHBoxLayout(sf); sl.setContentsMargins(100,8,100,8)

        self.si=QLineEdit()
        self.si.setPlaceholderText("搜一搜：打印成绩单 / 打篮球 / 补办校园卡...")
        self.si.setStyleSheet("QLineEdit{padding:10px 16px;border:1px solid #e2e8f0;border-radius:8px;font-size:15px;background:#f8fafc;}QLineEdit:focus{border-color:#2563eb;background:white;}")
        self.si.returnPressed.connect(self.do_search)

        sb=QPushButton("搜索"); sb.clicked.connect(self.do_search)
        sb.setStyleSheet("QPushButton{background:#2563eb;color:white;border:none;border-radius:8px;padding:10px 20px;font-size:15px;}QPushButton:hover{background:#1d4ed8;}")

        clr=QPushButton("清除"); clr.clicked.connect(self.clear_all)
        clr.setStyleSheet("QPushButton{background:#f1f5f9;color:#64748b;border:none;border-radius:8px;padding:10px 16px;font-size:14px;}QPushButton:hover{background:#e2e8f0;}")

        self.edit_btn=QPushButton("编辑"); self.edit_btn.setCheckable(True); self.edit_btn.clicked.connect(self.toggle_edit)
        self.edit_btn.setStyleSheet("QPushButton{background:#f1f5f9;color:#64748b;border:none;border-radius:8px;padding:10px 16px;font-size:14px;}QPushButton:checked{background:#fef08a;color:#854d0e;}QPushButton:hover{background:#e2e8f0;}")

        self.add_btn=QPushButton("+"); self.add_btn.clicked.connect(self.start_add)
        self.add_btn.setStyleSheet("QPushButton{background:#2563eb;color:white;border:none;border-radius:8px;padding:10px 14px;font-size:15px;font-weight:bold;}QPushButton:hover{background:#1d4ed8;}")

        sl.addWidget(self.si); sl.addWidget(sb); sl.addWidget(clr)
        sl.addWidget(self.edit_btn); sl.addWidget(self.add_btn)
        layout.addWidget(sf)

        # 地图
        self.canvas=MapCanvas()
        self.canvas.clicked.connect(self.on_click)
        self.canvas.poi_clicked.connect(self.on_poi_dblclick)

        # 结果
        self.rl=QListWidget(); self.rl.hide(); self.rl.setMaximumHeight(250)
        self.rl.setStyleSheet("QListWidget{border:1px solid #e2e8f0;border-radius:8px;font-size:14px;}QListWidget::item{padding:10px 16px;border-bottom:1px solid #f0f0f0;}QListWidget::item:hover{background:#eff6ff;}")
        self.rl.itemClicked.connect(self.on_result)

        layout.addWidget(self.canvas,1); layout.addWidget(self.rl)

        # 状态栏
        self.slbl=QLabel("📍 点击地图选起点 | 🔍 搜索目的地 | 右键/ Esc 清除 | 🖱 滚轮缩放")
        self.slbl.setStyleSheet("padding:6px 12px;background:#1e293b;color:#94a3b8;font-size:12px;")
        layout.addWidget(self.slbl)

        self.usr=None; self.dst=None; self._worker=None; self._add_mode=False

        QTimer.singleShot(200,self.init_data)

    def init_data(self):
        self.canvas.load_roadnet(); QTimer.singleShot(500,self.canvas.fit_campus)

    def do_search(self):
        q=self.si.text().strip(); results=search_places(q); self.rl.clear()
        if results:
            for p in results:
                it=QListWidgetItem(f"{p['name']}  —  {p.get('detail','')}"); it.setData(Qt.UserRole,p); self.rl.addItem(it)
            self.rl.show()
        else: self.rl.hide()

    def on_result(self,item):
        place=item.data(Qt.UserRole); self.rl.hide(); self.si.setText(place["name"])
        self.dst=place; self.canvas.dst_marker=(place["lat"],place["lng"])
        if self.usr: self.do_route()
        else: self.canvas.update(); self.slbl.setText(f"🎯 目的地: {place['name']} | 点击地图选起点")

    def on_click(self,lat,lng):
        if lat is None: self.clear_all(); return  # 右键清除
        if self._add_mode:
            self._add_mode=False; self.add_btn.setText("+"); self.add_btn.setStyleSheet(self.add_btn.styleSheet().replace("background:#fef08a","background:#2563eb"))
            self.canvas.setCursor(Qt.ArrowCursor)
            self._show_edit_dialog(None,lat,lng); return
        self.usr=(lat,lng); self.canvas.usr=(lat,lng); self.canvas.route=None; self.canvas.route_text=""
        if self.dst: self.do_route()
        else: self.canvas.update(); self.slbl.setText(f"📍 起点: ({lat:.5f},{lng:.5f}) | 搜索目的地")

    def on_poi_dblclick(self,pid):
        pl=next((p for p in PLACES if p["id"]==pid),None)
        if pl: self._show_edit_dialog(pl,pl["lat"],pl["lng"])

    def _show_edit_dialog(self,place,lat,lng):
        d=QDialog(self); d.setWindowTitle("编辑地点" if place else "新增地点")
        d.resize(380,280)
        layout=QVBoxLayout(d); layout.setSpacing(8)

        layout.addWidget(QLabel("名称")); name=QLineEdit(place["name"] if place else ""); layout.addWidget(name)
        layout.addWidget(QLabel("关键词（逗号分隔）"))
        kw=QLineEdit("，".join(place.get("keywords",[])) if place else ""); layout.addWidget(kw)
        layout.addWidget(QLabel("详情")); det=QLineEdit(place.get("detail","") if place else ""); layout.addWidget(det)

        btns=QHBoxLayout()
        if place:
            del_btn=QPushButton("删除"); del_btn.setStyleSheet("color:#ef4444;")
            def do_del():
                PLACES.remove(place); save_places(); d.accept(); self.canvas.update()
            del_btn.clicked.connect(do_del); btns.addWidget(del_btn)
        btns.addStretch()
        cancel=QPushButton("取消"); cancel.clicked.connect(d.reject); btns.addWidget(cancel)
        ok=QPushButton("保存"); ok.setStyleSheet("background:#2563eb;color:white;"); btns.addWidget(ok)
        def do_save():
            n=name.text().strip()
            if not n: return
            kws=[k.strip() for k in kw.text().replace(",","，").split("，") if k.strip()] or [n]
            if place:
                place["name"]=n; place["keywords"]=kws; place["detail"]=det.text()
            else:
                new_id=max(p["id"] for p in PLACES)+1 if PLACES else 1
                PLACES.append({"id":new_id,"name":n,"keywords":kws,"detail":det.text(),"lat":lat,"lng":lng})
            save_places(); d.accept(); self.canvas.update()
            if not place: self.slbl.setText(f"✅ 已新增: {n}")
        ok.clicked.connect(do_save)
        layout.addLayout(btns)
        d.exec()

    def do_route(self):
        if not self.usr or not self.dst: return
        self.slbl.setText("计算路线中..."); QApplication.processEvents()
        self._worker=RouteWorker(self.usr[0],self.usr[1],self.dst["lat"],self.dst["lng"])
        self._worker.done.connect(self.on_route); self._worker.start()

    def on_route(self,r):
        if r:
            self.canvas.route=r["coords"]; self.canvas.route_text=f"  {r['dist_km']} km  ·  {r['time_m']} 分钟"
            self.slbl.setText(f"✅ {self.dst['name']} | {r['dist_km']}km | 约{r['time_m']}分钟")
        else: self.canvas.route=None; self.canvas.route_text=""; self.slbl.setText("❌ 找不到可达路径")
        self.canvas.update()

    def clear_all(self):
        self.usr=None; self.dst=None; self.canvas.usr=None; self.canvas.dst_marker=None
        self.canvas.route=None; self.canvas.route_text=""; self.si.clear(); self.rl.hide()
        self.canvas.update(); self.slbl.setText("📍 点击地图选起点 | 🔍 搜索目的地 | 右键/ Esc 清除")

    def toggle_edit(self):
        self.canvas.edit_mode=not self.canvas.edit_mode; self.canvas.update()

    def start_add(self):
        self._add_mode=not self._add_mode
        if self._add_mode:
            self.add_btn.setText("🎯"); self.add_btn.setStyleSheet("QPushButton{background:#fef08a;color:#854d0e;border:none;border-radius:8px;padding:10px 14px;font-size:15px;}")
            self.canvas.setCursor(Qt.CrossCursor); self.slbl.setText("🎯 点击地图选择新地点位置")
        else:
            self.add_btn.setText("+"); self.add_btn.setStyleSheet("QPushButton{background:#2563eb;color:white;border:none;border-radius:8px;padding:10px 14px;font-size:15px;}")
            self.canvas.setCursor(Qt.ArrowCursor); self.slbl.setText("")

    def keyPressEvent(self,e):
        if e.key()==Qt.Key_Escape: self.clear_all()

if __name__=="__main__":
    app=QApplication(sys.argv); app.setStyle("Fusion")
    w=MainWindow(); w.show(); sys.exit(app.exec())
