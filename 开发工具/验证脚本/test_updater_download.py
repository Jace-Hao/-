# -*- coding: utf-8 -*-
"""test_updater_download.py — 在线更新「多源下载」联网测试。

覆盖：慢源自动切换 / 换源断点续传 / 哈希一致性 / 校验值取回降级。
用法（在任意目录）：
    py -3.14 test_updater_download.py
按需修改下方 ASSET / LOCAL（对照的本地安装包）后运行。
注意：会消耗约 200MB 流量（数分钟内多次下载 56MB 安装包）。
"""
import hashlib
import os
import sys
import time
import urllib.request

sys.path.insert(0, r'E:\软件开发\洗衣管家上传助手')
import updater  # noqa: E402

ASSET = 'https://github.com/Jace-Hao/xiyi-photo-upload/releases/download/v1.9/Xiyiguanjia-Uploader-Setup-v1.9.exe'
LOCAL = r'E:\软件开发\洗衣管家上传助手\安装包\洗衣管家上传助手_安装包_v1.9.exe'
DEST = os.path.join(os.environ.get('TEMP', r'C:\Windows\Temp'), 'upd_test')
os.makedirs(DEST, exist_ok=True)
OUT = os.path.join(DEST, os.path.basename(ASSET.split('?')[0]))
PART = OUT + '.part'


def sha(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for c in iter(lambda: f.read(1 << 20), b''):
            h.update(c)
    return h.hexdigest()


if not os.path.exists(LOCAL):
    print('请先修改脚本中的 LOCAL 为存在的对照安装包'); sys.exit(1)
local_sha = sha(LOCAL)
local_size = os.path.getsize(LOCAL)
print('local sha256:', local_sha)
print('local size  :', local_size)

for p in (PART, OUT):
    if os.path.exists(p):
        os.remove(p)

print()
print('===== Range 支持探测（续传前提） =====')
for base in ['https://gh-proxy.com/', 'https://gh.ddlc.top/']:
    try:
        req = urllib.request.Request(base + ASSET,
                                     headers={'User-Agent': updater.USER_AGENT, 'Range': 'bytes=100000-'})
        with urllib.request.urlopen(req, timeout=8) as r:
            print('  %-24s status=%s  Content-Length=%s' % (base, r.status, r.headers.get('Content-Length')))
    except Exception as e:
        print('  %-24s FAIL %s' % (base, str(e)[:70]))

_orig_one = updater._download_one


def _wrap_one(url, part, progress_cb, expected_size, timeout, resume_from, checks):
    print('    >> try %s  resume_from=%d' % (url.split('/')[2], resume_from))
    return _orig_one(url, part, progress_cb, expected_size, timeout, resume_from, checks)


updater._download_one = _wrap_one

print()
print('===== T1: 强制早切（3 秒内平均须 >=8MB/s，必然切换）+ 换源续传 =====')
updater.SPEED_CHECKS = ((3, 8 * 1024 * 1024),)
first_prog = [None]


def pc1(done, total):
    if first_prog[0] is None:
        first_prog[0] = done


try:
    t0 = time.time()
    path = updater.download(ASSET, dest_dir=DEST,
                            mirrors=['https://gh-proxy.com/', 'https://gh.ddlc.top/'],
                            expected_size=local_size, progress_cb=pc1)
    print('  [T1] done in %.0fs, first progress %s bytes; sha match: %s'
          % (time.time() - t0, first_prog[0], sha(path) == local_sha))
    os.remove(path)
except Exception as e:
    print('  [T1] FAILED:', str(e)[:200])

print()
print('===== T2: 预置 1MB 断点 -> 单线路续传 =====')
with open(LOCAL, 'rb') as f:
    seed = f.read(1024 * 1024)
with open(PART, 'wb') as f:
    f.write(seed)
updater.SPEED_CHECKS = ((6, 64 * 1024), (22, 96 * 1024), (55, 64 * 1024))
first_prog2 = [None]


def pc2(done, total):
    if first_prog2[0] is None:
        first_prog2[0] = done


try:
    t0 = time.time()
    path = updater.download(ASSET, dest_dir=DEST,
                            mirrors=['https://gh-proxy.com/'],
                            expected_size=local_size, progress_cb=pc2)
    print('  [T2] done in %.0fs, first progress %s bytes (期望 >=1048576); sha match: %s'
          % (time.time() - t0, first_prog2[0], sha(path) == local_sha))
    os.remove(path)
except Exception as e:
    print('  [T2] FAILED:', str(e)[:200])

print()
print('===== T3: 新线路单源完整性（gh.ddlc.top） =====')
try:
    t0 = time.time()
    updater._download_one('https://gh.ddlc.top/' + ASSET, PART, None, local_size, 10, 0, updater.SPEED_CHECKS)
    print('  [T3] done in %.0fs; sha match: %s' % (time.time() - t0, sha(PART) == local_sha))
    os.remove(PART)
except Exception as e:
    print('  [T3] FAILED:', str(e)[:200])

print()
print('===== T4: 校验值取回优雅失败（不存在 -> None） =====')
v = updater._fetch_sha256(
    'https://github.com/Jace-Hao/xiyi-photo-upload/releases/download/v1.9/nonexistent.sha256',
    ['https://gh-proxy.com/'], timeout=6)
print('  [T4] result (expect None):', v)

for p in (PART, OUT):
    if os.path.exists(p):
        os.remove(p)
print()
print('ALL DONE')
