# -*- coding: utf-8 -*-
"""
updater.py - 在线更新（从 GitHub 仓库获取新版本）
====================================================
职责：
  · check_latest()  —— 查询 GitHub Releases 最新版本，与本机版本比较；
  · download()      —— 下载安装包（自动多源回退：GitHub 直连 → 国内加速线路；
                       支持换源断点续传与完整性校验）。

说明：
  · 部分网络环境（如国内直连 GitHub）下载很慢或不通；直连过慢时会在几秒内
    自动切换 config.json 中 update.mirrors 列出的国内加速线路（可增删或清空）；
  · 切换线路不重头下载：已下载部分通过 HTTP Range 自动续传，多线路接力完成；
    若全部线路都慢，最后会放宽限制再兜底尝试一轮，尽量完成下载；
  · 若 Release 附带 .sha256 校验附件（或说明文本中给出校验值），下载完成后自动校验，
    校验不通过则换源重试；
  · 仅使用标准库（urllib / hashlib），无第三方依赖。
"""
import hashlib
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

# 直连过慢/失败时使用的国内加速线路（留空列表 = 只用直连；可在 config.json 的 update.mirrors 调整）
DEFAULT_MIRRORS = [
    "https://gh-proxy.com/",
    "https://ghfast.top/",
    "https://gh.ddlc.top/",
    "https://gh.idayer.com/",
    "https://gh.xxooo.cf/",
    "https://gh.catmak.name/",
    "https://ghproxy.net/",
]

# 速度健康检查：(已耗时秒数, 该阶段最低平均速度)；低于阈值即放弃当前源并切换下一个。
# 第一轮用 SPEED_CHECKS（几秒内发现慢源并切换）；全部失败后第二轮用 _RELAXED_CHECKS
# 兜底（允许慢速完成，但仍然终止长时间过慢的连接）。
SPEED_CHECKS = ((6, 64 * 1024), (22, 96 * 1024), (55, 64 * 1024))
_RELAXED_CHECKS = ((90, 12 * 1024),)


def ver_tuple(ver):
    """"v1.7" / "1.7.2" → (1, 7, 0) 之类三元组，便于比较。"""
    nums = [int(x) for x in re.findall(r"\d+", str(ver or ""))]
    while len(nums) < 3:
        nums.append(0)
    return tuple(nums[:3])


