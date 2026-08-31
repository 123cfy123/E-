"""
E刻校园 桌面版
双击运行，原生窗口，无需浏览器
"""

import threading
import webview
import time
from app import app, init


def start_flask():
    """后台线程启动 Flask"""
    init()
    app.run(host="127.0.0.1", port=5000, debug=False, use_reloader=False)


def main():
    # 1. 启动 Flask 后台线程
    flask_thread = threading.Thread(target=start_flask, daemon=True)
    flask_thread.start()

    # 2. 等待 Flask 就绪
    import requests
    for _ in range(30):
        try:
            requests.get("http://127.0.0.1:5000/api/health", timeout=1)
            break
        except Exception:
            time.sleep(0.5)

    # 3. 创建原生窗口
    window = webview.create_window(
        title="E刻校园 - 华师大闵行校区智能导航",
        url="http://127.0.0.1:5000",
        width=1200,
        height=800,
        min_size=(800, 600),
        text_select=False,
        confirm_close=False
    )

    # 4. 启动
    webview.start(debug=False)


if __name__ == "__main__":
    main()
