# -*- coding: utf-8 -*-
"""注入 base64 图片，生成最终 程序说明.html"""
import base64, io, os

proj = r"E:\软件开发\洗衣管家上传助手"

tpl = io.open(os.path.join(proj, "构建工具", "manual_template.html"), encoding="utf-8").read()

def b64(path):
    data = open(path, "rb").read()
    return "data:image/png;base64," + base64.b64encode(data).decode("ascii")

img_main = b64(os.path.join(proj, "文档素材", "主界面截图.png"))
img_cal = b64(os.path.join(proj, "文档素材", "校准窗口截图.png"))

out = tpl.replace("{{IMG_MAIN}}", img_main).replace("{{IMG_CAL}}", img_cal)
dst = os.path.join(proj, "程序说明.html")
io.open(dst, "w", encoding="utf-8").write(out)
print("written:", dst, os.path.getsize(dst), "bytes")
