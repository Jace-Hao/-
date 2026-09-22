# -*- coding: utf-8 -*-
"""PyInstaller 打包构建脚本。"""
import subprocess, sys, os

proj = r"E:\软件开发\洗衣管家上传助手"
pkg = r"E:\软件开发\.openclaw\tmp\pkg"
os.makedirs(pkg, exist_ok=True)

cmd = [sys.executable, "-m", "PyInstaller",
       "--noconfirm", "--clean", "--windowed", "--uac-admin",
       "--name", "洗衣管家上传助手",
       "--icon", os.path.join(proj, "assets", "logo.ico"),
       "--distpath", os.path.join(pkg, "dist"),
       "--workpath", os.path.join(pkg, "build"),
       "--specpath", pkg,
       "--hidden-import", "pyperclip",
       "--hidden-import", "pygetwindow",
       "--hidden-import", "keyboard",
       "--hidden-import", "openpyxl",
       "--hidden-import", "websocket",
       "--hidden-import", "cv2",
       "--hidden-import", "pyscreeze",
       "--hidden-import", "mouseinfo",
       "--hidden-import", "pymsgbox",
       "--hidden-import", "pytweening",
       os.path.join(proj, "main.py")]

print("BUILD START:", cmd[-1])
r = subprocess.run(cmd, cwd=proj)
print("BUILD EXIT:", r.returncode)
