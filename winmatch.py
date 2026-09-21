# -*- coding: utf-8 -*-
"""
winmatch.py — 窗口匹配模块
================================================================
把「洗衣管家」应用主窗口从一堆窗口标题里稳稳地挑出来：
  - 先排除本工具自身窗口（照片批量上传助手 / 坐标校准 / 程序说明等）
    以及 cmd / python / 编辑器等干扰窗口；
  - 再按打分挑选：用户设定的关键词命中最优先，
    其次「有效期」等应用特征字样，最后是「洗衣 / 管家」类泛化词。

打分示例（关键词=洗衣管家）：
  '洗衣管家'                                   -> 265  ✓ 选中
  '洗衣管家 · 照片批量上传助手 — 程序说明 - 夸克' -> 排除（上传助手/程序说明）
  '星期衣精致洗衣衣物照片系统'                    -> 10
"""
import re

EXCLUDE_KEYWORDS = (
    "照片批量上传助手", "上传助手", "坐标校准", "程序说明",
    "config", "settings", ".py", ".bat",
    "python", "cmd", "命令提示符", "powershell", "windows terminal",
    "visual studio", "vscode", "记事本", "notepad",
)

# 常见浏览器/文档查看器「后缀」特征（避免把打开说明文档的浏览器窗口当成应用）
BROWSER_HINTS = ("夸克", "quark", "chrome", "edge", "firefox", "浏览器", "browser")


def score_title(title: str, keyword: str = "") -> int:
    """给窗口标题打分；<=0 表示排除。"""
    if not title or not title.strip():
        return -1
    t = title.strip()
    low = t.lower()
    for bad in EXCLUDE_KEYWORDS:
        if bad.lower() in low:
            return -1
    score = 0
    kw = (keyword or "").strip()
    if kw:
        if t == kw:
            score += 150            # 完全相等，最优先
        elif t.startswith(kw):
            score += 60             # 以关键词开头
        elif kw in t:
            score += 40             # 包含关键词
    if "有效期" in t:
        score += 50                 # 洗衣管家标题中常见的“ (有效期: ...)”
    if "洗衣" in t:
        score += 10
    if "洗护" in t:
        score += 10
    if "管家" in t:
        score += 5
    # 浏览器后缀降权（例如某浏览器把页面标题拼进窗口标题）
    for bh in BROWSER_HINTS:
        if bh in low:
            score -= 60
            break
    return score


def pick_best_title(titles, keyword: str = ""):
    """从标题列表里挑最匹配的；没有合适返回 None。"""
    best, best_s = None, 0
    for t in titles:
        s = score_title(t, keyword)
        if s > best_s:
            best, best_s = t, s
    return best


def pick_best_window(windows, keyword: str = ""):
    """从 window 对象列表中直接选出最匹配的窗口对象。
    避免 getWindowsWithTitle 的“包含关系”陷阱：
    （例如“洗衣管家·照片批量上传助手”也会包含“洗衣管家”而排到前面）"""
    best, best_s = None, 0
    for w in windows:
        try:
            t = w.title or ""
        except Exception:
            continue
        s = score_title(t, keyword)
        if s > best_s:
            best, best_s = w, s
    return best
