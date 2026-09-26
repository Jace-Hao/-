# -*- coding: utf-8 -*-
"""
main.py — 洗衣管家 · 照片批量上传助手（图形界面主程序）
================================================================
功能总览（与需求对应）：
  1. 有操作界面（本窗口）；
  2. 文件导入功能：导入“导出清单”CSV → 自动识别编码/表头/条码/文件夹路径；
  3. 按识别结果执行程序：按“绘图1”流程图的操作序列自动操作洗衣管家软件；
  4. 输出执行结果：界面实时状态 + 日志 + 自动导出 CSV / Excel 结果文件。

运行方式（Windows）：
    双击「启动程序.bat」  或  命令行 py -3 main.py
"""
import os
import sys
import time
import json
import queue
import subprocess
import threading
import traceback
import urllib.request
import webbrowser

if getattr(sys, "frozen", False):
    # PyInstaller 打包后：配置/锚点/输出都放在 exe 同级目录
    BASE_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ---------- DPI 感知（避免高缩放屏幕下坐标偏移） ----------
def _setup_dpi():
    try:
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PER_MONITOR_AWARE_V2
        except Exception:
            ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

_setup_dpi()

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from tkinter.scrolledtext import ScrolledText

from config_io import load_config, save_config
from csv_import import import_list, TaskRecord
from ui_theme import PALETTE as C, FONTS, STATUS_COLORS, apply_theme
import updater

APP_TITLE = "洗衣管家 · 照片批量上传助手"
VERSION = "1.11"

_NO_WINDOW = 0x08000000  # subprocess.CREATE_NO_WINDOW


def _run_hidden(cmd, timeout=15):
    """静默子进程调用（CREATE_NO_WINDOW，避免控制台窗口闪动/干扰）。"""
    try:
        return subprocess.run(cmd, capture_output=True, timeout=timeout,
                              creationflags=_NO_WINDOW)
    except Exception:
        return None

COLOR_OK = STATUS_COLORS["成功"]
COLOR_FAIL = STATUS_COLORS["失败"]
COLOR_SKIP = STATUS_COLORS["跳过"]
COLOR_RUN = STATUS_COLORS["进行中"]


