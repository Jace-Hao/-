# -*- coding: utf-8 -*-
"""
ui_theme.py - 「洗衣管家 · 照片批量上传助手」统一视觉主题
==========================================================
把界面的颜色 / 字体 / 控件样式集中在这里，方便后续统一调整：

  · 想换主色：改 PALETTE 里的 accent / accent_soft 等几个值即可；
  · 想换字体：改 FONTS 里对应项；
  · 控件皮肤统一由 apply_theme() 注入（基于 ttk 'clam' 主题铺设）。

设计方向：清朗 · 工业实用（锐利直角、扁平克制、品牌绿点缀）。
色板取自项目 Logo 的品牌绿，中性色为冷调灰绿。
"""
from tkinter import ttk

# ------------------------------------------------------------------ 色板
PALETTE = {
    # 基层
    "bg":            "#F3F5F1",   # 窗口底色
    "surface":       "#FFFFFF",   # 卡片表面
    "surface_soft":  "#F8FAF5",   # 次级表面
    "log_bg":        "#FAFCF8",   # 日志底色
    # 线与文字
    "line":          "#E1E5DB",   # 卡片描边
    "line_soft":     "#EBEEE6",   # 分隔线
    "ink":           "#1F241D",   # 主文字
    "ink_2":         "#46503F",   # 次级文字
    "muted":         "#6F7768",   # 弱化文字
    # 品牌绿（取自 Logo）
    "accent":        "#2F7D1F",
    "accent_hover":  "#296E1B",
    "accent_press":  "#1F5714",
    "accent_soft":   "#EAF3E1",
    "accent_ink":    "#275E15",
    "accent_line":   "#C9DEBB",
    # 语义色
    "danger":        "#B3261E",
    "danger_soft":   "#FBEDEC",
    "danger_line":   "#E8C8C5",
    "warn":          "#8A6A1A",
    # 小徽章
    "chip_bg":       "#EDF1E7",
    "chip_fg":       "#4D5548",
    # 表格
    "stripe":        "#F6F9F3",   # 斑马纹
    "tree_sel":      "#DCEDCC",   # 选中行
    "tree_head_bg":  "#F1F4EC",
    "tree_head_fg":  "#5A6255",
    # 滚动条
    "scroll_trough": "#F1F4EC",
    "scroll_thumb":  "#C6CFC0",
}

# 表格行 / 日志行的状态色（前景色）
STATUS_COLORS = {
    "成功":   "#1A7F37",
    "失败":   "#C62828",
    "跳过":   "#8A9086",
    "进行中": "#1B62B8",
}

# ------------------------------------------------------------------ 字体
FONTS = {
    "ui":    ("Microsoft YaHei UI", 10),
    "small": ("Microsoft YaHei UI", 9),
    "card":  ("Microsoft YaHei UI", 11, "bold"),
    "title": ("Microsoft YaHei UI", 16, "bold"),
    "chip":  ("Microsoft YaHei UI", 9),
    "badge": ("Microsoft YaHei UI", 9, "bold"),
    "log":   ("Consolas", 9),
}


