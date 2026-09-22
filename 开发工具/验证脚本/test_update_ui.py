# -*- coding: utf-8 -*-
"""test_update_ui.py — 界面「更新下载」文案渲染测试。

模拟：发现新版本 → 直连下载 → 自动切换国内加速 → 进度 42%。
检查状态栏是否显示「国内加速」与进度、日志是否记录换源。
运行后约 7 秒自动关闭；如需截图核验，可在运行时对窗口截图。
用法：py -3.14 test_update_ui.py
"""
import os
import sys
import tkinter as tk

proj = r'E:\软件开发\洗衣管家上传助手'
sys.path.insert(0, proj)
os.chdir(proj)
import main as m  # noqa: E402

OUT_STATE = os.path.join(os.environ.get('TEMP', r'C:\Windows\Temp'), 'ui2_state.txt')

app = m.App()


def inject1():
    app.update_info = {'latest': '9.9', 'asset_url': 'https://example.com/x.exe', 'asset_name': 'X.exe'}
    app.msg_queue.put(('update_source', (1, 7, 'github.com')))
    app.msg_queue.put(('update_progress', 3))
    app.after(800, inject2)


def inject2():
    app.msg_queue.put(('update_source', (2, 7, 'gh-proxy.com')))
    app.msg_queue.put(('update_progress', 42))


def inject3():
    app.msg_queue.put(('update_source', (2, 7, 'gh-proxy.com')))
    app.after(300, lambda: app.msg_queue.put(('update_progress', 42)))


def dump_state():
    try:
        status_txt = app.status.get()
    except Exception as e:
        status_txt = 'status err: %s' % e
    texts = []

    def walk(w):
        for ch in w.winfo_children():
            if isinstance(ch, tk.Text):
                try:
                    texts.append(ch.get('1.0', 'end'))
                except Exception:
                    pass
            walk(ch)

    walk(app)
    try:
        with open(OUT_STATE, 'w', encoding='utf-8') as f:
            f.write('STATUS: ' + str(status_txt) + '\n')
            for i, t in enumerate(texts):
                f.write('--- TEXT #%d ---\n' % i + t)
    except Exception as e:
        print('dump err:', e)
    print('STATUS:', status_txt)
    app.destroy()


app.after(1200, inject1)
app.after(4500, inject3)
app.after(6800, dump_state)
app.mainloop()
print('UI TEST DONE')
