"""
临时脚本：下载校园范围 z18 OSM 瓦片到 static/tiles，供拼接本地底图。
"""
import requests, math, time, sys, io
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

TILE_DIR = Path("static/tiles")
WEST, EAST, SOUTH, NORTH = 121.438, 121.472, 31.024, 31.044
Z = 18

HEADERS = {"User-Agent": "ECNU-Walk/1.0 (student project)"}


def deg2num(lat, lon, zoom):
    n = 2.0 ** zoom
    x = int((lon + 180.0) / 360.0 * n)
    latr = math.radians(lat)
    y = int((1.0 - math.asinh(math.tan(latr)) / math.pi) / 2.0 * n)
    return x, y


def download(x, y, retries=3):
    p = TILE_DIR / str(Z) / str(x) / f"{y}.png"
    if p.exists():
        return True
    p.parent.mkdir(parents=True, exist_ok=True)
    url = f"https://tile.openstreetmap.org/{Z}/{x}/{y}.png"
    for attempt in range(retries):
        try:
            r = requests.get(url, headers=HEADERS, timeout=15)
            if r.status_code == 200:
                p.write_bytes(r.content)
                return True
            time.sleep(0.4)
        except Exception as e:
            if attempt == retries - 1:
                print(f"  FAIL {Z}/{x}/{y}: {e}", flush=True)
            time.sleep(0.4)
    return False


x1, y1 = deg2num(NORTH, WEST, Z)
x2, y2 = deg2num(SOUTH, EAST, Z)
total_tiles = (x2 - x1 + 1) * (y2 - y1 + 1)
print(f"z{Z}: x{x1}-{x2} y{y1}-{y2} total={total_tiles}", flush=True)

ok, fail = 0, 0
for x in range(x1, x2 + 1):
    for y in range(y1, y2 + 1):
        if download(x, y):
            ok += 1
        else:
            fail += 1
        time.sleep(0.05)  # 礼貌限速
    print(f"  row x={x} done ok={ok} fail={fail}", flush=True)

print(f"\nDONE ok={ok} fail={fail} total={total_tiles}", flush=True)
