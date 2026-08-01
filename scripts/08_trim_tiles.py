"""
仅保留校园边界内的瓦片，删除校外瓦片，减少 ngrok 传输量
"""

import json, math, sys, io
from pathlib import Path
from shapely.geometry import Polygon, box

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

TILE_DIR = Path("static/tiles")

# 加载校园边界
with open("data/campus_boundary.geojson") as f:
    coords = json.load(f)["features"][0]["geometry"]["coordinates"][0]
campus = Polygon(coords)

def tile_bounds(z, x, y):
    """瓦片坐标 → WGS-84 矩形"""
    n = 2.0 ** z
    lng1 = x / n * 360.0 - 180.0
    lng2 = (x + 1) / n * 360.0 - 180.0
    lat1 = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))
    lat2 = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (y + 1) / n))))
    return box(lng1, lat2, lng2, lat1)  # shapely box(minx, miny, maxx, maxy)

total = 0
kept = 0
removed = 0

for png in sorted(TILE_DIR.rglob("*.png")):
    total += 1
    parts = png.parts
    y = int(parts[-1].replace(".png", ""))
    x = int(parts[-2])
    z = int(parts[-3])

    tb = tile_bounds(z, x, y)
    if campus.intersects(tb):
        kept += 1
    else:
        png.unlink()
        removed += 1
        # 清理空目录
        for parent in [png.parent, png.parent.parent]:
            if parent != TILE_DIR and not any(parent.iterdir()):
                parent.rmdir()

print(f"Total tiles: {total}")
print(f"Kept (inside campus): {kept}")
print(f"Removed (outside): {removed}")

# 重新计算大小
size = sum(f.stat().st_size for f in TILE_DIR.rglob("*.png"))
print(f"New size: {size / 1024:.0f} KB ({size / 1024 / 1024:.1f} MB)")
