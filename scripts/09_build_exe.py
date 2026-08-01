"""
打包桌面版为 .exe（打包过程需要几分钟）
用法：python scripts/09_build_exe.py
"""

import subprocess, sys
from pathlib import Path

print("=" * 60)
print("打包 ECNU Walk 桌面版为 .exe")
print("=" * 60)

PROJECT_DIR = Path(__file__).parent.parent

# 构建 PyInstaller 命令，直接加目录
cmd = [
    sys.executable, "-m", "PyInstaller",
    "--onefile",
    "--windowed",
    "--name", "ECNU-Walk",
    "--clean",
    "--noconfirm",
    "--add-data", f"data;data",
    "--add-data", f"static;static",
    "--exclude-module", "PyQt5",
    "--exclude-module", "PySide6",
    "--exclude-module", "PySide2",
    "--exclude-module", "tkinter",
    "desktop_app.py"
]

print(f"Command: {' '.join(cmd[:8])}...")

print(f"\nRunning PyInstaller...")
print(f"(This may take 3-5 minutes)\n")

result = subprocess.run(cmd, cwd=PROJECT_DIR)

if result.returncode == 0:
    exe_path = PROJECT_DIR / "dist" / "ECNU-Walk.exe"
    size_mb = exe_path.stat().st_size / 1024 / 1024
    print(f"\n{'=' * 60}")
    print(f"Packaging successful!")
    print(f"  Output: {exe_path}")
    print(f"  Size:   {size_mb:.0f} MB")
    print(f"\nDouble-click ECNU-Walk.exe to launch!")
else:
    print(f"\nPackaging FAILED (exit code {result.returncode})")
