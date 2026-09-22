# -*- coding: utf-8 -*-
"""
updater.py - 在线更新（从 GitHub 仓库获取新版本）
====================================================
职责：
  · check_latest()  —— 查询 GitHub Releases 最新版本，与本机版本比较；
  · download()      —— 下载安装包（自动多源回退：GitHub 直连 → 加速镜像）。

说明：
  · 部分网络环境（如国内直连 GitHub）下载很慢或不通；直连失败或过慢时，
    自动切换 config.json 中 update.mirrors 列出的镜像源（第三方加速，可增删或清空）；
  · 仅使用标准库（urllib），无第三方依赖。
"""
import json
import os
import re
import tempfile
import time
import urllib.request

GITHUB_REPO = "Jace-Hao/xiyi-photo-upload"
RELEASES_PAGE = "https://github.com/{repo}/releases"
API_LATEST = "https://api.github.com/repos/{repo}/releases/latest"
USER_AGENT = "Xiyiguanjia-Uploader-Updater"

# 直连过慢/失败时使用的镜像前缀（留空列表 = 只用直连；可在 config.json 的 update.mirrors 调整）
DEFAULT_MIRRORS = [
    "https://gh-proxy.com/",
    "https://ghfast.top/",
    "https://ghproxy.net/",
]

# 下载健康检查：(已耗时秒数, 最低平均速度 字节/秒)；低于阈值则放弃当前源、切换下一个
_SPEED_CHECKS = ((15, 128 * 1024), (60, 100 * 1024))


def ver_tuple(ver):
    """"v1.7" / "1.7.2" → (1, 7, 0) 之类三元组，便于比较。"""
    nums = [int(x) for x in re.findall(r"\d+", str(ver or ""))]
    while len(nums) < 3:
        nums.append(0)
    return tuple(nums[:3])


def check_latest(current_version, repo=GITHUB_REPO, timeout=8):
    """查询最新 Release。

    返回 dict：found / latest / current / name / notes / html_url / asset_url / asset_name / asset_size。
    网络或解析问题会抛出异常，由调用方兜底提示。
    """
    req = urllib.request.Request(
        API_LATEST.format(repo=repo),
        headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    tag = str(data.get("tag_name") or "")
    found = ver_tuple(tag) > ver_tuple(current_version)

    asset_url, asset_name, asset_size = None, None, None
    assets = data.get("assets") or []
    for a in assets:                       # 优先取“Setup”安装包附件
        nm = str(a.get("name") or "")
        if nm.lower().endswith(".exe") and "setup" in nm.lower():
            asset_url, asset_name = a.get("browser_download_url"), nm
            asset_size = int(a.get("size") or 0) or None
            break
    if asset_url is None:                  # 兜底：任意 exe 附件
        for a in assets:
            nm = str(a.get("name") or "")
            if nm.lower().endswith(".exe"):
                asset_url, asset_name = a.get("browser_download_url"), nm
                asset_size = int(a.get("size") or 0) or None
                break

    return {
        "found": bool(found),
        "latest": tag.lstrip("vV") or "?",
        "current": str(current_version),
        "name": str(data.get("name") or tag),
        "notes": str(data.get("body") or "")[:800],
        "html_url": str(data.get("html_url") or RELEASES_PAGE.format(repo=repo)),
        "asset_url": asset_url,
        "asset_name": asset_name,
        "asset_size": asset_size,
    }


def build_sources(asset_url, mirrors=None):
    """构造下载源列表：直连优先，其后是各加速镜像。"""
    srcs = [asset_url]
    for m in (DEFAULT_MIRRORS if mirrors is None else mirrors):
        m = str(m or "").strip()
        if m:
            srcs.append(m.rstrip("/") + "/" + asset_url)
    return srcs


def download(asset_url, dest_dir=None, filename=None, progress_cb=None,
             mirrors=None, expected_size=None, source_cb=None, timeout=15):
    """多源下载更新包，返回本地保存路径。

    progress_cb(done_bytes, total_bytes)  可为 None（total 未知时为 0）；
    source_cb(index, total_sources, url)  切换下载源时回调，可为 None；
    直连失败或过慢时自动按 mirrors 顺序切换备用源。
    """
    if not asset_url:
        raise ValueError("该版本没有可下载的安装包附件")
    if dest_dir is None:
        dest_dir = tempfile.gettempdir()
    if not filename:
        filename = os.path.basename(asset_url.split("?")[0]) or "update_setup.exe"
    dest = os.path.join(dest_dir, filename)
    sources = build_sources(asset_url, mirrors)

    errors = []
    for idx, src in enumerate(sources, 1):
        try:
            if source_cb:
                try:
                    source_cb(idx, len(sources), src)
                except Exception:
                    pass
            _download_one(src, dest, progress_cb, expected_size, timeout)
            return dest
        except Exception as e:
            host = src.split("/")[2] if "//" in src else src
            errors.append("%s: %s" % (host, e))
    raise RuntimeError("所有下载源均失败：" + "；".join(errors))


def _download_one(url, dest, progress_cb, expected_size, timeout):
    """从单个源下载到 dest（失败自动清理 .part 文件并抛出异常）。"""
    part = dest + ".part"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            try:
                total = int(resp.headers.get("Content-Length") or 0)
            except Exception:
                total = 0
            t0 = time.time()
            done = 0
            check_i = 0
            with open(part, "wb") as f:
                while True:
                    chunk = resp.read(256 * 1024)
                    if not chunk:
                        break
                    f.write(chunk)
                    done += len(chunk)
                    elapsed = time.time() - t0
                    while check_i < len(_SPEED_CHECKS) and elapsed >= _SPEED_CHECKS[check_i][0]:
                        need = _SPEED_CHECKS[check_i][1]
                        avg = done / max(elapsed, 0.1)
                        if avg < need:
                            raise RuntimeError("速度过慢（约 %.0f KB/s）" % (avg / 1024))
                        check_i += 1
                    if progress_cb:
                        try:
                            progress_cb(done, total)
                        except Exception:
                            pass
        if expected_size and os.path.getsize(part) != int(expected_size):
            raise RuntimeError("文件大小不符（%d != %d）" % (os.path.getsize(part), int(expected_size)))
        os.replace(part, dest)
    except Exception:
        try:
            if os.path.exists(part):
                os.remove(part)
        except Exception:
            pass
        raise
