# -*- coding: utf-8 -*-
"""
automation.py — 自动化执行引擎
================================================================
按“绘图1”流程图实现的操作序列（每一步都可在 config.json 调整）：

  [循环：对清单中每一条记录]
  0. 激活「洗衣管家」窗口；
  1. 点击「条码输入框」→ 粘贴条码 → 回车；
     （流程图：点击条码输入框 / 输入表格中第一个条码，并单击回车）
  2. 页面状态自检（对应流程图“当页面在首页时，点击更多，否则跳过 /
     不在拍照页”两个判断）：等「上传图片」按钮出现；
     找不到就依次尝试点击「拍照」按钮、「更多」按钮，循环重试；
  3. 点击「上传图片」；（流程图：点击上传图片）
  4. 等待“选择图片”文件对话框；粘贴该条码对应的文件夹路径 → 回车；
     （流程图：输入条码对应的存储位置）
  5. 全选文件夹内的文件（Ctrl+A）→ 回车；（流程图：全选文件夹内的文件并点击回车）
  6. 记录结果，进入下一条；（流程图：循环操作，直至表格结束）

所有点击目标支持两种定位方式：
  - 坐标模式：在【坐标校准】中把鼠标挪到按钮上采集即可；
  - 图像锚点模式（可选）：截取按钮小图，自动在窗口内找位置，抗窗口移动。
"""
import os
import time
import threading

import pyautogui

from winmatch import pick_best_title, pick_best_window

try:
    from cdp_control import CDPApp
except Exception:  # pragma: no cover
    CDPApp = None

try:
    import pyperclip
except Exception:  # pragma: no cover
    pyperclip = None
try:
    import pygetwindow as gw
except Exception:  # pragma: no cover
    gw = None

import ctypes

try:
    import win32gui
    import win32con
    import win32api
except Exception:  # pragma: no cover
    win32gui = win32con = win32api = None

pyautogui.FAILSAFE = True          # 鼠标甩到屏幕左上角 = 紧急停止
pyautogui.PAUSE = 0.03


# ----------------------------------------------------------------------------
# 页面识别（根据「页面标志」锚点判断当前在哪一页）
# ----------------------------------------------------------------------------
PAGE_NAMES = {"home": "首页", "more": "更多功能页", "photo": "拍照页"}


def _anchor_path(cfg, base_dir, name):
    p = (cfg.get("anchors") or {}).get(name) or ""
    if not p:
        return None
    if not os.path.isabs(p):
        p = os.path.join(base_dir, p)
    return p if os.path.exists(p) else None


def ascii_safe_path(path):
    """把含非 ASCII 字符的模板路径先复制到临时 ASCII 路径，
    规避 cv2.imread / pyautogui 在 Windows 中文路径下的兼容问题。"""
    try:
        if all(ord(ch) < 128 for ch in str(path)):
            return path
        import tempfile, hashlib, shutil
        if not os.path.isfile(path):
            return path
        key = hashlib.md5((os.path.abspath(path) + "|" + str(os.path.getmtime(path))
                           + "|" + str(os.path.getsize(path))).encode("utf-8")).hexdigest()[:14]
        d = os.path.join(tempfile.gettempdir(), "lu_anchor_cache")
        os.makedirs(d, exist_ok=True)
        dst = os.path.join(d, "t_" + key + ".png")
        if not os.path.exists(dst):
            shutil.copyfile(path, dst)
        return dst
    except Exception:
        return path


def locate_anchor_once(cfg, base_dir, name, timeout=0.9, confidence=0.85, region=None):
    """查找单个锚点，找到返回 (x, y)，否则 None。"""
    path = _anchor_path(cfg, base_dir, name)
    if not path:
        return None
    path = ascii_safe_path(path)
    deadline = time.time() + max(0.1, timeout)
    while time.time() < deadline:
        try:
            box = pyautogui.locateOnScreen(path, region=region, confidence=confidence, grayscale=True)
            if box:
                c = pyautogui.center(box)
                return int(c.x), int(c.y)
        except Exception:
            try:
                box = pyautogui.locateOnScreen(path, region=region) if region else pyautogui.locateOnScreen(path)
                if box:
                    c = pyautogui.center(box)
                    return int(c.x), int(c.y)
            except Exception:
                return None
        time.sleep(0.2)
    return None


def detect_page(cfg, base_dir, region=None, per_timeout=0.9, require_enabled=True):
    """按页面标志锚点识别当前页面：返回 'photo' | 'more' | 'home' | None。
    每个页面支持多个指纹（xxx 与 xxx_b），任一命中即算该页。"""
    anchors = cfg.get("anchors") or {}
    if require_enabled and not anchors.get("enabled"):
        return None
    for names, page in ((('page_photo_b', 'page_photo'), 'photo'),
                        (('page_more', 'page_more_b'), 'more'),
                        (('page_home_b', 'page_home'), 'home')):
        for name in names:
            if anchors.get(name):
                if locate_anchor_once(cfg, base_dir, name, timeout=per_timeout, region=region):
                    return page
    return None


# ----------------------------------------------------------------------------
# 运行控制（暂停 / 停止）
# ----------------------------------------------------------------------------
class RunControl:
    def __init__(self):
        self.stop_event = threading.Event()
        self.pause_event = threading.Event()   # set 表示处于暂停

    def stop(self):
        self.stop_event.set()

    def pause(self):
        self.pause_event.set()

    def resume(self):
        self.pause_event.clear()

    @property
    def stopped(self):
        return self.stop_event.is_set()

    def wait_if_paused(self):
        while self.pause_event.is_set() and not self.stop_event.is_set():
            time.sleep(0.15)


class StepError(Exception):
    """某一步骤失败（用于把失败原因传给上层记录）。"""
    pass


