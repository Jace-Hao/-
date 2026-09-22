# -*- coding: utf-8 -*-
"""
config_io.py — 配置读写模块
--------------------------------
负责读取/保存 config.json（包含窗口标题、坐标、锚点、时间参数等）。
首次运行若不存在配置文件，会自动生成默认配置。
"""
import json
import os
import copy

CONFIG_FILENAME = "config.json"


def default_config():
    """默认配置。所有 坐标/锚点 默认为空，需要通过【坐标校准】设置。"""
    return {
        "window": {
            "title_keyword": "洗衣管家",        # 窗口标题关键字/部分标题
            "title_full_hint": "星期衣精致洗衣",  # 备注用（完整标题示例）
            "activate_timeout": 5.0,            # 激活窗口最长等待（秒）
            "coords_mode": "window"             # window=窗口相对坐标；absolute=屏幕绝对坐标
        },
        "cdp": {
            # 调试端口精准模式：洗衣管家以 --remote-debugging-port=9222 启动时可用
            # 每次运行前会自动确保调试模式：未启动→自动带参数启动；已启动但无参数→询问后重启
            "enabled": True,
            "auto_start": True,            # 每次运行前自动保障调试模式
            "port": 9222,
            "app_exe": "D:\\Blending_Release-6.1.17\\xygjwinapp.exe",
            "app_dir": "D:\\Blending_Release-6.1.17",
            "start_wait": 30,               # 启动后等待调试端口就绪的最长秒数
            "force_restart": True,          # 正常关闭失败时自动强制结束进程（再重启）
            "force_topmost": True,          # 上传阶段自动把洗衣管家置前+置顶（结束后还原）
            "routes": {
                "home": "#/",
                "more": "#/more",
                "photo": "#/clothManage/photograph?moduleid=3&templetid=3007&layoutType=1"
            }
        },
        "points": {
            # 各操作点的坐标。window 模式下为相对窗口左上角的 (dx, dy)
            "barcode_input": None,      # 条码输入框（点击进入编辑）
            "more_button": None,        # “更多”按钮（左侧导航）
            "photo_button": None,       # “拍照”按钮（更多功能页里）
            "upload_button": None,      # “上传图片”按钮（拍照页右下角）
            "more_card": None,          # （可选）首页右侧的“更多”卡片
            "dialog_filename": None     # （可选）文件对话框中的“文件名”输入框
        },
        "anchors": {
            # 可选：图像锚点（.png）。勾选“使用图像锚点”后优先用图像匹配找按钮；找不到再退回坐标。
            "enabled": False,
            "upload_button": "",
            "photo_button": "",
            "more_button": "",
            "more_card": "",
            "barcode_input": "",
            "dialog_filename": "",
            # 页面标志（用于识别当前页面，避免多余点击）
            "page_home": "",
            "page_home_b": "",
            "page_more": "",
            "page_more_b": "",
            "page_photo": "",
            "page_photo_b": ""
        },
        "timing": {
            "after_activate": 0.6,      # 激活窗口后等待
            "after_click": 0.6,         # 普通点击后等待
            "after_enter": 1.5,         # 输入条码回车后等待（等页面刷新）
            "after_upload_click": 1.2,  # 点击“上传图片”后等待（等对话框弹出）
            "dialog_timeout": 8.0,      # 等待文件对话框出现的最长时间
            "after_path_enter": 1.0,    # 输入路径回车后等待（等进入文件夹）
            "after_select_all": 1.0,    # 全选并回车后等待（等上传/关闭）
            "between_rows": 1.0,        # 两条记录之间间隔
            "step_retry_cycles": 3,     # 页面状态恢复最大循环次数（找“上传图片”）
            "speed_factor": 1.0         # 全局速度倍率（>1 更慢更稳）
        },
        "dialog": {
            "title_keywords": ["选择图片", "打开", "Open"],
            "use_standard_dialog": True,  # 标准 Windows 打开对话框流程：粘贴路径→回车→Ctrl+A→回车
            "select_all_hotkey": "ctrl+a"
        },
        "options": {
            "check_folder_exists": True,   # 导入时检查组文件夹是否存在
            "stop_on_error": False,        # True=某条失败即暂停；False=记录失败继续
            "screenshot_on_error": True,   # 失败时自动截图保存便于排查
            "export_xlsx": True,           # 结果同时导出 xlsx（需要 openpyxl）
            "warmup_nav": True,            # 坐标模式：批次开始前自动“更多→拍照”预热导航
            "dry_run": False,              # 演练模式：只移动鼠标不点击（界面上也有开关）
            "strict_dialog_check": True,   # 严格校验文件对话框（未出现/未关闭则判失败，避免假成功）
            "skip_if_has_photos": True,   # 精准模式：订单已有照片时跳过，防止重复上传（正式执行时）
            "check_update_on_start": True  # 启动时自动检查 GitHub 新版本（界面底部也可手动【检查更新】）
        },
        "update": {
            # 在线更新下载源：直连 GitHub 过慢时自动切换的国内加速线路（可增删；留空列表 = 只用直连）
            "mirrors": [
                "https://gh-proxy.com/",
                "https://ghfast.top/",
                "https://gh.ddlc.top/",
                "https://gh.idayer.com/",
                "https://gh.xxooo.cf/",
                "https://gh.catmak.name/",
                "https://ghproxy.net/"
            ]
        }
    }


def _merge(base, override):
    """把 override 合并进 base（深合并，override 优先）。"""
    out = copy.deepcopy(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config(base_dir):
    path = os.path.join(base_dir, CONFIG_FILENAME)
    if not os.path.exists(path):
        cfg = default_config()
        save_config(base_dir, cfg)
        return cfg
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    return _merge(default_config(), raw)


def save_config(base_dir, cfg):
    path = os.path.join(base_dir, CONFIG_FILENAME)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    return path
