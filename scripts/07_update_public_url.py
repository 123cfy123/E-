"""
自动获取 ngrok 公网地址，更新 README.md 并推送到 GitHub
用法：python scripts/07_update_public_url.py
"""

import requests, re, sys, io, subprocess
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# 1. 获取 ngrok 当前公网地址
try:
    resp = requests.get("http://127.0.0.1:4040/api/tunnels", timeout=5)
    data = resp.json()
    url = None
    for t in data.get("tunnels", []):
        u = t.get("public_url", "")
        if u.startswith("https"):
            url = u
            break
    if not url:
        print("ERROR: ngrok 未运行或未获取到 HTTPS 地址")
        sys.exit(1)
except Exception as e:
    print(f"ERROR: 无法连接 ngrok API ({e})")
    sys.exit(1)

print(f"Ngrok URL: {url}")

# 2. 更新 README.md
readme_path = Path("README.md")
content = readme_path.read_text(encoding="utf-8")

# 替换公网地址行
new_line = f"> 公网访问：**[{url}]({url})**（实时更新）\n"
pattern = r"> 公网访问.*\n"

if re.search(pattern, content):
    content = re.sub(pattern, new_line, content)
else:
    # 在 "## 快速启动" 前面插入
    content = content.replace(
        "## 快速启动",
        new_line + "\n## 快速启动"
    )

readme_path.write_text(content, encoding="utf-8")

# 3. Git 提交并推送（仅在有变更时）
import hashlib
subprocess.run(["git", "add", "README.md"], check=True)
diff = subprocess.run(["git", "diff", "--cached", "--quiet", "README.md"])
if diff.returncode != 0:
    subprocess.run(["git", "commit", "-m", f"Update public URL: {url}"], check=True)
    subprocess.run(["git", "push"], check=True)
    print("Pushed to GitHub!")
else:
    print("URL unchanged, no push needed.")
print(f"  公网地址: {url}")
