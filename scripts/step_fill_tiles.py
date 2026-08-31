"""
补全校园范围 z14-z17 的 OSM 瓦片，让离线底图完整覆盖多级缩放。
(只在缺失时下载，已有则跳过)
"""
import requests, math, time, sys, io
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

TILE_DIR = Path("static/tiles")
WEST, EAST, SOUTH, NORTH = 121.438, 121.472, 31.024, 31.044
ZOOMS = [14, 15, 16, 17]
HEADERS = {"User-Agent": "ECNU-Walk/1.0 (student project)"}


def deg2num(lat, lon, zoom):
    n = 2.0 ** zoom
    x = int((lon + 180.0) / 360.0 * n)
    latr = math.radians(lat)
    y = int((1.0 - math.asinh(math.tan(latr)) / math.pi) / 2.0 * n)
    return x, y


def download(z, x, y, retries=3):
    p = TILE_DIR / str(z) / str(x) / f"{y}.png"
    if p.exists():
        return True
    p.parent.mkdir(parents=True, exist_ok=True)
    url = f"https://tile.openstreetmap.org/{z}/{x}/{y}.png"
    for attempt in range(retries):
        try:
            r = requests.get(url, headers=HEADERS, timeout=15)
            if r.status_code == 200:
                p.write_bytes(r.content)
                return True
            time.sleep(0.4)
        except Exception as e:
            if attempt == retries - 1:
                print(f"  FAIL {z}/{x}/{y}: {e}", flush=True)
            time.sleep(0.4)
    return False


for z in ZOOMS:
    x1, y1 = deg2num(NORTH, WEST, z)
    x2, y2 = deg2num(SOUTH, EAST, z)
    total = (x2 - x1 + 1) * (y2 - y1 + 1)
    ok = fail = 0
    for x in range(x1, x2 + 1):
        for y in range(y1, y2 + 1):
            if download(z, x, y):
                ok += 1
            else:
                fail += 1
            time.sleep(0.04)
    print(f"z{z}: {ok}/{total} ok, {fail} fail", flush=True)

print("DONE", flush=True)