class MonitorWindow(tk.Toplevel):
    """执行监视悬浮窗：上传执行期间始终置顶，实时显示进度 / 当前条码 / 计数 / 耗时 / 日志。
    可拖动、可最小化，带暂停 / 停止快捷按钮；采用 WS_EX_NOACTIVATE 不抢占焦点，
    不会干扰对洗衣管家的自动化操作。"""

    WIDTH = 352

    def __init__(self, master, on_pause=None, on_stop=None):
        super().__init__(master)
        self._on_pause = on_pause
        self._on_stop = on_stop
        self.closed = False
        self._minimized = False
        self._t0 = time.time()
        self._total = 1
        self._logs = []
        self.configure(bg=C["accent"])
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.withdraw()
        self._build()
        self._place_bottom_right()
        self.after(1000, self._tick)

    # ---------- 构建 ----------
    def _build(self):
        self._body = tk.Frame(self, bg=C["surface"], highlightthickness=1,
                              highlightbackground=C["accent_line"])
        self._body.pack(fill="both", expand=True)
        head = tk.Frame(self._body, bg=C["accent"], cursor="fleur")
        head.pack(fill="x")
        head.bind("<Button-1>", self._drag_start)
        head.bind("<B1-Motion>", self._drag_move)
        tk.Label(head, text="执行监视", bg=C["accent"], fg="#FFFFFF",
                 font=FONTS["card"]).pack(side="left", padx=(12, 6), pady=6)
        self._badge = tk.Label(head, text="", bg=C["accent"], fg="#DFF2D0",
                               font=FONTS["chip"])
        self._badge.pack(side="left")
        tk.Button(head, text="✕", command=self.close, bd=0, bg=C["accent"],
                  fg="#E8F5DC", activebackground=C["accent_hover"],
                  activeforeground="#FFFFFF", font=FONTS["small"],
                  cursor="hand2").pack(side="right", padx=(2, 8), pady=4)
        tk.Button(head, text="—", command=self.toggle_min, bd=0, bg=C["accent"],
                  fg="#E8F5DC", activebackground=C["accent_hover"],
                  activeforeground="#FFFFFF", font=FONTS["small"],
                  cursor="hand2").pack(side="right", pady=4)
        self._detail = tk.Frame(self._body, bg=C["surface"])
        self._detail.pack(fill="both", expand=True)
        box = tk.Frame(self._detail, bg=C["surface"])
        box.pack(fill="x", padx=14, pady=(10, 2))
        style_bar = "Monitor.Horizontal.TProgressbar"
        s = ttk.Style(self)
        s.configure(style_bar, thickness=10, troughcolor=C["accent_soft"],
                    background=C["accent"], bordercolor=C["accent_line"],
                    lightcolor=C["accent"], darkcolor=C["accent"])
        self._bar = ttk.Progressbar(box, mode="determinate", maximum=100,
                                    style=style_bar)
        self._bar.pack(fill="x")
        self._count = tk.Label(box, text="准备中…", bg=C["surface"], fg=C["ink"],
                               font=FONTS["ui"], anchor="w")
        self._count.pack(fill="x", pady=(6, 0))
        self._cur = tk.Label(box, text="等待开始…", bg=C["surface"], fg=C["ink_2"],
                             font=FONTS["small"], anchor="w")
        self._cur.pack(fill="x", pady=(1, 0))
        self._log = tk.Label(self._detail, text="", bg=C["surface_soft"], fg=C["muted"],
                             font=FONTS["log"], anchor="nw", justify="left",
                             wraplength=self.WIDTH - 32)
        self._log.pack(fill="x", padx=14, pady=(6, 4))
        btns = tk.Frame(self._detail, bg=C["surface"])
        btns.pack(fill="x", padx=14, pady=(2, 10))
        self._btn_pause = tk.Button(btns, text="暂停", command=self._do_pause,
                                    bd=0, bg=C["accent_soft"], fg=C["accent_ink"],
                                    activebackground=C["accent_line"],
                                    font=FONTS["small"], cursor="hand2", padx=12)
        self._btn_pause.pack(side="left")
        self._btn_stop = tk.Button(btns, text="停止", command=self._do_stop,
                                   bd=0, bg=C["danger_soft"], fg=C["danger"],
                                   activebackground=C["danger_line"],
                                   font=FONTS["small"], cursor="hand2", padx=12)
        self._btn_stop.pack(side="left", padx=(8, 0))
        self._elapsed = tk.Label(btns, text="", bg=C["surface"], fg=C["muted"],
                                 font=FONTS["small"])
        self._elapsed.pack(side="right")

    # ---------- 窗口行为 ----------
    def _place_bottom_right(self):
        self.update_idletasks()
        h = max(self.winfo_reqheight(), 120)
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry(f"{self.WIDTH}x{h}+{sw - self.WIDTH - 24}+{sh - h - 72}")

    def _noactivate(self):
        """WS_EX_NOACTIVATE：窗口永不夺焦点，点击也只触发回调。"""
        try:
            import ctypes
            user32 = ctypes.windll.user32
            hwnd = user32.GetParent(self.winfo_id())
            style = user32.GetWindowLongPtrW(hwnd, -20)      # GWL_EXSTYLE
            user32.SetWindowLongPtrW(hwnd, -20,
                                     style | 0x08000000 | 0x00000008)  # NOACTIVATE|TOPMOST
        except Exception:
            pass

    def show(self):
        if self.closed:
            return
        self.deiconify()
        self._noactivate()
        try:
            import ctypes
            user32 = ctypes.windll.user32
            hwnd = user32.GetParent(self.winfo_id())
            # HWND_TOPMOST + SWP_NOACTIVATE：置顶但不激活
            user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x2 | 0x1 | 0x10)
        except Exception:
            pass
        self.lift()
        try:
            self.master.focus_set()      # 焦点还给主窗口
        except Exception:
            pass

    def close(self):
        self.closed = True
        try:
            self.destroy()
        except Exception:
            pass

    def _tick(self):
        if self.closed:
            return
        try:
            self.attributes("-topmost", True)   # 定时重申置顶
        except Exception:
            pass
        if not self._minimized:
            sec = int(time.time() - self._t0)
            self._elapsed.configure(text=f"已用时 {sec // 60:02d}:{sec % 60:02d}")
        self.after(1000, self._tick)

    def _drag_start(self, e):
        self._drag = (e.x, e.y)

    def _drag_move(self, e):
        if getattr(self, "_drag", None):
            self.geometry(f"+{e.x_root - self._drag[0]}+{e.y_root - self._drag[1]}")

    def toggle_min(self):
        self._minimized = not self._minimized
        if self._minimized:
            self._detail.pack_forget()
            self.geometry(f"{self.WIDTH}x{self._body.winfo_reqheight()}")
        else:
            self._detail.pack(fill="both", expand=True)
            self._place_bottom_right()

    def _do_pause(self):
        if self._on_pause:
            self._on_pause()

    def _do_stop(self):
        if self._on_stop:
            self._on_stop()

    # ---------- 数据刷新 ----------
    def set_total(self, total, dry=False):
        self._total = max(int(total), 1)
        self._badge.configure(text="演练模式" if dry else "实时监视")

    def update_counts(self, done, ok, fail, skip):
        pct = min(int(done * 100 / self._total), 100)
        self._bar.configure(value=pct)
        self._count.configure(
            text=f"{done}/{self._total}    成功 {ok}    失败 {fail}    跳过 {skip}")
        if self._minimized:
            self._badge.configure(text=f"{done}/{self._total}")

    def set_current(self, text):
        self._cur.configure(text=text)

    def add_log(self, line):
        self._logs.append(line)
        self._logs = self._logs[-3:]
        self._log.configure(text="\n".join(self._logs))

    def set_paused(self, paused):
        self._btn_pause.configure(text="继续" if paused else "暂停")
        self._cur.configure(text="已暂停（点【继续】恢复）" if paused else "继续执行…")

    def finish(self, text):
        self._cur.configure(text=text)
        self._btn_pause.configure(state="disabled")
        self._btn_stop.configure(state="disabled")
        self._badge.configure(text="已结束")
        self.after(30000, self.close)        # 结束 30 秒后自动收起


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"{APP_TITLE}  v{VERSION}")
        self.geometry("1000x760")
        self.minsize(920, 700)

        self.cfg = load_config(BASE_DIR)
        self.tasks = []
        self.report = None
        self.msg_queue = queue.Queue()
        self.engine = None
        self.worker = None
        self.control = None
        self.result_dir = os.path.join(BASE_DIR, "输出结果")
        os.makedirs(self.result_dir, exist_ok=True)
        self.run_started_at = None
        self.run_log_file = None

        self._setup_style()
        self._setup_brand()
        self._build_ui()
        self._setup_hotkey()
        # 在线更新状态
        self.update_info = None
        self.update_thread = None
        self.update_bar = None
        self.update_label = None
        self.update_accel = False
        self.downloading = False
        self.monitor = None
        if self.cfg["options"].get("check_update_on_start", True):
            self.after(1500, self._auto_check_update)
        self.after(120, self._drain_queue)

    # ================= UI =================
    def _setup_style(self):
        apply_theme(self)
        self.option_add("*Font", FONTS["ui"])
        self.configure(bg=C["bg"])

    def _setup_brand(self):
        """加载品牌 logo（窗口图标 + 顶栏图案）。"""
        asset_dir = os.path.join(BASE_DIR, "assets")
        self._img_icon = None
        self._img_header = None
        try:
            icon_p = os.path.join(asset_dir, "logo.png")
            if os.path.exists(icon_p):
                self._img_icon = tk.PhotoImage(file=icon_p)
                self.iconphoto(True, self._img_icon)
        except Exception:
            self._img_icon = None
        try:
            head_p = os.path.join(asset_dir, "logo_header.png")
            if os.path.exists(head_p):
                img = tk.PhotoImage(file=head_p)
                if img.width() >= 72:   # 原图 96×96，缩为 48×48 更精致
                    img = img.subsample(2, 2)
                self._img_header = img
        except Exception:
            self._img_header = None

    def _new_card(self, parent, step, title, hint=None, expand=False):
        """构建统一卡片容器：编号徽标 + 标题（含右侧提示）+ 分隔线 + 内容区。"""
        card = tk.Frame(parent, bg=C["surface"], highlightthickness=1,
                        highlightbackground=C["line"], highlightcolor=C["line"])
        head = tk.Frame(card, bg=C["surface"])
        head.pack(fill="x", padx=14, pady=(7, 0))
        tk.Label(head, text=str(step), bg=C["accent_soft"], fg=C["accent_ink"],
                 font=FONTS["badge"], padx=7, pady=1).pack(side="left")
        tk.Label(head, text=title, bg=C["surface"], fg=C["ink"],
                 font=FONTS["card"]).pack(side="left", padx=(8, 0))
        if hint:
            tk.Label(head, text=hint, bg=C["surface"], fg=C["muted"],
                     font=FONTS["small"]).pack(side="right")
        tk.Frame(card, bg=C["line_soft"], height=1).pack(fill="x", padx=14, pady=(6, 0))
        body = tk.Frame(card, bg=C["surface"])
        if expand:
            body.pack(fill="both", expand=True, padx=14, pady=7)
        else:
            body.pack(fill="x", padx=14, pady=7)
        return card, body

    def _build_ui(self):
        # ---------- 顶栏：品牌 / 版本 / 模式 ----------
        header = tk.Frame(self, bg=C["surface"], highlightthickness=1,
                          highlightbackground=C["line"], highlightcolor=C["line"])
        header.pack(fill="x", padx=12, pady=(10, 0))
        hrow = tk.Frame(header, bg=C["surface"])
        hrow.pack(fill="x", padx=16, pady=6)
        if self._img_header is not None:
            tk.Label(hrow, image=self._img_header, bg=C["surface"]).pack(side="left", padx=(0, 12))
        tw = tk.Frame(hrow, bg=C["surface"])
        tw.pack(side="left")
        tk.Label(tw, text=APP_TITLE, bg=C["surface"], fg=C["ink"],
                 font=FONTS["title"]).pack(anchor="w")
        chips = tk.Frame(hrow, bg=C["surface"])
        chips.pack(side="right")
        self.mode_chip = tk.Label(chips, text="精准模式", bg=C["accent_soft"], fg=C["accent_ink"],
                                  font=FONTS["chip"], padx=10, pady=2)
        self.mode_chip.pack(side="right")
        tk.Label(chips, text=f"v{VERSION}", bg=C["chip_bg"], fg=C["chip_fg"],
                 font=FONTS["chip"], padx=10, pady=2).pack(side="right", padx=(0, 8))

        # ---------- 步骤提示条 ----------
        self.lbl_steps = tk.Label(self, anchor="w", bg=C["accent_soft"], fg=C["accent_ink"],
                                  font=FONTS["ui"], padx=14, pady=5)
        self.lbl_steps.pack(fill="x", padx=12, pady=(5, 0))

        # ---------- 底部：状态栏 / 工具行（先占位，保证小窗口下不被裁掉） ----------
        bar = tk.Frame(self, bg=C["bg"])
        bar.pack(fill="x", side="bottom", padx=12, pady=(3, 6))
        self.dot = tk.Label(bar, text="●", bg=C["bg"], fg=COLOR_OK, font=FONTS["small"])
        self.dot.pack(side="left")
        self.status = tk.StringVar(value="就绪。导入清单后，点「开始执行」即可。")
        tk.Label(bar, textvariable=self.status, bg=C["bg"], fg=C["ink_2"],
                 font=FONTS["small"], anchor="w").pack(side="left", padx=(6, 0))
        tk.Label(bar, text="急停：鼠标甩到屏幕左上角 / F12", bg=C["bg"], fg=C["muted"],
                 font=FONTS["small"]).pack(side="right")

        bottom = tk.Frame(self, bg=C["bg"])
        bottom.pack(fill="x", side="bottom", padx=12, pady=(5, 0))
        calib_holder = tk.Frame(bottom, bg=C["bg"])
        calib_holder.pack(side="left")
        self.btn_calib = ttk.Button(calib_holder, text="坐标校准…", style="Ghost.TButton",
                                    command=self.on_calibrate)
        self.btn_calib.pack(side="left")
        self.lbl_calib_off = tk.Label(calib_holder, text="坐标校准已停用（当前为精准模式）",
                                      bg=C["bg"], fg=C["muted"], font=FONTS["small"])
        ttk.Button(bottom, text="打开配置文件", style="Ghost.TButton",
                   command=self.on_open_config).pack(side="left", padx=(10, 0))
        ttk.Button(bottom, text="打开输出文件夹", style="Ghost.TButton",
                   command=lambda: self._open_path(self.result_dir)).pack(side="left", padx=(4, 0))
        ttk.Button(bottom, text="检查更新", style="Ghost.TButton",
                   command=self.on_check_update).pack(side="left", padx=(4, 0))
        ttk.Button(bottom, text="导出执行结果", style="Secondary.TButton",
                   command=self.on_export).pack(side="right")

        # ---------- 卡片 1：导入清单 ----------
        card1, body1 = self._new_card(self, 1, "导入清单", "支持 GBK / UTF-8 的 CSV · 需含条码与文件夹位置")
        card1.pack(fill="x", padx=12, pady=(6, 0))
        ttk.Button(body1, text="导入清单文件…", style="Accent.TButton",
                   command=self.on_import).pack(side="left")
        self.lbl_import = tk.Label(body1, text="尚未导入清单", bg=C["surface"], fg=C["muted"],
                                   font=FONTS["ui"])
        self.lbl_import.pack(side="left", padx=12)

        # ---------- 卡片 2：识别结果 / 执行状态 ----------
        card2, body2 = self._new_card(self, 2, "识别结果 / 执行状态",
                                      "双击某行可打开对应照片文件夹", expand=True)
        card2.pack(fill="both", expand=True, padx=12, pady=(6, 0))
        treewrap = tk.Frame(body2, bg=C["surface"])
        treewrap.pack(fill="both", expand=True)
        cols = ("idx", "barcode", "folder", "files", "status", "note")
        self.tree = ttk.Treeview(treewrap, columns=cols, show="headings", height=4)
        headers = {"idx": ("#", 46), "barcode": ("条码", 140), "folder": ("文件夹位置", 300),
                   "files": ("文件数", 56), "status": ("状态", 76), "note": ("说明", 242)}
        for c, (t, w) in headers.items():
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w, anchor="center" if c in ("idx", "files", "status") else "w")
        vs = ttk.Scrollbar(treewrap, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vs.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vs.pack(side="right", fill="y")
        self.tree.tag_configure("ok", foreground=COLOR_OK)
        self.tree.tag_configure("fail", foreground=COLOR_FAIL)
        self.tree.tag_configure("skip", foreground=COLOR_SKIP)
        self.tree.tag_configure("running", foreground=COLOR_RUN)
        self.tree.tag_configure("stripe", background=C["stripe"])
        self.tree.bind("<Double-1>", self.on_row_dblclick)
        self.tree.bind("<Button-3>", self.on_row_dblclick)
        # 空态提示（导入清单后隐藏）
        self.empty_hint = tk.Label(body2,
                                   text="尚未导入清单\n先在「1 导入清单」卡片导入 CSV，这里会逐条显示状态",
                                   bg=C["surface"], fg=C["muted"], font=FONTS["ui"], justify="center")
        self.empty_hint.place(relx=0.5, rely=0.60, anchor="center")

        # ---------- 卡片 3：执行控制 ----------
        card3, body3 = self._new_card(self, 3, "执行控制", "建议先「测试第一条」验证后再整批执行")
        card3.pack(fill="x", padx=12, pady=(6, 0))
        crow = tk.Frame(body3, bg=C["surface"])
        crow.pack(fill="x")
        self.btn_run = ttk.Button(crow, text="▶ 开始执行", style="Primary.TButton", command=self.on_run)
        self.btn_run.pack(side="left")
        self.btn_test = ttk.Button(crow, text="测试第一条", style="Secondary.TButton",
                                   command=self.on_test_one)
        self.btn_test.pack(side="left", padx=(8, 0))
        self.btn_pause = ttk.Button(crow, text="暂停", style="Secondary.TButton",
                                    command=self.on_pause, state="disabled")
        self.btn_pause.pack(side="left", padx=(8, 0))
        self.btn_stop = ttk.Button(crow, text="停止", style="Danger.TButton",
                                   command=self.on_stop, state="disabled")
        self.btn_stop.pack(side="left", padx=(8, 0))
        self.progress = ttk.Progressbar(crow, mode="determinate", length=200,
                                        style="Brand.Horizontal.TProgressbar")
        self.progress.pack(side="right")
        tk.Label(crow, text="进度", bg=C["surface"], fg=C["muted"],
                 font=FONTS["small"]).pack(side="right", padx=(0, 8))

        orow = tk.Frame(body3, bg=C["surface"])
        orow.pack(fill="x", pady=(7, 0))
        self.var_stop_on_error = tk.BooleanVar(value=bool(self.cfg["options"].get("stop_on_error")))
        ttk.Checkbutton(orow, text="出错即暂停（方便人工处理）", variable=self.var_stop_on_error,
                        style="Card.TCheckbutton").pack(side="left")
        self.var_dry_run = tk.BooleanVar(value=bool(self.cfg["options"].get("dry_run", False)))
        ttk.Checkbutton(orow, text="演练模式（只移动鼠标不点击）", variable=self.var_dry_run,
                        style="Card.TCheckbutton").pack(side="left", padx=(18, 0))

        # ---------- 卡片 4：运行日志 ----------
        card4, body4 = self._new_card(self, 4, "运行日志", expand=True)
        card4.pack(fill="both", expand=True, padx=12, pady=(6, 0))
        self.logtxt = ScrolledText(body4, height=3, font=FONTS["log"], relief="flat",
                                   background=C["log_bg"], foreground="#2C332A",
                                   insertbackground="#2C332A", borderwidth=0,
                                   highlightthickness=1, highlightbackground=C["line_soft"],
                                   padx=10, pady=4, state="disabled")
        self.logtxt.pack(fill="both", expand=True)
        try:
            self.logtxt.vbar.configure(bg="#DCE3D4", activebackground="#C8D2BF",
                                       troughcolor="#F1F4EC", relief="flat", bd=0, width=10)
        except Exception:
            pass
        self.logtxt.tag_configure("err", foreground="#B3261E")
        self.logtxt.tag_configure("warn", foreground="#8A6A1A")

        self._apply_mode_ui()

    # ================= 日志 / 队列 =================
    def log(self, text):
        self.msg_queue.put(("log", text))

    def _drain_queue(self):
        try:
            while True:
                kind, payload = self.msg_queue.get_nowait()
                if kind == "log":
                    ts = time.strftime("%H:%M:%S")
                    self._append_log(f"[{ts}] {payload}")
                    self._monitor_log(f"[{ts}] {payload}")
                elif kind == "row":
                    task, success, msg = payload
                    self._update_row(task)
                    self._monitor_sync()
                elif kind == "row_start":
                    task = payload
                    self._update_row(task)
                    self._monitor_sync(task)
                elif kind == "progress":
                    done = sum(1 for t in self.tasks if t.status in ("成功", "失败", "跳过"))
                    self.progress.configure(value=done)
                    self._monitor_sync()
                elif kind == "done":
                    self._on_run_finished(payload)
                elif kind == "update_result":
                    self._on_update_result(payload)
                elif kind == "update_source":
                    i, n, host = payload
                    if i > 1:
                        self.update_accel = True
                        self.log(f"直连较慢，已自动切换国内加速线路：{host}（第 {i - 1}/{n - 1} 条）")
                        self.status.set(f"国内加速下载中（{host}）…")
                    else:
                        self.update_accel = False
                        self.log(f"开始从 GitHub 直连下载：{host}")
                        self.status.set("更新包下载中（GitHub 直连）…")
                elif kind == "update_progress":
                    latest = (self.update_info or {}).get("latest", "?")
                    tag = "（国内加速）" if self.update_accel else ""
                    if payload is None:
                        self.status.set(f"正在下载 v{latest}{tag} …")
                    else:
                        self.status.set(f"正在下载 v{latest}{tag} … {payload}%")
                elif kind == "update_file":
                    self._on_update_file(payload)
                elif kind == "update_error":
                    self._on_update_error(payload)
        except queue.Empty:
            pass
        self.after(120, self._drain_queue)

    def _append_log(self, line):
        if "[错误]" in line or "异常" in line or "失败" in line:
            tag = "err"
        elif "警告" in line:
            tag = "warn"
        else:
            tag = ""
        self.logtxt.configure(state="normal")
        if tag:
            self.logtxt.insert("end", line + "\n", tag)
        else:
            self.logtxt.insert("end", line + "\n")
        self.logtxt.see("end")
        self.logtxt.configure(state="disabled")
        if self.run_log_file:
            try:
                self.run_log_file.write(line + "\n")
                self.run_log_file.flush()
            except Exception:
                pass

    def _update_row(self, task):
        iid = str(task.index)
        if self.tree.exists(iid):
            tag = {"成功": "ok", "失败": "fail", "跳过": "skip", "进行中": "running"}.get(task.status, "")
            tags = ("stripe",) if task.index % 2 == 0 else ()
            if tag:
                tags = tags + (tag,)
            self.tree.item(iid, values=(task.index, task.barcode, task.folder,
                                        task.display_files, task.status, task.note),
                           tags=tags)

    # ================= 模式化 UI（精准/兼容） =================
    def _apply_mode_ui(self):
        """精准模式启用时隐藏坐标校准入口；兼容模式下恢复显示。"""
        cdp_on = bool(self.cfg.get("cdp", {}).get("enabled", True))
        try:
            if cdp_on:
                self.btn_calib.pack_forget()
                self.lbl_calib_off.pack(side="left", padx=(6, 0))
                self.lbl_steps.configure(
                    text="① 导入清单    →    ② 开始执行    →    ③ 查看 / 导出结果（当前：精准模式）")
                self.mode_chip.configure(text="精准模式", bg=C["accent_soft"], fg=C["accent_ink"])
            else:
                self.lbl_calib_off.pack_forget()
                self.btn_calib.pack(side="left")
                self.lbl_steps.configure(
                    text="① 导入清单    →    ② 坐标校准    →    ③ 开始执行    →    ④ 导出结果（当前：兼容模式）")
                self.mode_chip.configure(text="兼容模式", bg="#F4EFE0", fg=C["warn"])
        except Exception:
            pass

    # ================= 调试模式保障（每次运行前） =================
    def _cdp_port_alive(self):
        """检查洗衣管家调试端口是否可用。"""
        cdp_cfg = self.cfg.get("cdp", {}) or {}
        port = int(cdp_cfg.get("port", 9222))
        try:
            data = json.load(urllib.request.urlopen(
                f"http://127.0.0.1:{port}/json", timeout=2))
            return bool([t for t in data if t.get("type") == "page"])
        except Exception:
            return False

    def _app_image_name(self):
        """洗衣管家进程名（从配置的 exe 路径提取）。"""
        cdp_cfg = self.cfg.get("cdp", {}) or {}
        exe = cdp_cfg.get("app_exe") or r"D:\Blending_Release-6.1.17\xygjwinapp.exe"
        return os.path.basename(exe) or "xygjwinapp.exe"

    def _app_process_running(self):
        name = self._app_image_name()
        r = _run_hidden(["tasklist", "/FI", f"IMAGENAME eq {name}", "/FO", "CSV"])
        if r is None:
            return False
        out = (r.stdout or b"").decode("gbk", errors="ignore").lower()
        return name.lower() in out

    def _close_app_windows(self):
        """先温和地请求关闭（发 WM_CLOSE），并辅以 taskkill 优雅模式。"""
        try:
            import pygetwindow as gw
            for w in gw.getAllWindows():
                try:
                    t = w.title or ""
                    if "洗衣管家" in t and "照片批量上传助手" not in t and "坐标校准" not in t:
                        w.close()
                except Exception:
                    pass
        except Exception:
            pass
        try:
            _run_hidden(["taskkill", "/IM", self._app_image_name()], timeout=10)
        except Exception:
            pass

    def _force_kill_app(self):
        """强制结束洗衣管家进程（正常关闭失败时的兜底）。"""
        name = self._app_image_name()
        self.log(f"正常关闭未成功，正在强制结束 {name} 进程…")
        r = _run_hidden(["taskkill", "/F", "/IM", name], timeout=15)
        ok = bool(r is not None and r.returncode == 0)
        if not ok:
            self.log("强制结束命令未成功（进程可能已退出）。")
        return ok

    def _wait_process_gone(self, seconds):
        """等待进程退出（期间保持界面刷新，避免“卡死”观感）。"""
        end = time.time() + seconds
        step = 0
        while time.time() < end:
            if not self._app_process_running():
                return True
            time.sleep(0.7)
            step += 1
            if step % 3 == 0:
                self._pump_events()
        return False

    def _launch_app_debug(self, exe, app_dir):
        """以调试参数启动洗衣管家。"""
        subprocess.Popen([exe, "--remote-debugging-port=9222"], cwd=app_dir)

    def _pump_events(self):
        """立即把日志刷到界面（避免长流程时“看起来卡死”）。"""
        try:
            while True:
                kind, payload = self.msg_queue.get_nowait()
                if kind == "log":
                    ts = time.strftime("%H:%M:%S")
                    self._append_log(f"[{ts}] {payload}")
        except queue.Empty:
            pass
        except Exception:
            pass
        try:
            self.update_idletasks()
        except Exception:
            pass

    def _proc_il_value(self, pid):
        """取进程完整性级别数值（12288=管理员，8192=普通）。"""
        try:
            import win32api, win32security
            h = win32api.OpenProcess(0x1000, False, pid)
            tok = win32security.OpenProcessToken(h, win32security.TOKEN_QUERY)
            sid, _ = win32security.GetTokenInformation(tok, win32security.TokenIntegrityLevel)
            s = win32security.ConvertSidToStringSid(sid)
            return int(s.rsplit('-', 1)[1])
        except Exception:
            return None

    def _app_pid(self):
        r = _run_hidden(['tasklist', '/FI', f'IMAGENAME eq {self._app_image_name()}', '/FO', 'CSV', '/NH'])
        if r is None:
            return None
        txt = (r.stdout or b'').decode('gbk', errors='ignore')
        for line in txt.splitlines():
            parts = [x.strip('"') for x in line.split('","')]
            if len(parts) >= 2 and parts[1].isdigit():
                return int(parts[1])
        return None

    def _check_integrity_hint(self):
        """检查本工具与洗衣管家的权限级别；不匹配时给出明确提示（防止再被 UIPI 拦截）。"""
        try:
            il_self = self._proc_il_value(os.getpid())
            pid = self._app_pid()
            il_app = self._proc_il_value(pid) if pid else None
        except Exception:
            return
        names = {4096: '低', 8192: '普通', 12288: '管理员', 16384: '系统'}
        self.log(f"权限自检：本工具={names.get(il_self, il_self)}；洗衣管家={names.get(il_app, il_app)}")
        if il_self and il_app and il_app > il_self:
            messagebox.showwarning(
                "权限不匹配（需以管理员身份运行本工具）",
                "检测到「洗衣管家」以管理员权限运行，而本工具是普通权限。\n"
                "Windows 会阻止普通权限程序操作管理员程序（表现为：选照片弹窗填不了内容）。\n\n"
                "请关闭本工具，改用桌面快捷方式重新打开（v1.6 起已内置管理员启动），再执行。")

    def ensure_debug_ready_for_run(self):
        """每次运行前确保洗衣管家处于调试模式；返回 'debug' 或 'fallback'。
        流程：端口可用→直接精准；已开但无参数→确认后关闭（正常→强制）→带参启动；
        全程刷新界面日志，最多两次启动尝试。"""
        self._check_integrity_hint()
        cdp_cfg = self.cfg.get("cdp", {}) or {}
        if not cdp_cfg.get("enabled", True) or not cdp_cfg.get("auto_start", True):
            alive = self._cdp_port_alive()
            return "debug" if alive else "fallback"
        if self._cdp_port_alive():
            self.log("调试端口可用（洗衣管家已处于调试模式）。")
            self._pump_events()
            return "debug"

        exe = cdp_cfg.get("app_exe") or r"D:\Blending_Release-6.1.17\xygjwinapp.exe"
        app_dir = cdp_cfg.get("app_dir") or os.path.dirname(exe)
        wait_s = float(cdp_cfg.get("start_wait", 30))
        force_restart = bool(cdp_cfg.get("force_restart", True))

        # ---- 第一段：旧进程在运行时，按需重启 ----
        if self._app_process_running():
            ok = messagebox.askyesno(
                "洗衣管家未在调试模式运行",
                "检测到「洗衣管家」已打开，但没有带调试参数。\n\n"
                "为了使用精准模式，需要关闭并重新打开它。\n"
                "请确认软件当前没有正在进行中的操作。\n"
                "若正常关闭失败，程序会自动强制结束它的进程。\n\n"
                "现在自动重启为调试模式吗？\n"
                "（选“否”将直接用兼容模式运行）")
            if not ok:
                self.log("已选择不重启 —— 将以兼容模式运行。")
                self._pump_events()
                return "fallback"
            self.log("正在正常关闭洗衣管家…")
            self._pump_events()
            self._close_app_windows()
            if not self._wait_process_gone(6):
                if force_restart:
                    self._force_kill_app()
                    self._pump_events()
                    if not self._wait_process_gone(12):
                        self.log("无法结束洗衣管家进程 —— 本次以兼容模式运行。")
                        self._pump_events()
                        return "fallback"
                else:
                    self.log("未能正常关闭（已禁用强制重启）—— 以兼容模式运行。")
                    self._pump_events()
                    return "fallback"
            else:
                self.log("洗衣管家已正常退出。")
                self._pump_events()

        # ---- 第二段：带参启动（最多两次尝试）----
        for attempt in (1, 2):
            try:
                self.log(f"正在启动洗衣管家（调试模式，第 {attempt} 次）…")
                self._pump_events()
                self._launch_app_debug(exe, app_dir)
            except Exception as e:
                self.log(f"启动失败（{e}）。")
                self._pump_events()
                continue

            end = time.time() + wait_s
            ok_port = False
            step = 0
            while time.time() < end:
                if self._cdp_port_alive():
                    ok_port = True
                    break
                time.sleep(0.8)
                step += 1
                if step % 4 == 0:
                    self._pump_events()

            if ok_port:
                self.log("洗衣管家已启动，调试端口就绪。")
                self._pump_events()
                try:
                    from cdp_control import CDPApp
                    cdp_app = CDPApp(int(cdp_cfg.get("port", 9222)), routes=cdp_cfg.get("routes"))
                    ok2, _info = cdp_app.connect()
                    if ok2:
                        r_end = time.time() + 15
                        while time.time() < r_end:
                            try:
                                if cdp_app.ev("document.readyState") == "complete" and cdp_app.get_hash():
                                    self.log("软件页面已就绪。")
                                    break
                            except Exception:
                                pass
                            time.sleep(0.5)
                        cdp_app.close()
                except Exception:
                    pass
                self._pump_events()
                return "debug"

            # 未成功：清理后重试
            if self._app_process_running():
                self.log("调试端口未在预期时间内出现，清理后重试…")
                self._pump_events()
                self._force_kill_app()
                self._wait_process_gone(8)
            else:
                self.log("进程未成功启动，准备重试…")
                self._pump_events()

        self.log("重启调试模式未成功 —— 本次以兼容模式运行（可稍后手动用带参数的快捷方式启动）。")
        self._pump_events()
        return "fallback"

    # ================= 导入 =================
    def on_import(self):
        path = filedialog.askopenfilename(
            title="选择导出清单（CSV）",
            filetypes=[("CSV/文本清单", "*.csv *.txt"), ("所有文件", "*.*")])
        if not path:
            return
        self._import_path(path)

    def _import_path(self, path):
        try:
            tasks, report = import_list(
                path, check_folder=self.cfg["options"].get("check_folder_exists", True))
        except Exception as e:
            messagebox.showerror("导入失败", f"无法解析该清单：\n{e}")
            self.log(f"导入失败：{e}")
            return
        self.tasks = tasks
        self.report = report
        self._fill_tree()
        self.lbl_import.configure(
            text=f"已导入：{os.path.basename(path)}", fg=C["accent_ink"])
        self.status.set(report.summary())
        self.log(f"已导入清单：{path}")
        self.log(f"  识别编码：{report.encoding}；数据行：{report.total_rows}；条码有效：{report.valid_rows}")
        for issue in report.issues[:20]:
            self.log(f"  · {issue}")
        if len(report.issues) > 20:
            self.log(f"  · ……其余 {len(report.issues) - 20} 条提示省略")
        # 自动保存最近清单路径到配置（方便下次）
        self.cfg.setdefault("recent", {})["last_csv"] = path
        save_config(BASE_DIR, self.cfg)

    def _fill_tree(self):
        for i in self.tree.get_children():
            self.tree.delete(i)
        for t in self.tasks:
            tag = {"成功": "ok", "失败": "fail", "跳过": "skip"}.get(t.status, "")
            tags = ("stripe",) if t.index % 2 == 0 else ()
            if tag:
                tags = tags + (tag,)
            self.tree.insert("", "end", iid=str(t.index),
                             values=(t.index, t.barcode, t.folder, t.display_files,
                                     t.status, t.note), tags=tags)
        if getattr(self, "empty_hint", None) is not None:
            if self.tasks:
                self.empty_hint.place_forget()
            else:
                self.empty_hint.place(relx=0.5, rely=0.60, anchor="center")

    # ================= 校准 =================
    def on_calibrate(self):
        from calibrate import open_calibration

        def _saved():
            self.cfg = load_config(BASE_DIR)
            self._apply_mode_ui()
            self.log("坐标校准已更新。")

        try:
            open_calibration(self, BASE_DIR, self.cfg, on_saved=_saved)
        except Exception as e:
            messagebox.showerror("校准工具错误", str(e))

    def on_open_config(self):
        self._open_path(os.path.join(BASE_DIR, "config.json"))

    def _open_path(self, p):
        try:
            os.startfile(p)
        except Exception as e:
            messagebox.showinfo("提示", f"无法打开：{p}\n{e}")

    def on_row_dblclick(self, event):
        iid = self.tree.identify_row(event.y)
        if not iid:
            return
        t = next((x for x in self.tasks if str(x.index) == iid), None)
        if t and t.folder and os.path.isdir(t.folder):
            self._open_path(t.folder)

    # ================= 执行 =================
    def _check_ready(self):
        if not self.tasks:
            messagebox.showinfo("提示", "请先导入清单文件。")
            return False
        pending = [t for t in self.tasks if t.status == "待执行"]
        if not pending:
            messagebox.showinfo("提示", "没有待执行的任务（可能都已执行或跳过）。")
            return False
        # 每次运行前：确保洗衣管家处于调试模式（没开则自动带参数启动）
        mode = self.ensure_debug_ready_for_run()
        if mode == "debug":
            return True      # 精准模式不需要坐标校准
        pts = self.cfg["points"]
        missing = [k for k in ("barcode_input", "photo_button", "upload_button")
                   if not pts.get(k)]
        if missing:
            if not messagebox.askyesno(
                    "尚未完成坐标校准",
                    "以下操作点还没有采集坐标：\n  "
                    + "\n  ".join(missing)
                    + "\n\n现在运行可能无法正确点击。建议先做【坐标校准】。\n是否仍要开始执行？"):
                return False
        return True

    def on_run(self):
        if not self._check_ready():
            return
        self._start_worker(only_first=False)

    def on_test_one(self):
        if not self._check_ready():
            return
        if not messagebox.askyesno("测试第一条", "将只执行清单中的第一条记录，用于验证校准是否正确。\n继续吗？"):
            return
        self._start_worker(only_first=True)

    def _start_worker(self, only_first):
        # 快照待执行任务（重跑时保留已成功记录的历史）
        for t in self.tasks:
            if t.status in ("失败", "进行中"):
                t.status = "待执行"
                t.note = ""
        self._fill_tree()

        self.cfg["options"]["stop_on_error"] = bool(self.var_stop_on_error.get())
        self.cfg["options"]["dry_run"] = bool(self.var_dry_run.get())
        save_config(BASE_DIR, self.cfg)

        from automation import AutomationEngine, RunControl
        import automation as aut

        self.control = RunControl()
        self.engine = AutomationEngine(self.cfg, log=self.log, control=self.control)
        self.engine.base_dir = BASE_DIR
        self.engine.output_dir = self.result_dir

        # 运行日志文件
        try:
            log_path = os.path.join(self.result_dir, f"运行日志_{time.strftime('%Y%m%d_%H%M%S')}.txt")
            self.run_log_file = open(log_path, "w", encoding="utf-8")
        except Exception:
            self.run_log_file = None

        self.run_started_at = time.time()
        total = len([t for t in self.tasks if t.status == "待执行"])
        if only_first:
            total = min(total, 1)
        done_before = sum(1 for t in self.tasks if t.status in ("成功", "失败", "跳过"))
        self.progress.configure(maximum=max(len(self.tasks), 1), value=done_before)

        self._set_running_ui(True)
        self._monitor_open(total, only_first, bool(self.var_dry_run.get()))

        def worker():
            try:
                stats = self.engine.run_batch(
                    self.tasks,
                    on_row_start=lambda t: (setattr(t, "status", "进行中"),
                                            self.msg_queue.put(("row_start", t))),
                    on_row_done=lambda t, ok, m: (self.msg_queue.put(("row", (t, ok, m))),
                                                  self.msg_queue.put(("progress", None))),
                    only_first=only_first)
            except Exception as e:
                self.log(f"[错误] 执行线程异常：{e}")
                self.log(traceback.format_exc())
                stats = {"total": 0, "ok": 0, "fail": 0, "skip": 0}
            self.msg_queue.put(("done", stats))

        self.worker = threading.Thread(target=worker, daemon=True)
        self.worker.start()

    def _set_running_ui(self, running):
        state = "disabled" if running else "normal"
        self.btn_run.configure(state=state)
        self.btn_test.configure(state=state)
        self.btn_pause.configure(state="normal" if running else "disabled", text="暂停")
        self.btn_stop.configure(state="normal" if running else "disabled")
        try:
            self.dot.configure(fg=COLOR_RUN if running else COLOR_OK)
        except Exception:
            pass

    def on_pause(self):
        if not self.control:
            return
        if self.control.pause_event.is_set():
            self.control.resume()
            self.btn_pause.configure(text="暂停")
            self.status.set("已继续。")
            self.log("已继续执行。")
            self._monitor_paused(False)
        else:
            self.control.pause()
            self.btn_pause.configure(text="继续")
            self.status.set("已暂停。处理完当前步骤后停在原地，点击【继续】恢复。")
            self.log("已暂停（当前步骤完成后生效）。")
            self._monitor_paused(True)

    def on_stop(self):
        if self.control:
            self.control.resume()
            self.control.stop()
            self.status.set("正在停止…")
            self.log("收到停止指令，正在安全停止…")

    # ================= 执行监视悬浮窗 =================
    def _monitor_open(self, total, only_first, dry):
        """执行开始：弹出（或重建）置顶监视窗。"""
        try:
            if self.monitor is not None:
                try:
                    self.monitor.close()
                except Exception:
                    pass
            self.monitor = MonitorWindow(self, on_pause=self.on_pause, on_stop=self.on_stop)
            self.monitor.set_total(min(total, 1) if only_first else total, dry)
            self.monitor.show()
        except Exception as e:
            self.monitor = None
            self.log(f"[警告] 监视窗口创建失败：{e}")

    def _monitor_sync(self, current_task=None):
        """把最新计数 / 当前条码同步到监视窗。"""
        mon = self.monitor
        if mon is None or mon.closed:
            return
        ok = sum(1 for t in self.tasks if t.status == "成功")
        fail = sum(1 for t in self.tasks if t.status == "失败")
        skip = sum(1 for t in self.tasks if t.status == "跳过")
        mon.update_counts(ok + fail + skip, ok, fail, skip)
        if current_task is not None:
            mon.set_current(f"正在处理：{current_task.barcode}（第 {current_task.index} 条）")

    def _monitor_log(self, line):
        mon = self.monitor
        if mon is None or mon.closed:
            return
        try:
            mon.add_log(line)
        except Exception:
            pass

    def _monitor_paused(self, paused):
        mon = self.monitor
        if mon is None or mon.closed:
            return
        try:
            mon.set_paused(paused)
        except Exception:
            pass

    def _monitor_finish(self, stats, dur):
        mon = self.monitor
        if mon is None or mon.closed:
            return
        try:
            mon.finish(f"执行结束：成功 {stats.get('ok', 0)} / 失败 {stats.get('fail', 0)}"
                       f" / 跳过 {stats.get('skip', 0)}，用时 {dur:.0f} 秒")
        except Exception:
            pass

    def _on_run_finished(self, stats):
        self._set_running_ui(False)
        dur = time.time() - self.run_started_at if self.run_started_at else 0
        prefix = "执行结束（演练模式·未真实操作）：" if self.cfg["options"].get("dry_run") else "执行结束："
        self.status.set(prefix + f"成功 {stats.get('ok', 0)} / 失败 {stats.get('fail', 0)} / "
                        f"跳过 {stats.get('skip', 0)}，用时 {dur:.0f} 秒")
        self._monitor_finish(stats, dur)
        # 自动导出结果
        try:
            paths = self._export_results(auto=True)
            if paths:
                self.log("已自动导出执行结果：" + "；".join(paths))
        except Exception as e:
            self.log(f"[警告] 自动导出失败：{e}")
        if self.run_log_file:
            try:
                self.run_log_file.close()
            except Exception:
                pass
            self.run_log_file = None

    # ================= 导出结果 =================
    def on_export(self):
        paths = self._export_results(auto=False)
        if paths:
            messagebox.showinfo("导出完成", "已导出：\n" + "\n".join(paths))

    def _export_results(self, auto=False):
        done = [t for t in self.tasks if t.status in ("成功", "失败", "跳过")]
        if not done:
            if not auto:
                messagebox.showinfo("提示", "还没有可导出的执行结果。")
            return []
        ts = time.strftime("%Y%m%d_%H%M%S")
        base = os.path.join(self.result_dir, f"执行结果_{ts}")
        headers = ["序号", "条码", "文件夹位置", "文件数", "结果", "耗时(秒)", "备注"]
        rows = []
        for t in done:
            rows.append([t.index, t.barcode, t.folder, t.display_files,
                         t.status, f"{t.duration:.1f}" if t.duration else "-", t.note])

        ok = sum(1 for t in done if t.status == "成功")
        fail = sum(1 for t in done if t.status == "失败")
        skip = sum(1 for t in done if t.status == "跳过")

        paths = []
        # --- CSV（utf-8-sig，Excel 直接打开无乱码） ---
        csv_path = base + ".csv"
        with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
            import csv as _csv
            w = _csv.writer(f)
            w.writerow([f"# {APP_TITLE} v{VERSION} 执行结果"])
            w.writerow([f"# 导出时间：{time.strftime('%Y-%m-%d %H:%M:%S')}"])
            w.writerow([f"# 合计：{len(done)} 条，成功 {ok}，失败 {fail}，跳过 {skip}"])
            w.writerow(headers)
            for r in rows:
                w.writerow(r)
        paths.append(csv_path)

        # --- XLSX（可选） ---
        if self.cfg["options"].get("export_xlsx", True):
            try:
                from openpyxl import Workbook
                from openpyxl.styles import Font, PatternFill
                wb = Workbook()
                ws = wb.active
                ws.title = "执行结果"
                ws.append(headers)
                for c in ws[1]:
                    c.font = Font(bold=True)
                    c.fill = PatternFill("solid", fgColor="DDEBF7")
                for r in rows:
                    ws.append(r)
                for col, width in zip("ABCDEFG", (6, 18, 42, 8, 8, 10, 40)):
                    ws.column_dimensions[col].width = width
                # 汇总页
                ws2 = wb.create_sheet("汇总")
                ws2.append(["项目", "数量"])
                ws2.append(["成功", ok])
                ws2.append(["失败", fail])
                ws2.append(["跳过", skip])
                ws2.append(["合计", len(done)])
                xlsx_path = base + ".xlsx"
                wb.save(xlsx_path)
                paths.append(xlsx_path)
            except ImportError:
                pass
            except Exception as e:
                self.log(f"[警告] 导出 Excel 失败：{e}")
        return paths

    # ================= 在线更新 =================
    def _auto_check_update(self):
        """启动后静默检查更新（可在 config.json 关闭：options.check_update_on_start）。"""
        if self.update_thread and self.update_thread.is_alive():
            return
        self.log("正在检查更新…")
        self._start_update_check(mode="auto")

    def on_check_update(self):
        """手动检查更新（底部【检查更新】按钮）。"""
        if self.update_thread and self.update_thread.is_alive():
            self.status.set("正在检查更新…")
            return
        self.status.set("正在检查更新…")
        self.log("正在检查更新（手动）…")
        self._start_update_check(mode="manual")

    def _start_update_check(self, mode):
        def worker():
            try:
                info = updater.check_latest(VERSION)
                self.msg_queue.put(("update_result", {"mode": mode, "error": None, "info": info}))
            except Exception as e:
                self.msg_queue.put(("update_result", {"mode": mode, "error": str(e), "info": None}))
        self.update_thread = threading.Thread(target=worker, daemon=True)
        self.update_thread.start()

    def _on_update_result(self, res):
        mode = (res or {}).get("mode")
        err = (res or {}).get("error")
        if err:
            self.log(f"[警告] 检查更新失败：{err}")
            if mode == "manual":
                messagebox.showwarning("检查更新",
                                       f"检查更新失败：\n{err}\n\n可稍后重试，或到 Releases 页面手动查看。")
            else:
                self.status.set("检查更新失败（不影响正常使用）。")
            return
        info = (res or {}).get("info") or {}
        if info.get("found"):
            self.update_info = info
            self.log(f"发现新版本：v{info.get('latest')}（当前 v{VERSION}）。")
            self._show_update_bar(info)
            if mode == "manual":
                if messagebox.askyesno("发现新版本",
                                       f"发现新版本 v{info.get('latest')}（当前 v{VERSION}）。\n\n现在下载并安装吗？"):
                    self._start_update_download()
        else:
            self.status.set(f"当前已是最新版本（v{VERSION}）。")
            self.log(f"检查更新：已是最新版本（v{VERSION}）。")
            if mode == "manual":
                messagebox.showinfo("检查更新", f"当前已是最新版本（v{VERSION}）。")

    def _show_update_bar(self, info):
        if self.update_bar is None:
            bar = tk.Frame(self, bg=C["accent_soft"])
            self.update_label = tk.Label(bar, text="", bg=C["accent_soft"], fg=C["accent_ink"],
                                         font=FONTS["ui"])
            self.update_label.pack(side="left", padx=(14, 12), pady=5)
            ttk.Button(bar, text="下载并安装", style="Primary.TButton",
                       command=self._start_update_download).pack(side="left", pady=4)
            ttk.Button(bar, text="更新说明", style="SoftGhost.TButton",
                       command=self._open_release_page).pack(side="left", padx=(8, 0), pady=4)
            ttk.Button(bar, text="稍后", style="SoftGhost.TButton",
                       command=self._hide_update_bar).pack(side="right", padx=(0, 10), pady=4)
            self.update_bar = bar
        self.update_label.configure(
            text=f"发现新版本 v{info.get('latest')}（当前 v{VERSION}），可一键下载并安装。")
        if not self.update_bar.winfo_ismapped():
            self.update_bar.pack(fill="x", padx=12, pady=(4, 0), after=self.lbl_steps)
        self.status.set(f"发现新版本 v{info.get('latest')}。")

    def _hide_update_bar(self):
        if self.update_bar is not None:
            self.update_bar.pack_forget()

    def _open_release_page(self):
        url = (self.update_info or {}).get("html_url") or updater.RELEASES_PAGE.format(repo=updater.GITHUB_REPO)
        try:
            webbrowser.open(url)
        except Exception as e:
            self.log(f"打开浏览器失败：{e}")

    def _start_update_download(self):
        if self.worker is not None and self.worker.is_alive():
            messagebox.showinfo("提示", "当前正在执行任务，请等执行结束后再更新。")
            return
        info = self.update_info or {}
        if not info.get("asset_url"):
            if messagebox.askyesno("更新", "该版本没有可直接下载的安装包附件。\n是否打开 Releases 页面手动下载？"):
                self._open_release_page()
            return
        if self.downloading:
            self.status.set("更新包正在下载中…")
            return
        self.downloading = True
        self.update_accel = False
        self.status.set(f"正在下载 v{info.get('latest')} …")
        self.log(f"开始下载更新包：{info.get('asset_name')}")
        mirrors = (self.cfg.get("update") or {}).get("mirrors", updater.DEFAULT_MIRRORS)

        def worker():
            try:
                def cb(done, total):
                    if total:
                        self.msg_queue.put(("update_progress", int(done * 100 / total)))
                    else:
                        self.msg_queue.put(("update_progress", None))

                def scb(idx, total, url):
                    host = url.split("/")[2] if "//" in url else url
                    self.msg_queue.put(("update_source", (idx, total, host)))

                path = updater.download(info["asset_url"],
                                        filename=info.get("asset_name") or None,
                                        progress_cb=cb,
                                        mirrors=mirrors,
                                        expected_size=info.get("asset_size"),
                                        sha256_url=info.get("sha256_url"),
                                        expected_sha256=info.get("sha256"),
                                        source_cb=scb)
                self.msg_queue.put(("update_file", path))
            except Exception as e:
                self.msg_queue.put(("update_error", str(e)))
        threading.Thread(target=worker, daemon=True).start()

    def _on_update_file(self, path):
        self.downloading = False
        self.log(f"更新包已下载：{path}")
        self.status.set("更新包下载完成。")
        if messagebox.askyesno("更新下载完成",
                               f"新版本安装包已下载完成：\n{path}\n\n安装向导需要先退出本程序。\n"
                               f"现在退出并启动安装向导吗？\n（选『否』可稍后手动双击该文件安装）"):
            try:
                subprocess.Popen([path])
                self.after(400, self.destroy)
            except Exception as e:
                messagebox.showerror("更新", f"启动安装向导失败：{e}\n\n请手动双击安装包：\n{path}")

    def _on_update_error(self, msg):
        self.downloading = False
        self.log(f"[警告] 更新包下载失败：{msg}")
        self.status.set("更新包下载失败。")
        if messagebox.askyesno("下载失败",
                               f"更新包下载失败：\n{msg}\n\n是否打开 Releases 页面手动下载？"):
            self._open_release_page()

    # ================= 全局热键（急停） =================
    def _setup_hotkey(self):
        try:
            import keyboard as kb
            kb.add_hotkey("f12", lambda: self.msg_queue.put(("log", "F12 急停触发！"))
                          or (self.control and (self.control.resume(), self.control.stop())))
            self.log("已启用 F12 全局急停热键（另外：鼠标甩到屏幕左上角也可急停）。")
        except Exception:
            self.log("未启用 F12 全局热键（可选安装 keyboard 库），请使用鼠标左上角急停。")


def main():
    # 支持命令行自检：python main.py --selftest <csv路径>
    if len(sys.argv) >= 2 and sys.argv[1] == "--selftest":
        csv_path = sys.argv[2] if len(sys.argv) > 2 else None
        print(f"[自检] 工作目录：{BASE_DIR}")
        cfg = load_config(BASE_DIR)
        print(f"[自检] 配置加载 OK：窗口关键字={cfg['window'].get('title_keyword')}")
        import csv_import as ci
        print("[自检] 模块导入 OK：csv_import")
        import automation as aut
        print("[自检] 模块导入 OK：automation")
        if csv_path and os.path.exists(csv_path):
            tasks, report = ci.import_list(csv_path)
            print("[自检] 清单解析：", report.summary())
            for t in tasks[:5]:
                print("   ", t.index, t.barcode, t.folder, t.status, t.note)
        print("[自检] 完成 ✔")
        return

    # 无控制台运行（pythonw 方式双击启动）时，把 stdout/stderr 送入日志文件，避免崩溃
    if sys.stdout is None or sys.stderr is None:
        try:
            _devlog = open(os.path.join(BASE_DIR, "运行日志_启动控制台.txt"), "a", encoding="utf-8")
            if sys.stdout is None:
                sys.stdout = _devlog
            if sys.stderr is None:
                sys.stderr = _devlog
        except Exception:
            pass

    try:
        app = App()
        app.mainloop()
    except Exception:
        import traceback
        err = traceback.format_exc()
        # 1) 写入错误日志，供排查
        try:
            with open(os.path.join(BASE_DIR, "启动错误日志.txt"), "a", encoding="utf-8") as f:
                f.write(time.strftime("[%Y-%m-%d %H:%M:%S] \n") + err + "\n")
        except Exception:
            pass
        # 2) 弹窗提示（若 tkinter 可用）
        try:
            from tkinter import messagebox
            _root = tk.Tk()
            _root.withdraw()
            messagebox.showerror("程序启动失败", err[-1800:])
            _root.destroy()
        except Exception:
            pass
        raise


if __name__ == "__main__":
    main()