def check_latest(current_version, repo=GITHUB_REPO, timeout=8):
    """查询最新 Release。

    返回 dict：found / latest / current / name / notes / html_url /
    asset_url / asset_name / asset_size / sha256_url / sha256。
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

    sha256_url = None                      # 校验附件（如 Xxx-Setup.exe.sha256）
    for a in assets:
        nm = str(a.get("name") or "").lower()
        if nm.endswith(".sha256") or nm.endswith(".sha256.txt"):
            sha256_url = a.get("browser_download_url")
            break
    sha256_notes = None                    # 说明文本里如有校验值也解析出来
    body = str(data.get("body") or "")
    m = re.search(r"(?:sha-?256|校验)[^0-9a-fA-F]{0,16}([0-9a-fA-F]{64})", body, re.IGNORECASE)
    if m:
        sha256_notes = m.group(1).lower()

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
        "sha256_url": sha256_url,
        "sha256": sha256_notes,
    }


def build_sources(asset_url, mirrors=None):
    """构造下载源列表：直连优先，其后按顺序为各国内加速线路。"""
    srcs = [asset_url]
    for m in (DEFAULT_MIRRORS if mirrors is None else mirrors):
        m = str(m or "").strip()
        if m:
            srcs.append(m.rstrip("/") + "/" + asset_url)
    return srcs


def _file_sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _fetch_sha256(url, mirrors=None, timeout=8):
    """尝试取回校验值文本（直连优先，失败换加速线路）；失败返回 None（跳过校验）。"""
    for c in build_sources(url, mirrors):
        try:
            req = urllib.request.Request(c, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                txt = r.read(8192).decode("utf-8", "ignore")
            m = re.search(r"([0-9a-fA-F]{64})", txt)
            if m:
                return m.group(1).lower()
        except Exception:
            continue
    return None


def download(asset_url, dest_dir=None, filename=None, progress_cb=None,
             mirrors=None, expected_size=None, source_cb=None, timeout=10,
             expected_sha256=None, sha256_url=None):
    """多源下载更新包，返回本地保存路径。

    · 直连失败或过慢（几秒内判定）时自动切换国内加速线路；
    · 切换线路不重头下载：保留已下载部分，使用 Range 断点续传；
    · 可选完整性校验：expected_sha256 或 sha256_url（取回失败则跳过校验）。

    progress_cb(done_bytes, total_bytes)  可为 None（total 未知时为 0）；
    source_cb(index, total_sources, url)  切换下载源时回调（仅第一轮），可为 None。
    """
    if not asset_url:
        raise ValueError("该版本没有可下载的安装包附件")
    if dest_dir is None:
        dest_dir = tempfile.gettempdir()
    if not filename:
        filename = os.path.basename(asset_url.split("?")[0]) or "update_setup.exe"
    dest = os.path.join(dest_dir, filename)
    part = dest + ".part"
    sources = build_sources(asset_url, mirrors)

    sha = str(expected_sha256 or "").strip().lower() or None
    if not sha and sha256_url:
        sha = _fetch_sha256(sha256_url, mirrors, timeout=min(timeout, 8))

    errors = []
    for checks, relaxed in ((SPEED_CHECKS, False), (_RELAXED_CHECKS, True)):
        for idx, src in enumerate(sources, 1):
            resume_from = os.path.getsize(part) if os.path.exists(part) else 0
            try:
                if source_cb and not relaxed:
                    try:
                        source_cb(idx, len(sources), src)
                    except Exception:
                        pass
                _download_one(src, part, progress_cb, expected_size, timeout, resume_from, checks)
                if sha:
                    got = _file_sha256(part)
                    if got != sha:
                        try:
                            os.remove(part)
                        except Exception:
                            pass
                        raise RuntimeError("文件完整性校验不通过（sha256 不符）")
                os.replace(part, dest)
                return dest
            except Exception as e:
                host = src.split("/")[2] if "//" in src else src
                errors.append("%s: %s" % (host, e))
    raise RuntimeError("所有下载源均失败：" + "；".join(errors))


def _download_one(url, part, progress_cb, expected_size, timeout, resume_from, checks):
    """从单个源下载到 .part（支持 Range 续传）。

    失败时保留 .part（供下一个源续传）；平均速度低于 checks 阈值时主动中止。
    """
    headers = {"User-Agent": USER_AGENT}
    if resume_from > 0:
        headers["Range"] = "bytes=%d-" % resume_from
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        code = getattr(resp, "status", 200)
        if resume_from > 0 and code != 206:
            resume_from = 0            # 该源不支持断点续传 → 从头下载
        ctype = (resp.headers.get("Content-Type") or "").lower()
        if "text/html" in ctype:
            raise RuntimeError("该源返回的是网页而非安装包")
        try:
            clen = int(resp.headers.get("Content-Length") or 0)
        except Exception:
            clen = 0
        total = (resume_from + clen) if clen else (int(expected_size) if expected_size else 0)
        mode = "ab" if resume_from > 0 else "wb"
        t0 = time.time()
        done = resume_from
        check_i = 0
        with open(part, mode) as f:
            while True:
                chunk = resp.read(256 * 1024)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                elapsed = time.time() - t0
                while check_i < len(checks) and elapsed >= checks[check_i][0]:
                    need = checks[check_i][1]
                    avg = (done - resume_from) / max(elapsed, 0.1)
                    if avg < need:
                        raise RuntimeError("速度过慢（约 %.0f KB/s），切换下一个源" % (avg / 1024.0))
                    check_i += 1
                if progress_cb:
                    try:
                        progress_cb(done, total)
                    except Exception:
                        pass
    size = os.path.getsize(part)
    if expected_size:
        es = int(expected_size)
        if size > es:
            try:
                os.remove(part)
            except Exception:
                pass
            raise RuntimeError("文件超过预期大小")
        if size < es:
            raise RuntimeError("下载不完整（%d/%d 字节），已保留进度" % (size, es))
