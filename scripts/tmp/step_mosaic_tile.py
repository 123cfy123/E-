"""
临时脚本：按瓦片网格用 PIL 拼出校园范围 z18 底图，写成一张 GeoTIFF (EPSG:3857)。
输出: data/campus_basemap_z18.tif
"""
import sys, io, math
from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import from_bounds
from PIL import Image

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

TILE_DIR = Path("static/tiles")
OUT = Path("data/campus_basemap_z18.tif")
Z = 18
TILE = 256

WEST, EAST, SOUTH, NORTH = 121.438, 121.472, 31.024, 31.044


def deg2num(lat, lon, zoom):
    n = 2.0 ** zoom
    x = int((lon + 180.0) / 360.0 * n)
    latr = math.radians(lat)
    y = int((1.0 - math.asinh(math.tan(latr)) / math.pi) / 2.0 * n)
    return x, y


x1, y1 = deg2num(NORTH, WEST, Z)
x2, y2 = deg2num(SOUTH, EAST, Z)
ncol = x2 - x1 + 1
nrow = y2 - y1 + 1
print(f"grid: cols={ncol} rows={nrow}  (x{x1}-{x2}, y{y1}-{y2})", flush=True)

# 整幅画布
canvas = Image.new("RGB", (ncol * TILE, nrow * TILE), (240, 240, 240))
missing = []
for col in range(ncol):
    x = x1 + col
    for row in range(nrow):
        y = y1 + row
        p = TILE_DIR / str(Z) / str(x) / f"{y}.png"
        if p.exists():
            tile = Image.open(p).convert("RGB")
            canvas.paste(tile, (col * TILE, row * TILE))
        else:
            missing.append((x, y))

if missing:
    print(f"WARN {len(missing)} missing tiles: {missing[:5]}", flush=True)

# Web Mercator 拼接范围
xm = 20037508.34
n = 2 ** Z
tile_w = (2 * xm) / n
xmin = -xm + x1 * tile_w
xmax = -xm + (x2 + 1) * tile_w
ymax = xm - y1 * tile_w
ymin = xm - (y2 + 1) * tile_w
print(f"bbox: {xmin:.2f},{ymin:.2f} -> {xmax:.2f},{ymax:.2f}", flush=True)

arr = np.asarray(canvas)
OUT.parent.mkdir(parents=True, exist_ok=True)
transform = from_bounds(xmin, ymin, xmax, ymax, ncol * TILE, nrow * TILE)
with rasterio.open(
    OUT, "w", driver="GTiff", height=nrow * TILE, width=ncol * TILE,
    count=3, dtype="uint8", transform=transform, crs="EPSG:3857",
    tiled=True, compress="deflate"
) as dst:
    dst.write(arr[:, :, 0], 1)
    dst.write(arr[:, :, 1], 2)
    dst.write(arr[:, :, 2], 3)
    dst.set_band_description(1, "red")
    dst.set_band_description(2, "green")
    dst.set_band_description(3, "blue")

import os
print(f"[OK] {OUT}  size={os.path.getsize(OUT)/1024/1024:.1f} MB", flush=True)
