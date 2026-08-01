"""
第六步：下载校园范围 OSM 瓦片，实现离线底图
用法：python scripts/06_download_tiles.py
"""

import math, os, time, requests, sys, io
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# 校园边界
NORTH, SOUTH = 31.044, 31.024
WEST, EAST = 121.438, 121.472
TILE_DIR = Path("static/tiles")
MIN_ZOOM, MAX_ZOOM = 14, 18

def deg2num(lat, lon, zoom):
    """经纬度 → 瓦片坐标"""
    lat_rad = math.radians(lat)
    n = 2.0 ** zoom
    xtile = int((lon + 180.0) / 360.0 * n)
    ytile = int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n)
    return xtile, ytile

print("=" * 60)
print("Downloading OSM tiles for offline use")
print(f"  Area: {WEST}E-{EAST}E, {SOUTH}N-{NORTH}N")
print(f"  Zooms: {MIN_ZOOM}-{MAX_ZOOM}")
print("=" * 60)

total = 0
downloaded = 0
failed = 0

for zoom in range(MIN_ZOOM, MAX_ZOOM + 1):
    x1, y1 = deg2num(NORTH, WEST, zoom)
    x2, y2 = deg2num(SOUTH, EAST, zoom)
    nx = x2 - x1 + 1
    ny = y2 - y1 + 1
    count = nx * ny
    total += count
    print(f"\nZoom {zoom}: {nx}x{ny} = {count} tiles")

    for x in range(x1, x2 + 1):
        for y in range(y1, y2 + 1):
            tile_path = TILE_DIR / str(zoom) / str(x) / f"{y}.png"
            if tile_path.exists():
                downloaded += 1
                continue

            tile_path.parent.mkdir(parents=True, exist_ok=True)
            url = f"https://tile.openstreetmap.org/{zoom}/{x}/{y}.png"

            try:
                resp = requests.get(url, headers={
                    "User-Agent": "ECNU-Walk/1.0 (student project)"
                }, timeout=10)
                if resp.status_code == 200:
                    tile_path.write_bytes(resp.content)
                    downloaded += 1
                else:
                    failed += 1
                    print(f"  FAIL {zoom}/{x}/{y}: HTTP {resp.status_code}")
            except Exception as e:
                failed += 1
                print(f"  FAIL {zoom}/{x}/{y}: {e}")

            time.sleep(0.05)  # 礼貌限速

print(f"\n{'=' * 60}")
print(f"Done: {downloaded}/{total} downloaded, {failed} failed")
print(f"Tiles in: {TILE_DIR}")

# 估算大小
total_size = sum(f.stat().st_size for f in TILE_DIR.rglob("*.png"))
print(f"Total size: {total_size / 1024 / 1024:.1f} MB")
