# -*- mode: python ; coding: utf-8 -*-
"""
E刻校园 轻量桌面版打包配置
只打包 flask + networkx + shapely + webview，排除 osmnx/geopandas/gdal/pandas 等重库。

运行方式（从项目根目录）：
    pyinstaller src/light.spec
"""
from PyInstaller.utils.hooks import copy_metadata
from pathlib import Path

# 项目根目录：PyInstaller 提供 SPECPATH（spec 所在目录 src/），其上一级即项目根目录
ROOT = Path(SPECPATH).resolve().parent

# 需要随包携带的元数据（保证 flask 等能找到版本/静态资源）
datas = []
for pkg in ["flask", "networkx", "shapely", "webview", "markupsafe", "jinja2", "itsdangerous", "click", "blinker", "werkzeug"]:
    try:
        datas += copy_metadata(pkg)
    except Exception:
        pass

# 数据与前端资源（data 排除 backup/候选临时文件），基于项目根目录
datas += [
    (str(ROOT / "data/ecnu_walk_merged.graphml"), "data"),
    (str(ROOT / "data/campus_places.json"), "data"),
    (str(ROOT / "data/ecnu_edges_merged.geojson"), "data"),
    (str(ROOT / "data/campus_boundary.geojson"), "data"),
    (str(ROOT / "static/index.html"), "static"),
    (str(ROOT / "static/tiles"), "static/tiles"),
]

a = Analysis(
    [str(ROOT / "src/light_desktop.py")],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=[
        "webview",
        # OSX / Win 渲染后端，pywebview 按平台动态选
        "webview.platforms.winforms",
        "webview.platforms.edgechromium",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # 排除重依赖及其连带，显著缩小体积
        "osmnx", "geopandas", "pandas", "pyogrio", "fiona",
        "osgeo", "rasterio", "pyproj", "matplotlib", "folium", "seaborn",
        "sklearn", "scipy", "sqlalchemy", "psycopg2", "psycopg2-binary",
        "PyQt5", "PySide6", "PySide2", "PyQt6", "tkinter", "IPython",
        "pytest", "nose", "setuptools",
    ],
    noarchive=False,
    optimize=1,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="ECNU-Walk",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,                 # 无黑窗
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
