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

APP_TITLE = "洗衣管家 · 照片批量上传助手"
VERSION = "1.4"

_NO_WINDOW = 0x08000000  # subprocess.CREATE_NO_WINDOW


def _run_hidden(cmd, timeout=15):
    """静默子进程调用（CREATE_NO_WINDOW，避免控制台窗口闪动/干扰）。"""
    try:
        return subprocess.run(cmd, capture_output=True, timeout=timeout,
                              creationflags=_NO_WINDOW)
    except Exception:
        return None

COLOR_OK = "#1a7f37"
COLOR_FAIL = "#c62828"
COLOR_SKIP = "#8a8a8a"
COLOR_RUN = "#1565c0"


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"{APP_TITLE}  v{VERSION}")
        self.geometry("980x720")
        self.minsize(880, 640)

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
        self.after(120, self._drain_queue)

    # ================= UI =================
    def _setup_style(self):
        style = ttk.Style(self)
        try:
            style.theme_use("vista")
        except Exception:
            pass
        default_font = ("Microsoft YaHei UI", 10)
        self.option_add("*Font", default_font)
        style.configure(".", font=default_font)
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 15, "bold"))
        style.configure("Sub.TLabel", foreground="#666")
        style.configure("Section.TLabelframe.Label", font=("Microsoft YaHei UI", 10, "bold"))
        style.configure("Run.TButton", font=("Microsoft YaHei UI", 11, "bold"))

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
                self._img_header = tk.PhotoImage(file=head_p)
        except Exception:
            self._img_header = None

    def _build_ui(self):
        pad = {"padx": 10, "pady": 6}

        # ---- 顶部标题 ----
        header = ttk.Frame(self)
        header.pack(fill="x", **pad)
        if self._img_header is not None:
            ttk.Label(header, image=self._img_header).pack(side="left", padx=(0, 8))
        ttk.Label(header, text=APP_TITLE, style="Title.TLabel").pack(side="left")
        ttk.Label(header, text="  按操作流程图自动执行：输条码 → 上传图片 → 全选文件夹文件 → 循环",
                  style="Sub.TLabel").pack(side="left")

        # ---- 步骤条 ----
        steps = ttk.Frame(self)
        steps.pack(fill="x", padx=10)
        self.lbl_steps = ttk.Label(steps, foreground="#1565c0")
        self.lbl_steps.pack(anchor="w")

        # ---- 导入区 ----
        box1 = ttk.LabelFrame(self, text=" 1. 导入清单（导出清单 CSV） ", style="Section.TLabelframe")
        box1.pack(fill="x", **pad)
        row = ttk.Frame(box1)
        row.pack(fill="x", padx=8, pady=6)
        ttk.Button(row, text="导入清单文件…", command=self.on_import).pack(side="left")
        self.lbl_import = ttk.Label(row, text="尚未导入清单", foreground="#666")
        self.lbl_import.pack(side="left", padx=12)

        # ---- 任务列表 ----
        box2 = ttk.LabelFrame(self, text=" 2. 识别结果 / 执行状态 ", style="Section.TLabelframe")
        box2.pack(fill="both", expand=True, **pad)
        wrap = ttk.Frame(box2)
        wrap.pack(fill="both", expand=True, padx=8, pady=6)

        cols = ("idx", "barcode", "folder", "files", "status", "note")
        self.tree = ttk.Treeview(wrap, columns=cols, show="headings", height=9)
        headers = {"idx": ("#", 50), "barcode": ("条码", 150), "folder": ("文件夹位置", 320),
                   "files": ("文件数", 60), "status": ("状态", 80), "note": ("说明", 260)}
        for c, (t, w) in headers.items():
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w, anchor="center" if c in ("idx", "files", "status") else "w")
        vs = ttk.Scrollbar(wrap, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vs.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vs.pack(side="right", fill="y")
        self.tree.tag_configure("ok", foreground=COLOR_OK)
        self.tree.tag_configure("fail", foreground=COLOR_FAIL)
        self.tree.tag_configure("skip", foreground=COLOR_SKIP)
        self.tree.tag_configure("running", foreground=COLOR_RUN)
        self.tree.bind("<Double-1>", self.on_row_dblclick)
        self.tree.bind("<Button-3>", self.on_row_dblclick)

        # ---- 执行控制 ----
        box3 = ttk.LabelFrame(self, text=" 3. 执行控制 ", style="Section.TLabelframe")
        box3.pack(fill="x", **pad)
        ctl = ttk.Frame(box3)
        ctl.pack(fill="x", padx=8, pady=6)

        self.btn_run = ttk.Button(ctl, text="▶ 开始执行", style="Run.TButton", command=self.on_run)
        self.btn_run.pack(side="left")
        self.btn_test = ttk.Button(ctl, text="测试第一条", command=self.on_test_one)
        self.btn_test.pack(side="left", padx=6)
        self.btn_pause = ttk.Button(ctl, text="暂停", command=self.on_pause, state="disabled")
        self.btn_pause.pack(side="left", padx=6)
        self.btn_stop = ttk.Button(ctl, text="停止", command=self.on_stop, state="disabled")
        self.btn_stop.pack(side="left", padx=6)

        self.var_stop_on_error = tk.BooleanVar(value=bool(self.cfg["options"].get("stop_on_error")))
        ttk.Checkbutton(ctl, text="出错即暂停（方便人工处理）",
                        variable=self.var_stop_on_error).pack(side="left", padx=16)

        self.var_dry_run = tk.BooleanVar(value=bool(self.cfg["options"].get("dry_run", False)))
        ttk.Checkbutton(ctl, text="演练模式（只移动鼠标不点击）",
                        variable=self.var_dry_run).pack(side="left", padx=4)

        self.progress = ttk.Progressbar(ctl, mode="determinate", length=220)
        self.progress.pack(side="right")

        # ---- 日志 ----
        box4 = ttk.LabelFrame(self, text=" 4. 运行日志 ", style="Section.TLabelframe")
        box4.pack(fill="both", expand=True, **pad)
        self.logtxt = ScrolledText(box4, height=9, font=("Consolas", 9))
        self.logtxt.pack(fill="both", expand=True, padx=8, pady=6)
        self.logtxt.configure(state="disabled")

        # ---- 底部按钮 ----
        bottom = ttk.Frame(self)
        bottom.pack(fill="x", padx=10, pady=(0, 4))
        self.btn_calib = ttk.Button(bottom, text="坐标校准…", command=self.on_calibrate)
        self.btn_calib.pack(side="left")
        self.lbl_calib_off = ttk.Label(bottom, text="坐标校准已停用（当前为精准模式）", foreground="#888")
        ttk.Button(bottom, text="打开配置文件", command=self.on_open_config).pack(side="left", padx=6)
        ttk.Button(bottom, text="打开输出文件夹", command=lambda: self._open_path(self.result_dir)).pack(side="left", padx=6)
        ttk.Button(bottom, text="导出执行结果", command=self.on_export).pack(side="right")
        self._apply_mode_ui()

        # ---- 状态栏 ----
        self.status = tk.StringVar(value="就绪。提示：急停 = 鼠标猛甩到屏幕左上角（或按 F12）")
        bar = ttk.Label(self, textvariable=self.status, anchor="w", foreground="#555")
        bar.pack(fill="x", padx=10, pady=(0, 6))

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
                elif kind == "row":
                    task, success, msg = payload
                    self._update_row(task)
                elif kind == "row_start":
                    task = payload
                    self._update_row(task)
                elif kind == "progress":
                    done = sum(1 for t in self.tasks if t.status in ("成功", "失败", "跳过"))
                    self.progress.configure(value=done)
                elif kind == "done":
                    self._on_run_finished(payload)
        except queue.Empty:
            pass
        self.after(120, self._drain_queue)

    def _append_log(self, line):
        self.logtxt.configure(state="normal")
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
            self.tree.item(iid, values=(task.index, task.barcode, task.folder,
                                        task.display_files, task.status, task.note),
                           tags=(tag,) if tag else ())

    # ================= 模式化 UI（精准/兼容） =================
    def _apply_mode_ui(self):
        """精准模式启用时隐藏坐标校准入口；兼容模式下恢复显示。"""
        cdp_on = bool(self.cfg.get("cdp", {}).get("enabled", True))
        try:
            if cdp_on:
                self.btn_calib.pack_forget()
                self.lbl_calib_off.pack(side="left")
                self.lbl_steps.configure(
                    text="① 导入清单      →      ② 开始执行      →      ③ 查看 / 导出结果（精准模式）")
            else:
                self.lbl_calib_off.pack_forget()
                self.btn_calib.pack(side="left")
                self.lbl_steps.configure(
                    text="① 导入清单      →      ② 坐标校准      →      ③ 开始执行      →      ④ 导出结果")
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

    def ensure_debug_ready_for_run(self):
        """每次运行前确保洗衣管家处于调试模式；返回 'debug' 或 'fallback'。
        流程：端口可用→直接精准；已开但无参数→确认后关闭（正常→强制）→带参启动；
        全程刷新界面日志，最多两次启动尝试。"""
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
            text=f"已导入：{os.path.basename(path)}")
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
            self.tree.insert("", "end", iid=str(t.index),
                             values=(t.index, t.barcode, t.folder, t.display_files,
                                     t.status, t.note), tags=(tag,) if tag else ())

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

    def on_pause(self):
        if not self.control:
            return
        if self.control.pause_event.is_set():
            self.control.resume()
            self.btn_pause.configure(text="暂停")
            self.status.set("已继续。")
            self.log("已继续执行。")
        else:
            self.control.pause()
            self.btn_pause.configure(text="继续")
            self.status.set("已暂停。处理完当前步骤后停在原地，点击【继续】恢复。")
            self.log("已暂停（当前步骤完成后生效）。")

    def on_stop(self):
        if self.control:
            self.control.resume()
            self.control.stop()
            self.status.set("正在停止…")
            self.log("收到停止指令，正在安全停止…")

    def _on_run_finished(self, stats):
        self._set_running_ui(False)
        dur = time.time() - self.run_started_at if self.run_started_at else 0
        prefix = "执行结束（演练模式·未真实操作）：" if self.cfg["options"].get("dry_run") else "执行结束："
        self.status.set(prefix + f"成功 {stats.get('ok', 0)} / 失败 {stats.get('fail', 0)} / "
                        f"跳过 {stats.get('skip', 0)}，用时 {dur:.0f} 秒")
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
