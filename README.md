# E刻校园 (ECNU Walk)

华东师范大学闵行校区智能步行导航。

输入"打印成绩单""打篮球""补办校园卡"，自动匹配目的地，在地图上规划最短步行路线。

## 功能

- 自然语言搜索校园地点（55 个 POI）
- 基于真实步行路网的最短路径规划
- 浏览器 GPS 定位 + 手动选点
- 地图旋转、离线瓦片缓存
- 可视化编辑：拖拽移动地点、新增/删除/修改

## 技术栈

| 层 | 技术 |
|---|------|
| 前端 | MapLibre GL JS（WebGL 地图） |
| 后端 | Python Flask |
| 路由 | NetworkX Dijkstra 最短路径 |
| 路网 | OpenStreetMap + QGIS 手动补全 |
| 底图 | OpenStreetMap 瓦片（本地缓存） |

## 快速启动

```bash
# 1. 安装环境
conda env create -f environment.yml
conda activate ecnu-walk

# 2. 下载离线瓦片（可选，加速地图加载）
python scripts/06_download_tiles.py

# 3. 启动
python app.py
# → http://127.0.0.1:5000
```

## 项目结构

```
├── app.py                    # Flask 后端 API
├── static/index.html         # 前端单页应用
├── environment.yml           # Conda 环境配置
├── data/
│   ├── ecnu_walk_merged.graphml    # 路由图 (1154节点 / 2602边)
│   ├── ecnu_edges_merged.geojson   # 路段几何 (1301条)
│   ├── campus_places.json          # 校园地点 (55个)
│   └── campus_boundary.geojson     # 校园边界
└── scripts/
    ├── 01_download_network.py      # 下载 OSM 路网
    ├── 02_merge_paths.py           # 合并 QGIS 手动路径
    ├── 05_rebuild_graph.py         # 编辑路网后重建图
    └── 06_download_tiles.py        # 下载离线瓦片
```

## 编辑自己的路网

1. QGIS 打开 `data/ecnu_edges_merged.geojson`
2. 增删路段后保存
3. 运行 `python scripts/05_rebuild_graph.py`
4. 刷新网页

## API

| 端点 | 说明 |
|------|------|
| `GET /api/search?q=食堂` | 搜索地点 |
| `GET /api/route?from_lat=...&from_lng=...&to_lat=...&to_lng=...` | 路径规划 |
| `GET /api/places` | 地点列表 |
| `POST /api/places` | 新增地点 |
| `PUT /api/places/<id>` | 编辑地点 |
| `DELETE /api/places/<id>` | 删除地点 |

## License

MIT