def apply_theme(root):
    """把统一主题应用到 ttk 控件（在创建任何 ttk 控件之前调用一次）。"""
    P = PALETTE
    style = ttk.Style(root)
    try:
        style.theme_use("clam")   # clam 允许完整自定义颜色（vista 不支持）
    except Exception:
        pass

    # 全局缺省字体
    style.configure(".", font=FONTS["ui"])

    # ---- 按钮：主操作（品牌绿实底） ----
    style.configure("Primary.TButton",
                    font=FONTS["card"], foreground="#FFFFFF",
                    background=P["accent"], bordercolor=P["accent"],
                    lightcolor=P["accent"], darkcolor=P["accent"],
                    focuscolor=P["accent"], relief="flat", padding=(16, 6))
    style.map("Primary.TButton",
              background=[("pressed", P["accent_press"]), ("active", P["accent_hover"]),
                          ("disabled", "#C9D4C1")],
              foreground=[("disabled", "#F2F5EF")],
              bordercolor=[("pressed", P["accent_press"]), ("active", P["accent_hover"]),
                           ("disabled", "#C9D4C1")])

    # ---- 按钮：次操作（白底描边） ----
    style.configure("Secondary.TButton",
                    font=FONTS["ui"], foreground=P["ink_2"],
                    background=P["surface"], bordercolor="#CBD3C2",
                    lightcolor=P["surface"], darkcolor=P["surface"],
                    focuscolor=P["surface"], relief="flat", padding=(13, 5))
    style.map("Secondary.TButton",
              background=[("pressed", "#E3EADB"), ("active", P["accent_soft"]),
                          ("disabled", "#F4F6F1")],
              foreground=[("disabled", "#A2AA9C"), ("active", P["accent_ink"])],
              bordercolor=[("disabled", P["line"]), ("active", P["accent_line"])])

    # ---- 按钮：导入（绿色描边、浅底） ----
    style.configure("Accent.TButton",
                    font=FONTS["ui"], foreground=P["accent_ink"],
                    background=P["accent_soft"], bordercolor=P["accent_line"],
                    lightcolor=P["accent_soft"], darkcolor=P["accent_soft"],
                    focuscolor=P["accent_soft"], relief="flat", padding=(12, 5))
    style.map("Accent.TButton",
              background=[("pressed", "#D4E7C2"), ("active", "#DFEDD0"),
                          ("disabled", "#F4F6F1")],
              bordercolor=[("active", P["accent"]), ("pressed", P["accent"])])

    # ---- 按钮：危险（停止） ----
    style.configure("Danger.TButton",
                    font=FONTS["ui"], foreground=P["danger"],
                    background=P["surface"], bordercolor=P["danger_line"],
                    lightcolor=P["surface"], darkcolor=P["surface"],
                    focuscolor=P["surface"], relief="flat", padding=(13, 5))
    style.map("Danger.TButton",
              background=[("pressed", "#F6DEDC"), ("active", P["danger_soft"]),
                          ("disabled", "#F4F6F1")],
              foreground=[("disabled", "#C3A9A6")],
              bordercolor=[("disabled", P["line"])])

    # ---- 按钮：幽灵（底部工具行，无边框文字按钮） ----
    style.configure("Ghost.TButton",
                    font=FONTS["small"], foreground=P["muted"],
                    background=P["bg"], bordercolor=P["bg"],
                    lightcolor=P["bg"], darkcolor=P["bg"],
                    focuscolor=P["bg"], relief="flat", padding=(8, 3))
    style.map("Ghost.TButton",
              background=[("active", "#E6EBDF"), ("pressed", "#DDE4D5")],
              bordercolor=[("active", "#E6EBDF"), ("pressed", "#DDE4D5")],
              foreground=[("active", P["ink_2"])])

    # ---- 按钮：浅绿底上的文字按钮（更新提示条用） ----
    style.configure("SoftGhost.TButton",
                    font=FONTS["small"], foreground=P["accent_ink"],
                    background=P["accent_soft"], bordercolor=P["accent_soft"],
                    lightcolor=P["accent_soft"], darkcolor=P["accent_soft"],
                    focuscolor=P["accent_soft"], relief="flat", padding=(10, 4))
    style.map("SoftGhost.TButton",
              background=[("active", "#DCEECC"), ("pressed", "#D2E8BE")],
              bordercolor=[("active", "#DCEECC"), ("pressed", "#D2E8BE")])

    # ---- 复选框 ----
    style.configure("Card.TCheckbutton",
                    font=FONTS["ui"], foreground=P["ink_2"],
                    background=P["surface"], focuscolor=P["surface"])
    style.map("Card.TCheckbutton",
              background=[("active", P["surface"]), ("selected", P["surface"])],
              foreground=[("disabled", "#A2AA9C")])

    # ---- 表格 ----
    style.configure("Treeview",
                    font=FONTS["ui"], rowheight=26,
                    background=P["surface"], fieldbackground=P["surface"],
                    foreground=P["ink"], borderwidth=0)
    style.map("Treeview",
              background=[("selected", P["tree_sel"])],
              foreground=[("selected", P["ink"])])
    style.configure("Treeview.Heading",
                    font=FONTS["small"], background=P["tree_head_bg"],
                    foreground=P["tree_head_fg"], relief="flat", padding=(8, 6))
    style.map("Treeview.Heading",
              background=[("active", "#E9EEE2")], relief=[("active", "flat")])

    # ---- 滚动条 ----
    style.configure("Vertical.TScrollbar",
                    background=P["scroll_thumb"], troughcolor=P["scroll_trough"],
                    bordercolor=P["scroll_trough"], arrowcolor="#717A6A",
                    relief="flat", arrowsize=12)
    style.map("Vertical.TScrollbar",
              background=[("active", "#B3BEA9"), ("pressed", "#A7B29D")])

    # ---- 进度条 ----
    style.configure("Brand.Horizontal.TProgressbar",
                    troughcolor="#E2E9DE", background="#3F9A28",
                    bordercolor="#C9D4C1", lightcolor="#3F9A28",
                    darkcolor="#3F9A28")
    return style
