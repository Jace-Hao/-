# -*- coding: utf-8 -*-
"""
calibrate.py — 坐标校准工具
================================================================
打开一个校准窗口，把鼠标移动到洗衣管家软件中的目标按钮上，
一键采集该位置的坐标（窗口相对 / 屏幕绝对两种模式任选），
也可以截取按钮的小图作为“图像锚点”（抗窗口移动，更稳）。

采集流程：
  点击【采集】→ 校准窗口自动隐藏 → 屏幕右上角出现倒计时提示
  → 把鼠标移到目标按钮上 → 倒计时结束自动记录（安装了 keyboard
    库时也可以直接按 F2 立即记录）。
"""
import os
import time
import tkinter as tk
from tkinter import ttk, messagebox

import pyautogui

try:
    import pygetwindow as gw
except Exception:
    gw = None
try:
    import keyboard as kb
except Exception:
    kb = None

from config_io import save_config
from winmatch import pick_best_title, pick_best_window

POINTS = [
    ("barcode_input", "① 条码输入框", "拍照页左侧“请输入衣物条码号”输入框（点击位置）"),
    ("more_button", "② 「更多」按钮", "左侧导航栏里的“更多”（工作页均有，用于自动切页）"),
    ("photo_button", "③ 「拍照」按钮", "更多功能页 →“收衣操作”区域里的“拍照”按钮"),
    ("upload_button", "④ 「上传图片」按钮", "拍照页右下角的“上传图片”按钮"),
    ("more_card", "②b 「更多」卡片（首页，选配）", "首页界面右侧的“更多”卡片（仅当需要从首页自动切换时采集）"),
    ("dialog_filename", "⑤ 对话框文件名框（可选）", "“选择图片”对话框底部的“文件名”输入框（可不设）"),
    ("page_home", "⑥ 页面标志：首页〔仅截锚点〕", "把鼠标放在首页上固定不变的元素上（如“收衣”大卡片），用于识别“当前在首页”"),
    ("page_more", "⑦ 页面标志：更多功能页〔仅截锚点〕", "把鼠标放在更多功能页的固定元素上（如某个功能格子图标），用于识别“当前在更多页”"),
    ("page_photo", "⑧ 页面标志：拍照页〔仅截锚点〕", "把鼠标放在拍照页的固定元素上（如“请输入衣物条码号”输入框或“查询”按钮），用于识别“当前在拍照页”"),
]