# ----------------------------------------------------------------------------
# 引擎
# ----------------------------------------------------------------------------
class AutomationEngine:
    def __init__(self, cfg: dict, log, control: RunControl):
        """
        cfg  : 配置字典（config_io 加载）
        log  : 日志回调 log(text)   —— UI 层负责显示
        control : RunControl
        """
        self.cfg = cfg
        self.log = log
        self.ctrl = control
        self.window = None
        self._anchor_cache = {}
        self._last_page = None
        self.cdp = None

    # ---------------- 基础工具 ----------------
    def _tf(self, key, default=0.5):
        """取时间参数，乘速度倍率。"""
        t = self.cfg["timing"].get(key, default)
        factor = float(self.cfg["timing"].get("speed_factor", 1.0) or 1.0)
        return max(0.0, t * factor)

    def sleep(self, seconds):
        end = time.time() + seconds
        while time.time() < end:
            if self.ctrl.stopped:
                raise StepError("用户停止")
            self.ctrl.wait_if_paused()
            time.sleep(0.05)

    # ---------------- 窗口 ----------------
    def activate_window(self):
        """定位并激活「洗衣管家」主窗口（自动避开本工具/浏览器等干扰窗口）。"""
        if gw is None:
            self.log("[警告] 未安装 pygetwindow，跳过窗口激活，将直接操作屏幕。")
            return None
        keyword = self.cfg["window"].get("title_keyword", "洗衣管家")
        timeout = float(self.cfg["window"].get("activate_timeout", 5.0))
        deadline = time.time() + timeout
        win = None
        while time.time() < deadline and not self.ctrl.stopped:
            try:
                wins = gw.getAllWindows()
            except Exception:
                wins = []
            w = pick_best_window(wins, keyword)   # 直接按窗口对象打分，避免“同名词包含”的误选
            if w is not None:
                win = w
                break
            time.sleep(0.3)
        if win is None:
            raise StepError(
                f"未找到「洗衣管家」主窗口（匹配关键词：{keyword}）。"
                "请先打开洗衣管家软件，或到【坐标校准】确认窗口关键词设置。")
        self.window = win
        try:
            win_title = win.title
        except Exception:
            win_title = "（标题读取失败）"
        self.log(f"目标窗口：{win_title}")
        self._ensure_foreground(win)          # 确认已切到前台，失败则中止（避免点击落空）
        self.sleep(self._tf("after_activate", 0.6))
        return win

    def _ensure_foreground(self, win):
        """把目标窗口带到前台并验证；失败则报错，防止后续点击全部落空。"""
        import ctypes
        user32 = ctypes.windll.user32
        try:
            hwnd = int(getattr(win, "_hWnd", 0) or 0)
        except Exception:
            hwnd = 0

        def _fg():
            try:
                return int(user32.GetForegroundWindow())
            except Exception:
                return 0

        if hwnd and _fg() == hwnd:
            return
        # 1) 常规激活
        try:
            if getattr(win, "isMinimized", False):
                win.restore()
                time.sleep(0.25)
            win.activate()
        except Exception:
            pass
        time.sleep(0.25)
        if not hwnd or _fg() == hwnd:
            return
        # 2) 兜底：最小化 + 还原（通常可强制带到前台）
        try:
            win.minimize()
            time.sleep(0.2)
            win.restore()
        except Exception:
            pass
        time.sleep(0.3)
        if _fg() == hwnd:
            return
        raise StepError(
            "无法将「洗衣管家」窗口切到前台（可能被其他窗口遮挡）。"
            "请手动点一下洗衣管家窗口，再点击【开始执行】。")

    def _win_rect(self):
        """当前窗口区域 (left, top, w, h)，无窗口信息时返回 None。"""
        w = self.window
        if not w:
            return None
        try:
            return (int(w.left), int(w.top), int(w.width), int(w.height))
        except Exception:
            return None

    # ---------------- 底层动作（支持演练模式） ----------------
    def _dry(self):
        return bool(self.cfg["options"].get("dry_run", False))

    def _do_click(self, x, y):
        # 越界保护：屏幕边缘/角落的点击会误触 pyautogui 急停，直接阻止并报错
        try:
            sw, sh = pyautogui.size()
            if x <= 2 or y <= 2 or x >= sw - 2 or y >= sh - 2 or x < 0 or y < 0:
                raise StepError(
                    f"点击坐标 ({int(x)}, {int(y)}) 越界或位于屏幕边缘（会误触急停），已阻止。"
                    "请检查该项校准（可能参照了错误窗口）。")
        except StepError:
            raise
        except Exception:
            pass
        if self._dry():
            try:
                pyautogui.moveTo(int(x), int(y), duration=0.2)
            except Exception:
                pass
            self.log(f"    [演练] 移动鼠标到 ({int(x)}, {int(y)})（不点击）")
        else:
            pyautogui.click(int(x), int(y))

    def _do_press(self, key):
        if self._dry():
            self.log(f"    [演练] 按键 {key}（不发送）")
        else:
            pyautogui.press(key)

    def _do_hotkey(self, *keys):
        if self._dry():
            self.log(f"    [演练] 组合键 {'+'.join(keys)}（不发送）")
        else:
            pyautogui.hotkey(*keys)

    # ---------------- 坐标 / 锚点 ----------------
    def _resolve_point(self, name):
        """把配置中的点名解析为屏幕绝对坐标。"""
        pt = self.cfg["points"].get(name)
        if not pt:
            raise StepError(f"未设置【{name}】坐标，请先在【坐标校准】中采集。")
        x, y = float(pt[0]), float(pt[1])
        mode = self.cfg["window"].get("coords_mode", "window")
        if mode == "window":
            rect = self._win_rect()
            if rect is None:
                raise StepError("窗口相对模式下未获取到「洗衣管家」窗口，请确认软件已打开后重试。")
            w, h = rect[2], rect[3]
            if not (-60 <= x <= w + 60 and -60 <= y <= h + 60):
                raise StepError(
                    f"【{name}】的坐标 ({x:.0f}, {y:.0f}) 超出「洗衣管家」窗口范围，"
                    "疑似校准时参照了错误窗口——请重新校准该项。")
            return int(rect[0] + x), int(rect[1] + y), rect
        return int(x), int(y), None

    def _find_anchor(self, name, timeout=2.0, confidence=0.85):
        """
        在窗口区域内查找图像锚点，找到返回中心点坐标，否则 None。
        """
        path = self.cfg["anchors"].get(name, "")
        if not path:
            return None
        if not os.path.isabs(path):
            base = getattr(self, "base_dir", "")
            path = os.path.join(base, path)
        if not os.path.exists(path):
            return None
        path = ascii_safe_path(path)      # 中文路径兼容（转存到 ASCII 临时缓存）
        region = self._win_rect()
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.ctrl.stopped:
                return None
            found = None
            try:
                if region:
                    box = pyautogui.locateOnScreen(path, region=region,
                                                   confidence=confidence, grayscale=True)
                else:
                    box = pyautogui.locateOnScreen(path, confidence=confidence, grayscale=True)
                if box:
                    found = pyautogui.center(box)
            except Exception:
                # 降级：不带 confidence（无 OpenCV 环境）再试
                try:
                    if region:
                        box = pyautogui.locateOnScreen(path, region=region)
                    else:
                        box = pyautogui.locateOnScreen(path)
                    if box:
                        found = pyautogui.center(box)
                except Exception:
                    return None
            if found:
                return int(found.x), int(found.y), region
            time.sleep(0.25)
        return None

    def _click_point(self, name, why="", use_anchor_first=None, anchor_timeout=1.2):
        """点击某配置点：优先锚点（若启用且可用），否则坐标。"""
        self.ctrl.wait_if_paused()
        if self.ctrl.stopped:
            raise StepError("用户停止")
        anchor_name = use_anchor_first if use_anchor_first is not None else name
        if self.cfg["anchors"].get("enabled") and self.cfg["anchors"].get(anchor_name):
            found = self._find_anchor(anchor_name, timeout=anchor_timeout)
            if found:
                x, y, _ = found
                self._do_click(x, y)
                self.sleep(self._tf("after_click", 0.6))
                return True
        x, y, _ = self._resolve_point(name)
        self._do_click(x, y)
        self.sleep(self._tf("after_click", 0.6))
        return True

    def _click_at(self, x, y, wait=None):
        self._do_click(x, y)
        self.sleep(self._tf("after_click", 0.6) if wait is None else wait)

    def _paste_text(self, text):
        """剪贴板粘贴（支持中文/任意字符）。"""
        if self._dry():
            self.log(f"    [演练] 输入文本：{text}（不发送）")
            return
        if pyperclip is not None:
            pyperclip.copy(text)
            time.sleep(0.08)
            pyautogui.hotkey("ctrl", "v")
        else:
            pyautogui.write(text, interval=0.02)

    # ---------------- 各步骤 ----------------
    def step_enter_barcode(self, barcode):
        """点击条码输入框 → 粘贴条码 → 回车。"""
        self._click_point("barcode_input", "点击条码输入框",
                          use_anchor_first="barcode_input", anchor_timeout=1.0)
        self.log("    已点击条码输入框，输入条码并回车…")
        self._paste_text(barcode)
        time.sleep(0.15)
        self._do_press("enter")
        self.sleep(self._tf("after_enter", 1.5))

    def _try_find_upload_button(self):
        """检测“上传图片”按钮是否出现（锚点优先；坐标模式则直接认为可用）。"""
        if self.cfg["anchors"].get("enabled") and self.cfg["anchors"].get("upload_button"):
            found = self._find_anchor("upload_button", timeout=1.0)
            return found
        return None

    # ---------------- 页面识别 ----------------
    def _anchor_exists(self, name):
        return bool(_anchor_path(self.cfg, self.base_dir, name))

    def _page_detect_ready(self):
        """是否具备页面识别能力（已启用锚点 + 至少有一个页面指纹）。"""
        a = self.cfg.get("anchors") or {}
        return bool(a.get("enabled")) and any(
            self._anchor_exists(n) for n in
            ("page_home", "page_home_b", "page_more", "page_more_b", "page_photo", "page_photo_b"))

    def detect_page(self):
        """识别当前页面（变化时记日志）。"""
        if not self._page_detect_ready():
            return None
        page = detect_page(self.cfg, self.base_dir, region=self._win_rect(), per_timeout=0.9)
        if page != self._last_page:
            if page:
                self.log(f"    页面识别：当前在【{PAGE_NAMES.get(page, page)}】")
            self._last_page = page
        return page

    def _click_more(self, context="点击更多"):
        """点“更多”（优先首页的“更多”卡片，否则左侧导航的“更多”）。"""
        anchor_mode = self.cfg["anchors"].get("enabled")
        if (anchor_mode and self.cfg["anchors"].get("more_card")) or self._anchor_exists("more_card"):
            try:
                self._click_point("more_card", context, use_anchor_first="more_card")
                return
            except StepError:
                pass
        self._click_point("more_button", context, use_anchor_first="more_button")

    def prepare_batch(self):
        """
        批次开始前的一次性准备：
        - 有页面标志锚点：先识别当前页面，只做必要的切换（避免多余点击）；
        - 仅坐标模式：按配置执行一次“更多 → 拍照”预热导航。
        """
        anchor_mode = self.cfg["anchors"].get("enabled")
        page_mode = self._page_detect_ready()
        warmup = self.cfg["options"].get("warmup_nav", True)

        if page_mode:
            page = self.detect_page()
            if page == "photo":
                self.log("准备：当前已在【拍照页】，无需切换。")
                return
            if page == "more":
                self.log("准备：当前在【更多功能页】，点击【拍照】…")
                try:
                    self._click_point("photo_button", "点击拍照", use_anchor_first="photo_button")
                except StepError as e:
                    self.log(f"准备：点击【拍照】未完成（{e}）")
                self.sleep(0.7)
                return
            if page == "home":
                self.log("准备：当前在【首页】，点击【更多】…")
                try:
                    self._click_more()
                except StepError as e:
                    self.log(f"准备：点击【更多】未完成（{e}）")
                self.sleep(0.7)
                self.log("准备：点击【拍照】…")
                try:
                    self._click_point("photo_button", "点击拍照", use_anchor_first="photo_button")
                except StepError as e:
                    self.log(f"准备：点击【拍照】未完成（{e}）")
                self.sleep(0.7)
                return
            self.log("准备：暂未能识别当前页面；执行时逐条自检会自动纠正。")
            return

        if anchor_mode:
            return
        if not warmup:
            self.log("（坐标模式）未启用预热导航，请确认软件已处于【拍照】页。")
            return
        pts = self.cfg["points"]
        if not pts.get("more_button") or not pts.get("photo_button"):
            self.log("（坐标模式）未采集「更多/拍照」坐标，跳过预热；请手动把软件切到【拍照】页。")
            return
        self.log("（坐标模式）预热：点击【更多】→【拍照】，切换到拍照页…")
        try:
            self._click_point("more_button", "点击更多")
            self.sleep(0.8)
            self._click_point("photo_button", "点击拍照")
            self.sleep(0.8)
        except StepError as e:
            self.log(f"（坐标模式）预热导航未完成：{e}")

    def step_ensure_ready(self):
        """
        页面状态自检与导航（对应流程图“当页面在首页时，点击更多，否则跳过 / 不在拍照页”）：
        - 有【页面标志】锚点：先识别当前页面，按最少点击到达可上传状态（避免多余点击）；
        - 仅按钮锚点：逐级重试（旧逻辑）；
        - 纯坐标：不做状态检测（由准备阶段/人工保证）。
        """
        cycles = int(self.cfg["timing"].get("step_retry_cycles", 3))
        button_anchor_mode = self.cfg["anchors"].get("enabled")
        page_mode = self._page_detect_ready()
        upload_detectable = bool(button_anchor_mode and self.cfg["anchors"].get("upload_button"))

        if not page_mode and not button_anchor_mode:
            return "坐标模式"

        for i in range(cycles):
            if self.ctrl.stopped:
                raise StepError("用户停止")

            if upload_detectable and self._try_find_upload_button():
                return "已在上传图片就绪页面"

            if page_mode:
                page = self.detect_page()
                if page == "photo":
                    if not upload_detectable:
                        return "已在拍照页（页面识别）"
                    self.sleep(0.6)          # 已在拍照页，等“上传图片”就绪
                    continue
                if page == "more":
                    self._click_point("photo_button", "点击拍照", use_anchor_first="photo_button")
                    self.sleep(0.7)
                    continue
                if page == "home":
                    self._click_more()
                    self.sleep(0.7)
                    continue
                self.sleep(0.6)              # 页面未知：稍后重试
                continue

            # ——— 无页面标志：按按钮锚点逐级尝试 ———
            if self.cfg["anchors"].get("photo_button") and self._find_anchor("photo_button", timeout=0.8):
                self._click_point("photo_button", "点击拍照", use_anchor_first="photo_button")
                self.sleep(0.8)
                continue
            clicked_more = False
            for more_anchor in ("more_button", "more_card"):
                if self.cfg["anchors"].get(more_anchor) and self._find_anchor(more_anchor, timeout=0.8):
                    self._click_point(more_anchor, "点击更多", use_anchor_first=more_anchor)
                    self.sleep(0.8)
                    clicked_more = True
                    break
            if clicked_more:
                continue

        # 最后一轮放宽检查
        if upload_detectable and self._find_anchor("upload_button", timeout=3.0):
            return "已在上传图片就绪页面"
        if page_mode and self.detect_page() == "photo":
            return "已在拍照页（页面识别）"
        if not button_anchor_mode and not page_mode:
            return "坐标模式"
        raise StepError("多轮尝试后仍无法确认已进入可上传页面，请检查【页面标志】锚点或窗口状态。")

    def step_click_upload(self):
        """点击“上传图片”。"""
        self._click_point("upload_button", "点击上传图片",
                          use_anchor_first="upload_button", anchor_timeout=1.5)
        self.log("    已点击【上传图片】，等待文件对话框…")
        self.sleep(self._tf("after_upload_click", 1.2))

    def _wait_dialog(self, timeout=None):
        """等待“选择图片”对话框出现（pygetwindow 按标题匹配）。"""
        timeout = timeout if timeout is not None else self._tf("dialog_timeout", 8.0)
        if gw is None:
            return True  # 无窗口库时假设已出现
        kws = self.cfg["dialog"].get("title_keywords") or ["打开", "Open"]
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.ctrl.stopped:
                raise StepError("用户停止")
            try:
                titles = [t for t in gw.getAllTitles() if t]
            except Exception:
                titles = []
            for t in titles:
                if any(k.lower() in t.lower() for k in kws):
                    return True
            time.sleep(0.3)
        return False

    def step_upload_folder(self, folder):
        """
        在已弹出的文件对话框中：输入条码对应的存储位置 → 回车；
        随后 全选文件（Ctrl+A）→ 回车。
        非演练模式下，对话框未出现/未关闭会明确报错（不再“假装成功”）。
        """
        dry = self._dry()
        strict = bool(self.cfg["options"].get("strict_dialog_check", True))
        # 到这一步光标通常已在“文件名”输入框中；保险起见，如配置了对话框文件名框坐标则点击之
        if self.cfg["points"].get("dialog_filename"):
            try:
                self._click_point("dialog_filename", "点击对话框文件名输入框")
            except StepError:
                pass
        ok_dialog = True if dry else self._wait_dialog()
        if not dry and not ok_dialog:
            if strict:
                raise StepError("未检测到「选择图片」文件对话框——可能“上传图片”没有点到，"
                                "或页面/坐标不对。本条未真正执行。")
            self.log("    [警告] 未检测到文件对话框窗口，仍尝试执行粘贴路径…")
        elif ok_dialog and not dry:
            self.log("    文件对话框已出现。")

        self._paste_text(folder)
        time.sleep(0.15)
        self._do_press("enter")                        # 进入文件夹
        self.sleep(self._tf("after_path_enter", 1.0))

        self._do_hotkey("ctrl", "a")                   # 全选文件夹内的文件
        self.sleep(0.3)
        self._do_press("enter")                        # 确认（开始上传/选择）
        self.sleep(self._tf("after_select_all", 1.0))

        # 校验：对话框应当已关闭（若一直开着，说明可能没有真正选中文件）
        if not dry and gw is not None:
            kws = ["选择图片", "打开", "Open"]          # 关闭校验用严格关键词，避免误伤
            deadline = time.time() + 6.0
            still_open = True
            while time.time() < deadline:
                if self.ctrl.stopped:
                    raise StepError("用户停止")
                try:
                    titles = [t for t in gw.getAllTitles() if t]
                except Exception:
                    titles = []
                if not any(any(k.lower() in t.lower() for k in kws) for t in titles):
                    still_open = False
                    break
                time.sleep(0.3)
            if still_open:
                if strict:
                    raise StepError("文件对话框一直未关闭——可能没有成功选中文件，"
                                    "请检查该条码文件夹内容与坐标/窗口关键词。")
                self.log("    [警告] 文件对话框似乎仍未关闭，请人工确认。")

    def screenshot_on_error(self, name):
        """失败时截图，便于排查。"""
        try:
            out_dir = getattr(self, "output_dir", "logs")
            os.makedirs(os.path.join(out_dir, "出错截图"), exist_ok=True)
            p = os.path.join(out_dir, "出错截图",
                             f"{name}_{time.strftime('%H%M%S')}.png")
            pyautogui.screenshot(p)
            return p
        except Exception:
            return None

    # ---------------- 单条执行 ----------------
    def run_one(self, task):
        """执行一条记录，返回 (success: bool, message: str)。"""
        t0 = time.time()
        try:
            if self.cdp is not None:
                ok, msg = self._run_one_cdp(task)
                task.duration = time.time() - t0
                return ok, msg
            self.activate_window()
            self.step_enter_barcode(task.barcode)          # 点击输入框+输入条码+回车
            state = self.step_ensure_ready()               # 首页/更多/拍照 判断
            self.step_click_upload()                       # 点击“上传图片”
            self.step_upload_folder(task.folder)           # 路径→全选→回车
            used = time.time() - t0
            task.duration = used
            return True, f"完成（{state}，耗时 {used:.1f}s）"
        except StepError as e:
            task.duration = time.time() - t0
            if self.cfg["options"].get("screenshot_on_error", True):
                p = self.screenshot_on_error(f"{task.index}_{task.barcode}")
                tail = f"，已截图：{p}" if p else ""
            else:
                tail = ""
            return False, f"{e}{tail}"
        except pyautogui.FailSafeException:
            self.ctrl.stop()
            return False, ("触发急停：鼠标/点击坐标落到了屏幕左上角。"
                           "请把鼠标移离屏幕左上角，并检查坐标校准后重试。")
        except Exception as e:
            return False, f"异常：{type(e).__name__}: {e}"

    # ---------------- 调试端口（精准）模式 ----------------
    def init_cdp(self):
        """尝试连接洗衣管家调试端口；成功则启用精准模式。"""
        self.cdp = None
        if CDPApp is None:
            self.log("调试控制模块不可用，使用图像/坐标模式。")
            return False
        cdp_cfg = self.cfg.get("cdp", {}) or {}
        if not cdp_cfg.get("enabled", True):
            self.log("调试模式已在配置中关闭，使用图像/坐标模式。")
            return False
        port = int(cdp_cfg.get("port", 9222))
        app = CDPApp(port, routes=cdp_cfg.get("routes"))
        ok, info = app.connect()
        if ok:
            self.cdp = app
            self.log(f"已连接洗衣管家调试端口 {port}（精准模式）；页面：{info}")
            return True
        self.log(f"未连接调试端口（{info}）——使用图像/坐标模式。")
        return False

    def prepare_batch_cdp(self):
        page = self.cdp.page()
        name = PAGE_NAMES.get(page, "未知") if page else "未知"
        self.log(f"准备：调试模式已就绪，当前页面：【{name}】")
        if self._dry():
            return
        if page != "photo":
            if self.cdp.goto_page("photo"):
                self.log("准备：已切换到【拍照页】。")
            else:
                self.log("准备：未能自动切换到【拍照页】（执行每行时会再尝试）。")
        else:
            self.log("准备：当前已在【拍照页】，无需切换。")

    @staticmethod
    def _collect_folder_files(folder):
        exts = (".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff", ".gif")
        try:
            names = sorted(os.listdir(folder))
        except OSError:
            return []
        return [os.path.join(folder, n) for n in names
                if n.lower().endswith(exts) and os.path.isfile(os.path.join(folder, n))]

    # ---------------- 精准模式：文件对话框操作 ----------------
    def _find_select_dialog(self, timeout=10):
        """等待「选择图片」文件对话框出现（#32770 + 标题关键词）。"""
        if win32gui is None:
            return None
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.ctrl.stopped:
                return None
            hits = []

            def _cb(h, _):
                try:
                    if win32gui.IsWindowVisible(h):
                        c = win32gui.GetClassName(h)
                        t = win32gui.GetWindowText(h)
                        if c == "#32770" and ("选择图片" in t or "打开" in t or "Open" in t):
                            hits.append(h)
                except Exception:
                    pass
                return True

            try:
                win32gui.EnumWindows(_cb, None)
            except Exception:
                pass
            if hits:
                return hits[0]
            time.sleep(0.4)
        return None

    def _dialog_open(self, hwnd):
        try:
            return bool(hwnd) and bool(win32gui.IsWindow(hwnd))
        except Exception:
            return False

    def _wait_dialog_closed(self, hwnd, seconds):
        end = time.time() + seconds
        while time.time() < end:
            if not self._dialog_open(hwnd):
                return True
            time.sleep(0.4)
        return not self._dialog_open(hwnd)

    def _force_foreground_hwnd(self, hwnd):
        """用 AttachThreadInput 绕过前台锁，把窗口强制带到前台。"""
        if win32gui is None:
            return False
        try:
            user32 = ctypes.windll.user32
            fg = user32.GetForegroundWindow()
            tid_fg = user32.GetWindowThreadProcessId(fg, None)
            tid_me = win32api.GetCurrentThreadId()
            user32.AttachThreadInput(tid_fg, tid_me, True)
            user32.ShowWindow(hwnd, 5)
            user32.BringWindowToTop(hwnd)
            user32.SetForegroundWindow(hwnd)
            user32.AttachThreadInput(tid_fg, tid_me, False)
            time.sleep(0.35)
            return int(user32.GetForegroundWindow()) == int(hwnd)
        except Exception:
            return False

    def _dlg_focus_click(self, hwnd):
        """物理点击对话框标题栏，确保其获得前台焦点（对付前台锁/焦点被抢）。"""
        try:
            r = win32gui.GetWindowRect(hwnd)
            pyautogui.click(r[0] + (r[2] - r[0]) // 2, r[1] + 8)
            time.sleep(0.35)
            return True
        except Exception:
            return False

    def _clipboard_set(self, text, tries=4):
        """写剪贴板并回读校验（防剪贴板被占用导致静默失败）。"""
        if pyperclip is None:
            return False
        for _i in range(tries):
            try:
                pyperclip.copy(text)
                time.sleep(0.12)
                if pyperclip.paste() == text:
                    return True
            except Exception:
                time.sleep(0.2)
        return False

    def _paste_readback(self):
        """在对话框文件名框内 全选→粘贴→读回；返回读回文本（失败返回 None）。"""
        try:
            pyautogui.hotkey("ctrl", "a")
            time.sleep(0.12)
            pyautogui.hotkey("ctrl", "v")
            time.sleep(0.7)
            pyautogui.hotkey("ctrl", "a")
            time.sleep(0.12)
            pyautogui.hotkey("ctrl", "c")
            time.sleep(0.4)
            return pyperclip.paste() if pyperclip else None
        except Exception:
            return None

    def _dlg_children(self, hwnd):
        """枚举对话框所有后代控件 (hwnd, class, text, rect)。"""
        res = []
        def _cb(h, _):
            try:
                res.append((h, win32gui.GetClassName(h), win32gui.GetWindowText(h),
                            win32gui.GetWindowRect(h)))
            except Exception:
                pass
            return True
        try:
            win32gui.EnumChildWindows(hwnd, _cb, None)
        except Exception:
            pass
        return res

    def _app_window_hwnd(self):
        """获取「洗衣管家」主窗口句柄（用于置顶/置前）。"""
        if gw is None:
            return None
        try:
            w = pick_best_window(gw.getAllWindows(), self.cfg["window"].get("title_keyword", "洗衣管家"))
            if w is None:
                return None
            return int(getattr(w, "_hWnd", 0) or 0) or None
        except Exception:
            return None

    def _app_set_topmost(self, topmost):
        """把「洗衣管家」窗口设为/取消最顶层（HWND_TOPMOST）。"""
        if win32gui is None:
            return False
        hwnd = self._app_window_hwnd()
        if not hwnd:
            return False
        try:
            flag = win32con.HWND_TOPMOST if topmost else win32con.HWND_NOTOPMOST
            win32gui.SetWindowPos(hwnd, flag, 0, 0, 0, 0,
                                  win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE)
            return True
        except Exception:
            return False

    def _app_foreground_soft(self):
        """软尝试把「洗衣管家」切到前台（失败不报错，仅返回 False）。"""
        hwnd = self._app_window_hwnd()
        if not hwnd:
            return False
        ok = self._force_foreground_hwnd(hwnd)
        if not ok:
            try:
                win32gui.SetForegroundWindow(hwnd)
                ok = (int(ctypes.windll.user32.GetForegroundWindow()) == int(hwnd))
            except Exception:
                pass
        return ok

    def _dlg_set_topmost(self, hwnd):
        """把对话框也设为最顶层（静默）。"""
        try:
            win32gui.SetWindowPos(hwnd, win32con.HWND_TOPMOST, 0, 0, 0, 0,
                                  win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE)
        except Exception:
            pass

    def _pick_dialog_controls(self, hwnd, timeout=6):
        """挑出对话框的【文件名框】与【打开】按钮（等待控件就绪）。
        文件名框识别：位于对话框下半部（y > 60% 高度）的最宽 Edit 控件。"""
        end = time.time() + timeout
        ed = None
        btn = None
        while time.time() < end:
            kids = self._dlg_children(hwnd)
            try:
                dlg_r = win32gui.GetWindowRect(hwnd)
                dlg_top, dlg_h = dlg_r[1], max(1, dlg_r[3] - dlg_r[1])
            except Exception:
                dlg_top, dlg_h = 0, 720
            edits = [k for k in kids if k[1] == "Edit"]
            bottom = [k for k in edits if k[3][1] > dlg_top + int(dlg_h * 0.6)]
            pool = bottom if bottom else edits
            if pool:
                ed = max(pool, key=lambda k: k[3][2] - k[3][0])[0]
            for k in kids:
                if k[1] == "Button" and ("打开" in k[2] or "Open" in k[2]):
                    btn = k[0]
                    break
            if ed and btn:
                break
            time.sleep(0.4)
        return ed, btn

    def _edit_get_text(self, hwnd, size=600):
        """读取 Edit 控件文本（跨进程 WM_GETTEXT，兼容中文，用于校验）。"""
        try:
            buf = ctypes.create_unicode_buffer(size)
            n = ctypes.windll.user32.SendMessageW(hwnd, win32con.WM_GETTEXT, size, buf)
            return buf.value
        except Exception:
            return None

    def _verify_filename_box(self, ed, quoted):
        """校验文件名框内容：WM_GETTEXT 优先；失败时用“清空剪贴板→复制→读回”兑底。
        注：必须先清空剪贴板，否则“复制失败”时会残留旧内容造成假阳性。"""
        if ed:
            for _i in range(8):
                got = self._edit_get_text(ed)
                if got == quoted:
                    return True
                time.sleep(0.15)
        try:
            if pyperclip is not None:
                pyperclip.copy('')
                time.sleep(0.1)
                pyautogui.hotkey("ctrl", "a")
                time.sleep(0.12)
                pyautogui.hotkey("ctrl", "c")
                time.sleep(0.45)
                return pyperclip.paste() == quoted
        except Exception:
            pass
        return False

    def _select_files_by_message(self, hwnd, quoted, rounds=2):
        """首选通道：WM_SETTEXT 直写文件名框 + BM_CLICK「打开」按钮。
        不依赖前台焦点与键盘，对偶发焦点失灵免疫。
        返回 True 表示对话框已被关闭（已触发接受）。"""
        for rd in range(1, rounds + 1):
            if not self._dialog_open(hwnd):
                return True
            ed, btn = self._pick_dialog_controls(hwnd, timeout=4 if rd == 1 else 3)
            if not ed:
                self.log("    [消息通道] 未找到文件名输入框")
                return False
            try:
                win32gui.SendMessage(ed, win32con.WM_SETTEXT, 0, quoted)
            except Exception as e:
                self.log(f"    [消息通道] 写入失败：{e!r}")
                return False
            time.sleep(0.25)
            got = self._edit_get_text(ed)
            if got != quoted:
                try:
                    win32gui.SendMessage(ed, win32con.WM_SETTEXT, 0, quoted)
                except Exception:
                    pass
                time.sleep(0.25)
                got = self._edit_get_text(ed)
            if got != quoted:
                self.log("    [消息通道] 写入后校验未通过（将改用备用方式）")
                return False
            if btn:
                try:
                    win32gui.SendMessage(btn, win32con.BM_CLICK, 0, 0)
                except Exception:
                    pass
            else:
                try:
                    win32gui.SendMessage(hwnd, win32con.WM_COMMAND, 1, 0)
                except Exception:
                    pass
            for _i in range(10):
                if not self._dialog_open(hwnd):
                    return True
                time.sleep(0.5)
            self.log(f"    [消息通道] 第 {rd} 轮未关闭对话框")
        return False

    def _select_files_in_dialog(self, hwnd, files, folder):
        """在「选择图片」对话框中选中文件。
        首选：WM_SETTEXT 直写文件名框 + BM_CLICK「打开」（不依赖焦点/键盘）；
        兜底 1：标题栏点击聚焦 + 剪贴板粘贴（带读回校验）；
        兜底 2：文件夹路径导航 + 点列表空白 + 全选 + 回车。"""
        quoted = " ".join('"%s"' % f for f in files)
        # ----首选：消息直写通道----
        if win32gui is not None:
            if self._select_files_by_message(hwnd, quoted):
                self.log("    已通过消息通道提交文件选择")
                return True
            if not self._dialog_open(hwnd):
                return True
        # ----兜底 1：剪贴板 + 键盘（带校验重试）----
        self._dlg_focus_click(hwnd)
        self._force_foreground_hwnd(hwnd)
        ed_v, _btn_v = self._pick_dialog_controls(hwnd, timeout=2)

        attempts = [("直接粘贴", None), ("点击文件名框", (0.30, 58)), ("Alt+N", None)]
        for mode, arg in attempts:
            if not self._dialog_open(hwnd):
                return True
            self._force_foreground_hwnd(hwnd)
            if mode == "点击文件名框":
                try:
                    r = win32gui.GetWindowRect(hwnd)
                    pyautogui.click(r[0] + int((r[2] - r[0]) * arg[0]), r[3] - arg[1])
                    time.sleep(0.4)
                except Exception:
                    pass
            elif mode == "Alt+N":
                try:
                    pyautogui.hotkey("alt", "n")
                    time.sleep(0.35)
                except Exception:
                    pass
            if pyperclip is None:
                pyautogui.write(folder, interval=0.02)
                time.sleep(0.3)
                pyautogui.press("enter")
                if self._wait_dialog_closed(hwnd, 5):
                    return True
                continue
            if not self._clipboard_set(quoted):
                self.log("    [警告] 剪贴板写入失败，重试…")
                continue
            pyautogui.hotkey("ctrl", "a")
            time.sleep(0.12)
            pyautogui.hotkey("ctrl", "v")
            time.sleep(0.7)
            ok = self._verify_filename_box(ed_v, quoted)
            if ok:
                self.log(f"    文件名框校验：成功（{mode}）")
                for _j in range(2):
                    pyautogui.press("enter")
                    if self._wait_dialog_closed(hwnd, 4):
                        return True
            else:
                self.log(f"    文件名框校验：未通过（{mode}）")
        # 最后手段：导航文件夹方式
        if self._dialog_open(hwnd):
            self.log("    切换备用方式：导航文件夹 + 全选…")
            self._force_foreground_hwnd(hwnd)
            if pyperclip is not None and self._clipboard_set(folder):
                try:
                    pyautogui.hotkey("ctrl", "a")
                    time.sleep(0.12)
                    pyautogui.hotkey("ctrl", "v")
                    time.sleep(0.7)
                    pyautogui.press("enter")
                    time.sleep(1.6)
                    r = win32gui.GetWindowRect(hwnd)
                    pyautogui.click(r[0] + int((r[2] - r[0]) * 0.30),
                                    r[1] + int((r[3] - r[1]) * 0.72))
                    time.sleep(0.4)
                    pyautogui.hotkey("ctrl", "a")
                    time.sleep(0.4)
                    pyautogui.press("enter")
                    if self._wait_dialog_closed(hwnd, 6):
                        return True
                    pyautogui.press("enter")
                    if self._wait_dialog_closed(hwnd, 3):
                        return True
                except Exception:
                    pass
        return False

    def _chunk_files_by_length(self, files, limit=240):
        """按“文件名框长度上限”把文件分批（每批带引号全路径串 ≤ limit 字符）。
        实测「选择图片」对话框文件名框会截断超长文本（约 259 字符），故必须分批。"""
        chunks, cur, cur_len = [], [], 0
        for f in files:
            q = len(f) + 2                 # 两侧引号
            need = q + (1 if cur else 0)   # 分隔空格
            if cur and cur_len + need > limit:
                chunks.append(cur)
                cur, cur_len = [], 0
                need = q
            cur.append(f)
            cur_len += need
        if cur:
            chunks.append(cur)
        return chunks

    def _open_upload_dialog_cdp(self):
        """受信任点击「上传图片」并等待对话框（带重试+置前）；返回对话框 hwnd 或 None。"""
        if self.cdp is None or win32gui is None:
            return None
        for attempt in (1, 2, 3):
            self._app_foreground_soft()
            if not self.cdp.click_upload_button_trusted():
                time.sleep(1.0)
                continue
            dlg = self._find_select_dialog(timeout=12)
            if dlg:
                self._dlg_set_topmost(dlg)
                return dlg
            self.log(f"    [重试] 对话框未出现（第 {attempt} 次）")
            time.sleep(1.5)
        return None

    def _open_upload_dialog_mouse(self):
        """鼠标兜底：真实点击「上传图片」按钮（窗口坐标 + 视口坐标），带重试与原因日志。"""
        if self.cdp is None or win32gui is None or gw is None:
            return None
        for attempt in (1, 2):
            try:
                rect = self.cdp.get_upload_button_rect()
                if not rect:
                    self.log("    [鼠标重试] 未找到上传按钮")
                    return None
                wins = gw.getAllWindows()
                win = pick_best_window(wins, self.cfg["window"].get("title_keyword", "洗衣管家"))
                if win is None:
                    self.log("    [鼠标重试] 未找到洗衣管家窗口")
                    return None
                try:
                    win.activate()
                except Exception:
                    pass
                time.sleep(0.4)
                sx = int(win.left) + int(rect["x"])
                sy = int(win.top) + int(rect["y"])
                pyautogui.click(sx, sy)
            except Exception as e:
                self.log(f"    [鼠标重试] 异常：{e!r}")
                return None
            dlg = self._find_select_dialog(timeout=10)
            if dlg:
                self._dlg_set_topmost(dlg)
                return dlg
            self.log(f"    [鼠标重试] 对话框未出现（第 {attempt} 次）")
            time.sleep(1.5)
        return None

    def _run_one_cdp(self, task):
        """通过调试协议执行一条记录（精准模式）；演练时执行完整“预检查”。"""
        cdp = self.cdp
        dry = self._dry()
        skip_if_exists = bool(self.cfg["options"].get("skip_if_has_photos", True))
        page = cdp.page()
        self.log(f"    当前页面：{PAGE_NAMES.get(page, '未知') if page else '未知'}")
        if page != "photo" or not cdp.has_photo_input():
            if not cdp.goto_page("photo"):
                raise StepError("无法通过调试接口进入【拍照页】")
            self.log("    已进入【拍照页】")
        r = cdp.enter_barcode(task.barcode)
        if not r.get("ok"):
            raise StepError(f"调试接口输入条码失败：{r.get('why', r)}")
        self.log(f"    已输入条码 {task.barcode} 并触发查询")
        if not cdp.wait_order_loaded(timeout=10):
            raise StepError("等待订单加载超时（未出现上传区域）——请确认条码正确或软件状态")
        cdp.wait_order_info(task.barcode, timeout=6)      # 等新订单数据刷新到位
        time.sleep(0.6)
        existing = cdp.stable_uploaded_photo_count(timeout=6)   # 两次读数一致才采用
        self.log(f"    订单已加载（已有 {existing} 张照片）")
        files = self._collect_folder_files(task.folder)
        if dry:
            if not files:
                return True, "检查：本地文件夹内没有图片（正式执行将跳过）"
            if existing > 0 and skip_if_exists:
                if existing >= len(files):
                    return True, f"检查：订单已有 {existing} 张照片（正式执行将跳过）"
                return True, (f"检查：订单已有 {existing} 张、本地 {len(files)} 张"
                              "（疑似未传完；正式执行仍会跳过，请人工核对）")
            return True, f"检查：将上传 {len(files)} 张（当前订单已有 {existing} 张）"
        if not files:
            raise StepError(f"文件夹内没有图片文件：{task.folder}")
        if existing > 0 and skip_if_exists:
            if existing >= len(files):
                return True, f"跳过：订单已有 {existing} 张照片，未重复上传"
            return True, (f"跳过：订单已有 {existing} 张、本地 {len(files)} 张，"
                          "疑似未传完未行动，请人工核对")
        # —— 打开「选择图片」文件对话框 → 选择文件 → 等待上传 ——
        # 注 1：文件名框有长度上限（实测约 259 字符），超长会截断，故按长度分批；
        # 注 2：上传阶段自动把洗衣管家置前+置顶（避免软件在后台时对话框交互失灵），结束后还原。
        chunks = self._chunk_files_by_length(files)
        use_topmost = bool((self.cfg.get("cdp", {}) or {}).get("force_topmost", True))
        if use_topmost:
            self._app_foreground_soft()
            if self._app_set_topmost(True):
                self.log("    已临时把洗衣管家置于最顶层（上传阶段）")
        try:
            done_cnt = 0
            for ci, chunk in enumerate(chunks, 1):
                if len(chunks) > 1:
                    self.log(f"    第 {ci}/{len(chunks)} 批（{len(chunk)} 张）：正在打开文件选择对话框…")
                else:
                    self.log("    正在打开文件选择对话框…")
                dlg = self._open_upload_dialog_cdp()
                if not dlg:
                    self.log("    受信任点击未出现对话框，改用鼠标点击重试…")
                    dlg = self._open_upload_dialog_mouse()
                if not dlg:
                    raise StepError(f"未能打开「选择图片」文件对话框（已传 {done_cnt}/{len(files)} 张）")
                self.log("    文件对话框已打开，正在选择文件…")
                if not self._select_files_in_dialog(dlg, chunk, task.folder):
                    # 整轮回退：关闭后重开一次再试
                    try:
                        win32gui.PostMessage(dlg, win32con.WM_CLOSE, 0, 0)
                    except Exception:
                        pass
                    if not self._wait_dialog_closed(dlg, 5):
                        try:
                            win32gui.PostMessage(dlg, win32con.WM_CLOSE, 0, 0)
                        except Exception:
                            pass
                        if not self._wait_dialog_closed(dlg, 4):
                            raise StepError("文件对话框无法关闭（请手动关闭后重试），本条停止")
                    self.log("    对话框操作未成功，关闭后重开重试一次…")
                    dlg = self._open_upload_dialog_cdp()
                    if not dlg:
                        dlg = self._open_upload_dialog_mouse()
                    if not dlg or not self._select_files_in_dialog(dlg, chunk, task.folder):
                        try:
                            win32gui.PostMessage(dlg, win32con.WM_CLOSE, 0, 0)
                        except Exception:
                            pass
                        raise StepError(f"文件对话框未能完成选择（已传 {done_cnt}/{len(files)} 张）")
                self.log("    文件已提交，等待上传完成…")
                ok2, info2 = cdp.wait_upload_complete(existing + done_cnt, len(chunk), timeout=90)
                if not ok2:
                    raise StepError(f"上传未完成：{info2}（已完成 {done_cnt}/{len(files)} 张，请人工核对）")
                done_cnt += len(chunk)
                self.log(f"    本批完成：{info2}")
                time.sleep(1.2)
            return True, f"完成（精准模式，{len(files)} 张）"
        finally:
            if use_topmost:
                if self._app_set_topmost(False):
                    self.log("    已还原洗衣管家窗口层级")

    # ---------------- 批量执行 ----------------
    def run_batch(self, tasks, on_row_start=None, on_row_done=None, only_first=False):
        """
        批量执行。on_row_start(task) / on_row_done(task, success, message) 可选回调。
        返回统计 dict。
        """
        stats = {"total": 0, "ok": 0, "fail": 0, "skip": 0}
        todo = [t for t in tasks if t.status == "待执行"]
        if only_first:
            todo = todo[:1]
        stats["total"] = len(todo)
        if self._dry():
            self.log("===== 演练模式：不点击 / 不输入，不会真实执行 =====")
        self.log(f"—— 开始执行，共 {len(todo)} 条 ——")
        # 优先尝试「调试端口精准模式」
        self.init_cdp()
        if self.cdp is not None:
            try:
                self.prepare_batch_cdp()
            except Exception as e:
                self.log(f"[警告] 准备阶段（调试模式）：{e}")
        else:
            try:
                self.activate_window()
                self.prepare_batch()
            except StepError as e:
                self.log(f"[警告] 准备阶段：{e}")
        for i, task in enumerate(todo, 1):
            if self.ctrl.stopped:
                self.log("—— 已停止（剩余任务未执行）——")
                break
            self.ctrl.wait_if_paused()
            if task.status == "跳过":
                stats["skip"] += 1
                continue
            self.log(f"[{i}/{len(todo)}] 条码 {task.barcode} 开始…")
            if on_row_start:
                on_row_start(task)
            success, msg = self.run_one(task)
            task.status = "成功" if success else "失败"
            if self._dry():
                msg = "[演练] " + msg
            task.note = msg
            if success:
                stats["ok"] += 1
                self.log(f"[{i}/{len(todo)}] 条码 {task.barcode} ✔ {msg}")
            else:
                stats["fail"] += 1
                self.log(f"[{i}/{len(todo)}] 条码 {task.barcode} ✘ {msg}")
                if self.cfg["options"].get("stop_on_error", False):
                    if on_row_done:
                        on_row_done(task, success, msg)
                    self.log("—— 已按“出错即暂停”设置中止，可修正后继续 ——")
                    break
            if on_row_done:
                on_row_done(task, success, msg)
            self.sleep(self._tf("between_rows", 1.0))
        if self.cdp is not None:
            try:
                self.cdp.close()
            except Exception:
                pass
            self.cdp = None
        self.log(f"—— 执行结束：成功 {stats['ok']}，失败 {stats['fail']}，跳过 {stats['skip']}，合计 {stats['total']} ——")
        return stats
