# -*- coding: utf-8 -*-
"""组装打包 payload：exe + _internal + assets + anchors + config + 文档。"""
import os, shutil

proj = r"E:\软件开发\洗衣管家上传助手"
pkg = r"E:\软件开发\.openclaw\tmp\pkg"
dist = os.path.join(pkg, "dist", "洗衣管家上传助手")

print("dist exists:", os.path.exists(dist))
print("dist contents:", os.listdir(dist)[:10])

for item in ("assets", "anchors"):
    src = os.path.join(proj, item)
    dst = os.path.join(dist, item)
    shutil.copytree(src, dst, dirs_exist_ok=True)
    print("copied dir:", item, "->", len(os.listdir(dst)), "files")

for f in ("config.json", "README.md", "程序说明.html", "启动洗衣管家-调试模式.bat"):
    src = os.path.join(proj, f)
    if os.path.exists(src):
        shutil.copy2(src, os.path.join(dist, f))
        print("copied:", f)

total = 0
for root, dirs, files in os.walk(dist):
    for f in files:
        total += os.path.getsize(os.path.join(root, f))
print(f"payload total size: {total/1024/1024:.1f} MB")
