# -*- coding: utf-8 -*-
"""build_handover.py — 由《开发交接文档.md》生成网页版（复用既定版式）。

用法：py -3.14 build_handover.py
- 读取项目根目录 `开发交接文档.md`（跳过开头的封面区），转换为单文件自包含 html；
- 版式 CSS 优先从现有 `开发交接文档.html` 中提取复用（保持版式稳定）；
  若 html 不存在则使用内置兜底样式；
- 侧栏目录（TOC）由正文二/三级标题自动生成。
"""
import os
import re

import markdown

PROJ = r'E:\软件开发\洗衣管家上传助手'
MD_PATH = os.path.join(PROJ, '开发交接文档.md')
HTML_PATH = os.path.join(PROJ, '开发交接文档.html')

FALLBACK_CSS = """
:root{--paper:#faf9f6;--ink:#1b1a17;--ink-2:#4c4a44;--ink-3:#807c74;--rule:#d9d6cc;--accent:#2e5d4b;}
*{margin:0;padding:0;box-sizing:border-box}
body{background:var(--paper);color:var(--ink);font-family:"Microsoft YaHei UI","Microsoft YaHei",sans-serif;line-height:1.7}
.layout{display:flex}
.sidebar{width:256px;flex:0 0 256px;padding:28px 20px;border-right:1px solid var(--rule)}
.content{max-width:920px;padding:48px 56px}
h2{font-size:22px;margin:36px 0 12px;border-bottom:1px solid var(--rule);padding-bottom:6px}
h3{font-size:17px;margin:22px 0 8px}
table{border-collapse:collapse;margin:12px 0;width:100%;font-size:13.5px}
th,td{border:1px solid var(--rule);padding:6px 10px;text-align:left;vertical-align:top}
th{background:#f3f1ea}
pre{background:#f3f1ea;border:1px solid var(--rule);padding:12px;overflow:auto;font-size:12.5px;line-height:1.6}
code{font-family:Consolas,monospace;font-size:12.8px}
blockquote{border-left:2px solid var(--accent);padding-left:14px;color:var(--ink-2)}
.toc a{display:block;text-decoration:none;color:var(--ink-2);font-size:13px;padding:3px 0}
.toc-num{color:var(--accent);margin-right:6px;font-family:Consolas,monospace}
.toc-sub{padding-left:22px}
.cover{padding:34px 0 26px;border-bottom:2px solid var(--accent);margin-bottom:18px}
.kicker{font-size:11px;letter-spacing:.18em;color:var(--accent);margin-bottom:12px}
.t1{font-size:30px;font-weight:700}
.t2{font-size:15px;color:var(--ink-2);margin-top:6px}
.doc-meta{margin-top:16px;font-size:13px}
.mrow{display:flex;gap:14px;margin-top:4px}
.mk{color:var(--ink-3);width:76px;flex:0 0 76px}
"""


READABILITY_PATCH = """
/* v1.10 阅读性微调：略增正文字号与行高、表格内边距 */
.content{font-size:15px;line-height:1.75}
.content p,.content li{font-size:15px}
.content table{font-size:13.5px}
.content th,.content td{padding:7px 10px}
"""


def main():
    css = ''
    if os.path.exists(HTML_PATH):
        old = open(HTML_PATH, encoding='utf-8').read()
        m = re.search(r'<style>(.*?)</style>', old, re.S)
        if m:
            css = m.group(1)
    if not css:
        css = FALLBACK_CSS
    css = css + READABILITY_PATCH

    md_text = open(MD_PATH, encoding='utf-8').read()
    i = md_text.find('\n---\n')
    body_md = md_text[i + 5:] if i != -1 else md_text

    md = markdown.Markdown(extensions=['tables', 'fenced_code', 'toc', 'sane_lists'])
    body_html = md.convert(body_md)

    def build_toc(tokens):
        out = []
        n = 0
        for t in tokens:
            if t['level'] == 2:
                n += 1
                subs = ''
                for c in t.get('children', []):
                    if c['level'] == 3:
                        subs += '<a href="#%s">%s</a>' % (c['id'], c['name'])
                sec = ('<div class="toc-sec"><a href="#%s"><span class="toc-num">%02d</span>'
                       '<span class="toc-label">%s</span></a>' % (t['id'], n, t['name']))
                if subs:
                    sec += '<div class="toc-sub">' + subs + '</div>'
                sec += '</div>'
                out.append(sec)
        return '\n'.join(out)

    toc_html = build_toc(md.toc_tokens)
    cover = ('<div class="cover">'
             '<div class="kicker">INTERNAL · 开发交接文档</div>'
             '<div class="t1">洗衣管家 · 照片批量上传助手</div>'
             '<div class="t2">开发交接文档 · v1.10 · 2026-09-22</div>'
             '<div class="doc-meta">'
             '<div class="mrow"><span class="mk">交接对象</span><span class="mv">全栈工程师</span></div>'
             '<div class="mrow"><span class="mk">当前版本</span><span class="mv">v1.10（2026-09-22 发布）</span></div>'
             '<div class="mrow"><span class="mk">仓库</span><span class="mv">github.com/Jace-Hao/xiyi-photo-upload</span></div>'
             '</div></div>')
    page = ('<!DOCTYPE html>\n<html lang="zh-CN">\n<head>\n<meta charset="UTF-8">\n'
            '<meta name="viewport" content="width=device-width, initial-scale=1.0">\n'
            '<title>洗衣管家 · 照片批量上传助手 — 开发交接文档 v1.10</title>\n<style>'
            + css + '</style>\n</head>\n<body>\n<div class="layout">\n<aside class="sidebar">\n'
            '<div class="brand"><div class="monogram">洗</div><div>'
            '<div class="bname">洗衣管家 · 照片批量上传助手</div>'
            '<div class="bsub">开发交接文档 · v1.10</div></div></div>\n'
            '<div class="side-meta"><div>DOCUMENT · 2026-09-22</div><div>VERSION · v1.10</div></div>\n'
            '<nav class="toc">\n' + toc_html + '\n</nav>\n</aside>\n<main class="content">\n'
            + cover + '\n' + body_html + '\n</main>\n</div>\n</body>\n</html>')
    open(HTML_PATH, 'w', encoding='utf-8').write(page)
    print('html written:', len(page), 'chars')
    print('h2 count:', body_html.count('<h2'), '| toc entries:', toc_html.count('toc-sec'))


if __name__ == '__main__':
    main()