class CalibrateWindow(tk.Toplevel):
    def __init__(self, master, base_dir, cfg, on_saved=None):
        super().__init__(master)
        self.title("坐标校准 — 洗衣管家上传助手")
        self.geometry("800x620")
        self.base_dir = base_dir
        self.cfg = cfg
        self.on_saved = on_saved

        self._toast = None
        self._capturing = False
        self._capture_after_id = None

        self._build_ui()
        self._refresh_list()

    # ---------------- UI ----------------
    def _build_ui(self):
        top = ttk.Frame(self, padding=10)
        top.pack(fill="x")

        ttk.Label(
            top,
            text=("使用说明：先打开「洗衣管家」软件并调整好窗口位置，然后逐项采集。\n"
                  "操作点（①~⑤）：把鼠标移到目标按钮上采集；页面标志（⑥~⑧）：只需【截取锚点】，"
                  "用于让程序识别当前页面、避免多余点击。"
                  + ("（也可随时按 F2 立即记录）。" if kb else "。")),
            justify="left", foreground="#444"
        ).pack(anchor="w")

        mode_frame = ttk.Frame(self, padding=(10, 0))
        mode_frame.pack(fill="x")
        self.var_mode = tk.StringVar(value=self.cfg["window"].get("coords_mode", "window"))
        ttk.Label(mode_frame, text="坐标模式：").pack(side="left")
        ttk.Radiobutton(mode_frame, text="窗口相对（推荐，窗口移动后仍可用）",
                        variable=self.var_mode, value="window").pack(side="left", padx=4)
        ttk.Radiobutton(mode_frame, text="屏幕绝对",
                        variable=self.var_mode, value="absolute").pack(side="left", padx=4)

        self.var_use_anchor = tk.BooleanVar(value=bool(self.cfg["anchors"].get("enabled")))
        ttk.Checkbutton(mode_frame, text="启用图像锚点与页面识别（需先“截取锚点”）",
                        variable=self.var_use_anchor).pack(side="left", padx=12)

        cols = ("name", "value", "anchor")
        self.tree = ttk.Treeview(self, columns=cols, show="headings", height=9)
        self.tree.heading("name", text="项目")
        self.tree.heading("value", text="当前坐标")
        self.tree.heading("anchor", text="锚点")
        self.tree.column("name", width=250)
        self.tree.column("value", width=260)
        self.tree.column("anchor", width=180)
        self.tree.pack(fill="x", padx=10, pady=6)

        hint = ttk.Frame(self, padding=(10, 0))
        hint.pack(fill="x")
        ttk.Label(hint, text="提示：", foreground="#888").pack(side="left")
        self.lbl_hint = ttk.Label(hint, text="", foreground="#666")
        self.lbl_hint.pack(side="left")

        btns = ttk.Frame(self, padding=10)
        btns.pack(fill="x")
        ttk.Button(btns, text="采集坐标", command=self.capture_selected).pack(side="left")
        ttk.Button(btns, text="测试走位", command=self.test_selected).pack(side="left", padx=6)
        ttk.Button(btns, text="截取锚点", command=self.capture_anchor).pack(side="left", padx=6)
        ttk.Button(btns, text="测试页面识别", command=self._test_page_detect).pack(side="left", padx=6)
        ttk.Button(btns, text="清除该项", command=self.clear_selected).pack(side="left", padx=6)

        bottom = ttk.Frame(self, padding=10)
        bottom.pack(fill="x", side="bottom")
        ttk.Button(bottom, text="保存并关闭", command=self.save_close).pack(side="right")
        ttk.Button(bottom, text="取消", command=self.destroy).pack(side="right", padx=6)

        self.tree.bind("<<TreeviewSelect>>", lambda e: self._refresh_hint())
        # 默认选中第一项
        children = self.tree.get_children()
        if children:
            self.tree.selection_set(children[0])

    def _refresh_list(self):
        for i in self.tree.get_children():
            self.tree.delete(i)
        for key, label, desc in POINTS:
            if key.startswith("page_"):
                val = "—（仅锚点）"
            else:
                pt = self.cfg["points"].get(key)
                val = f"({pt[0]:.0f}, {pt[1]:.0f})" if pt else "— 未设置 —"
            anchor = self.cfg["anchors"].get(key) or ""
            anchor = os.path.basename(anchor) if anchor else "—"
            self.tree.insert("", "end", iid=key, values=(f"{label}", val, anchor))
        self._refresh_hint()

    def _refresh_hint(self):
        sel = self.tree.selection()
        if not sel:
            return
        key = sel[0]
        for k, label, desc in POINTS:
            if k == key:
                self.lbl_hint.configure(text=desc)

    def _selected_key(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("提示", "请先在列表里选择要操作的项目。", parent=self)
            return None
        return sel[0]

    # ---------------- 窗口查找 ----------------
    def _find_app_window(self):
        """定位「洗衣管家」主窗口（按窗口对象打分，避免“同名词包含”的误选）。"""
        if gw is None:
            return None
        keyword = self.cfg["window"].get("title_keyword", "洗衣管家")
        try:
            return pick_best_window(gw.getAllWindows(), keyword)
        except Exception:
            return None

    # ---------------- 倒计时提示（录点用） ----------------
    def _show_toast(self, text):
        self._hide_toast()
        t = tk.Toplevel(self)
        t.overrideredirect(True)
        t.attributes("-topmost", True)
        w, h = 360, 64
        sw = t.winfo_screenwidth()
        t.geometry(f"{w}x{h}+{sw - w - 24}+24")
        lab = tk.Label(t, text=text, bg="#222", fg="#fff",
                       font=("Microsoft YaHei", 12), justify="center")
        lab.pack(fill="both", expand=True)
        self._toast = (t, lab)
        return t, lab

    def _hide_toast(self):
        if self._toast:
            try:
                self._toast[0].destroy()
            except Exception:
                pass
            self._toast = None

    # ---------------- 采集坐标 ----------------
    def capture_selected(self):
        key = self._selected_key()
        if not key or self._capturing:
            return
        if key.startswith("page_"):
            messagebox.showinfo(
                "提示",
                "“页面标志”不需要采集坐标（程序用它来识别当前页面）。\n\n"
                "请选择该项后点【截取锚点】：把鼠标放在该页面上一个小块、"
                "固定不变的内容上（如按钮/图标/输入框），截取即可。",
                parent=self)
            return
        self._capturing = True
        self.withdraw()                     # 隐藏校准窗口
        time.sleep(0.25)
        t, lab = self._show_toast("请将鼠标移到目标位置… 3")
        captured = {"done": False}

        def do_capture():
            captured["done"] = True

        hotkey_handle = None
        if kb is not None:
            try:
                hotkey_handle = kb.add_hotkey("f2", do_capture)
            except Exception:
                hotkey_handle = None

        for remain in (3, 2, 1):
            if captured["done"]:
                break
            lab.configure(text=f"请将鼠标移到目标位置… {remain}"
                               + ("（或按 F2 立即记录）" if hotkey_handle else ""))
            for _ in range(10):
                if captured["done"]:
                    break
                self.update()
                time.sleep(0.1)
        pos = pyautogui.position()
        if hotkey_handle is not None:
            try:
                kb.remove_hotkey(hotkey_handle)
            except Exception:
                pass
        self._hide_toast()
        self.deiconify()
        self.lift()
        self._capturing = False

        x, y = int(pos.x), int(pos.y)
        mode = self.var_mode.get()
        if mode == "window":
            win = self._find_app_window()
            if win is None:
                if not messagebox.askyesno(
                        "未找到洗衣管家窗口",
                        "当前未找到「洗衣管家」窗口，无法换算成“窗口相对坐标”。\n\n"
                        "是否改为按“屏幕绝对坐标”保存本次采集？\n"
                        "（建议：打开洗衣管家窗口后重新采集，或切换到“屏幕绝对”模式）",
                        parent=self):
                    return
                mode = "absolute"
                self.var_mode.set("absolute")
            else:
                rx, ry = x - int(win.left), y - int(win.top)
                w, h = int(win.width), int(win.height)
                if not (-5 <= rx <= w + 5 and -5 <= ry <= h + 5):
                    if messagebox.askyesno(
                            "该点不在窗口范围内",
                            f"采集的位置不在检测到的「洗衣管家」窗口范围内\n"
                            f"（窗口：{win.title}，大小 {w}×{h}）。\n\n"
                            "这可能说明窗口识别不对，或刚采的点不在目标窗口上。\n\n"
                            "仍要按“窗口相对坐标”保存吗？\n"
                            "选“否”将按“屏幕绝对坐标”保存。",
                            parent=self):
                        x, y = rx, ry
                    else:
                        mode = "absolute"
                        self.var_mode.set("absolute")
                else:
                    x, y = rx, ry
        self.cfg["points"][key] = [x, y]
        self._refresh_list()
        self.tree.selection_set(key)
        for k, label, desc in POINTS:
            if k == key:
                messagebox.showinfo("已记录",
                                    f"{label}\n已记录坐标：({x:.0f}, {y:.0f})\n"
                                    f"模式：{'窗口相对' if mode == 'window' else '屏幕绝对'}",
                                    parent=self)
                break

    # ---------------- 测试走位 ----------------
    def test_selected(self):
        key = self._selected_key()
        if not key:
            return
        if key.startswith("page_"):
            self._test_page_detect()
            return
        pt = self.cfg["points"].get(key)
        if not pt:
            messagebox.showinfo("提示", "该项还没有坐标，请先采集。", parent=self)
            return
        x, y = float(pt[0]), float(pt[1])
        if self.var_mode.get() == "window":
            win = self._find_app_window()
            if win is not None:
                x, y = x + int(win.left), y + int(win.top)
            else:
                messagebox.showinfo("提示", "未找到洗衣管家窗口，按屏幕绝对坐标测试。", parent=self)
        pyautogui.moveTo(int(x), int(y), duration=0.3)
        self.lbl_hint.configure(text=f"已把鼠标移动到 ({x:.0f}, {y:.0f})，请观察是否对准目标。")

    # ---------------- 截取锚点 ----------------
    def capture_anchor(self):
        key = self._selected_key()
        if not key or self._capturing:
            return
        self._capturing = True
        self.withdraw()
        time.sleep(0.25)
        t, lab = self._show_toast("请将鼠标移到目标按钮上截取锚点… 3")
        time.sleep(3.0)
        pos = pyautogui.position()
        self._hide_toast()
        self.deiconify()
        self.lift()
        self._capturing = False

        try:
            sw, sh = pyautogui.size()
            w, h = 240, 72
            left = max(0, min(int(pos.x) - w // 2, sw - w))
            top = max(0, min(int(pos.y) - h // 2, sh - h))
            shot = pyautogui.screenshot(region=(left, top, w, h))
            adir = os.path.join(self.base_dir, "anchors")
            os.makedirs(adir, exist_ok=True)
            fname = f"{key}.png"
            path = os.path.join(adir, fname)
            shot.save(path)
            self.cfg["anchors"][key] = os.path.join("anchors", fname)
            self.var_use_anchor.set(True)
            self._refresh_list()
            if key.startswith("page_"):
                messagebox.showinfo("已截取页面标志",
                                    f"已保存：anchors\\{fname}\n\n"
                                    "建议点击【测试页面识别】验证程序能否识别到该页面。",
                                    parent=self)
            else:
                messagebox.showinfo("已截取锚点",
                                    f"已保存锚点图片：anchors\\{fname}\n"
                                    "建议点击【测试走位】检查是否对准，或用软件里的小图预览核对。",
                                    parent=self)
        except Exception as e:
            messagebox.showerror("截取失败", f"截取锚点失败：{e}", parent=self)

    # ---------------- 测试页面识别 ----------------
    def _test_page_detect(self):
        """在当前屏幕上试识别一次页面（用于验证页面标志锚点）。"""
        try:
            from automation import detect_page, PAGE_NAMES
        except Exception as e:
            messagebox.showerror("不可用", f"页面识别模块加载失败：{e}", parent=self)
            return
        win = self._find_app_window()
        region = None
        if win is not None:
            region = (int(win.left), int(win.top), int(win.width), int(win.height))
        self.lbl_hint.configure(text="正在识别当前页面…")
        self.update()
        page = detect_page(self.cfg, self.base_dir, region=region, per_timeout=1.2,
                           require_enabled=False)
        if page:
            messagebox.showinfo("页面识别结果",
                                f"当前识别为：【{PAGE_NAMES.get(page, page)}】✔\n\n"
                                "正式运行时，程序会据此决定是否需要点击（避免多余点击）。",
                                parent=self)
            self.lbl_hint.configure(text=f"页面识别：{PAGE_NAMES.get(page, page)}")
        else:
            messagebox.showinfo("页面识别结果",
                                "未能识别到任何【页面标志】。\n\n"
                                "请确认：\n"
                                "① 已对 ⑥⑦⑧ 三个页面标志【截取锚点】；\n"
                                "② 洗衣管家窗口没被其他窗口遮挡；\n"
                                "③ 截取时鼠标位置确实在固定不变的小块内容上（别选大片空白）；\n"
                                "④ 当前页面与已采集的标志对应（比如现在在拍照页，就应能识别到⑧）。",
                                parent=self)
            self.lbl_hint.configure(text="页面识别：未识别到")

    # ---------------- 清除 ----------------
    def clear_selected(self):
        key = self._selected_key()
        if not key:
            return
        self.cfg["points"][key] = None
        self.cfg["anchors"][key] = ""
        self._refresh_list()
        self.tree.selection_set(key)

    # ---------------- 保存 ----------------
    def save_close(self):
        self.cfg["window"]["coords_mode"] = self.var_mode.get()
        self.cfg["anchors"]["enabled"] = bool(self.var_use_anchor.get())
        p = save_config(self.base_dir, self.cfg)
        if self.on_saved:
            self.on_saved()
        messagebox.showinfo("已保存", f"校准结果已保存到：\n{p}", parent=self)
        self.destroy()


def open_calibration(parent, base_dir, cfg, on_saved=None):
    win = CalibrateWindow(parent, base_dir, cfg, on_saved)
    try:
        win.transient(parent)
    except Exception:
        pass
    # 等窗口真正显示后再抢焦点/模态，避免个别系统下失败
    def _grab():
        try:
            win.grab_set()
        except Exception:
            pass
    try:
        win.after(300, _grab)
        win.lift()
    except Exception:
        pass
    return win
