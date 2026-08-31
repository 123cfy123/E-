"""
E刻校园 轻量桌面版入口
后台线程启动轻量后端(flask)，pywebview 加载原生窗口。
无 osmnx/gdal 重依赖，打包体积小。
"""
import threading
import time
import sys

import webview
from light_backend import app, init


def start_flask():
    """后台线程启动轻量 Flask"""
    init()
    app.run(host="127.0.0.1", port=5000, debug=False, use_reloader=False)


def main():
    flask_thread = threading.Thread(target=start_flask, daemon=True)
    flask_thread.start()

    import requests
    for _ in range(40):
        try:
            requests.get("http://127.0.0.1:5000/api/health", timeout=1)
            break
        except Exception:
            time.sleep(0.4)

    window = webview.create_window(
        title="E刻校园 - 华师大闵行校区智能导航",
        url="http://127.0.0.1:5000",
        width=1200,
        height=800,
        min_size=(800, 600),
        text_select=False,
        confirm_close=False,
    )
    webview.start(debug=False)


if __name__ == "__main__":
    main()
