# -*- coding: utf-8 -*-
"""
cdp_control.py — 通过 Chromium 调试协议（CDP）精准控制「洗衣管家」
================================================================
洗衣管家使用 CefSharp（Chromium 内核）；当它以
    xygjwinapp.exe --remote-debugging-port=9222
启动时（本机桌面快捷方式已自带该参数），本模块可以：
  1. 精确识别当前页面（按路由）——不依赖截图与坐标；
  2. 在「拍照页」输入条码并触发查询；
  3. 检测订单是否加载成功；
  4. 直接把本地照片文件“注入”页面上的上传输入框（绕过系统文件选择弹窗）。

仅在调试端口可用时启用；不可用时由主程序自动退回“图像锚点/坐标”模式。
"""
import json
import time
import urllib.request

try:
    import websocket  # websocket-client
except Exception:  # pragma: no cover
    websocket = None

DEFAULT_ROUTES = {
    "home": "#/",
    "more": "#/more",
    "photo": "#/clothManage/photograph?moduleid=3&templetid=3007&layoutType=1",
}


class CDPError(Exception):
    """CDP 调用异常。"""
    pass


class CDPApp:
    def __init__(self, port=9222, routes=None):
        self.port = int(port)
        self.routes = dict(DEFAULT_ROUTES)
        if routes:
            for k, v in routes.items():
                if v:
                    self.routes[k] = v
        self.ws = None
        self._mid = 0
        self.title = ""

    # ---------------- 连接 ----------------
    def connect(self):
        if websocket is None:
            return False, "未安装 websocket-client 库"
        try:
            data = json.load(urllib.request.urlopen(
                f"http://127.0.0.1:{self.port}/json", timeout=4))
        except Exception as e:
            return False, f"无法连接调试端口 {self.port}（{e}）"
        pages = [t for t in data if t.get("type") == "page"]
        if not pages:
            return False, "调试端口已连接，但未找到页面目标"
        main = None
        for t in pages:
            if "洗衣管家" in (t.get("title") or "") or "xiyijst" in (t.get("url") or ""):
                main = t
                break
        main = main or pages[0]
        try:
            self.ws = websocket.create_connection(
                main["webSocketDebuggerUrl"], timeout=15, suppress_origin=True)
        except Exception as e:
            return False, f"建立调试连接失败（{e}）"
        self.title = main.get("title", "")
        return True, self.title

    def close(self):
        try:
            if self.ws:
                self.ws.close()
        except Exception:
            pass
        self.ws = None

    def _call(self, method, params=None, timeout=20):
        if not self.ws:
            raise CDPError("未连接调试端口")
        self._mid += 1
        self.ws.send(json.dumps({"id": self._mid, "method": method,
                                 "params": params or {}}))
        end = time.time() + timeout
        while time.time() < end:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == self._mid:
                if msg.get("error"):
                    raise CDPError(f"{method} 失败：{str(msg['error'].get('message',''))[:150]}")
                return msg.get("result", {})
        raise CDPError(f"{method} 调用超时")

    def ev(self, expr, timeout=20):
        """执行 JS 表达式。超时时自动重连并重试一次（防止对话框阻塞造成硬崩）。"""
        last = None
        for _attempt in (1, 2):
            try:
                res = self._call("Runtime.evaluate",
                                 {"expression": expr, "returnByValue": True,
                                  "awaitPromise": True}, timeout=timeout)
                r = res.get("result", {})
                if r.get("subtype") == "error":
                    raise CDPError(str(r.get("description", ""))[:200])
                return r.get("value")
            except Exception as e:
                last = e
                emsg = (str(e) or '') + type(e).__name__
                low = emsg.lower()
                if ('timeout' in low) or ('timed out' in low) or ('超时' in emsg):
                    try:
                        self._reconnect()
                        time.sleep(0.4)
                    except Exception:
                        pass
                    continue
                raise
        raise CDPError(f"调试调用超时：{last}")

    # ---------------- 状态识别 ----------------
    def get_hash(self):
        try:
            return self.ev("location.hash") or ""
        except CDPError:
            return ""

    def page(self):
        """返回 'home' | 'more' | 'photo' | None。"""
        h = self.get_hash()
        photo_prefix = self.routes["photo"].split("?")[0]
        if h.startswith(photo_prefix):
            return "photo"
        if h.startswith(self.routes["more"]):
            return "more"
        if h in ("", "#", "#/") or h == self.routes["home"]:
            return "home"
        return None

    def has_visible_text(self, text, exact=True):
        js = (
            "JSON.stringify((function(){"
            "var target=%s,exact=%s,all=document.querySelectorAll('*');"
            "for(var i=0;i<all.length && i<8000;i++){var e=all[i];"
            "if(e.children.length===0){var t=(e.textContent||'').trim();"
            "var ok=exact?(t===target):(t.indexOf(target)>=0);"
            "if(ok&&(e.offsetWidth||e.offsetHeight))return true;}}"
            "return false;})())"
            % (json.dumps(text), "true" if exact else "false"))
        try:
            return bool(self.ev(js))
        except CDPError:
            return False

    def has_photo_input(self):
        js = ("[].slice.call(document.querySelectorAll('input')).some(function(i){"
              "return (i.placeholder||'').indexOf('衣物条码号')>=0"
              " && (i.offsetWidth||i.offsetHeight);})")
        try:
            return bool(self.ev(js))
        except CDPError:
            return False

    def is_order_loaded(self):
        """出现可见的『上传图片/继续拍照』即视为订单已加载。"""
        return self.has_visible_text("上传图片") or self.has_visible_text("继续拍照")

    def image_count(self):
        """当前页面图片缩略图数量（用于观察上传结果）。"""
        try:
            return int(self.ev("document.querySelectorAll"
                               "('.viewer-container img, .image-wrapper img,"
                               " .photo_content img, .picture_page img').length") or 0)
        except CDPError:
            return -1

    def uploaded_photo_count(self):
        """订单已上传到云端的照片数量（按去重后的图片地址统计）。"""
        js = ("JSON.stringify((function(){var s={},c=0;"
              "document.querySelectorAll('img').forEach(function(m){"
              "var src=m.src||'';"
              "if(/oss\\.xiyijst\\.com|shopcloth|\\/upload\\//i.test(src)){"
              "var k=src.split('?')[0];if(!s[k]){s[k]=1;c++;}}});return c;})())")
        try:
            return int(self.ev(js) or 0)
        except (CDPError, ValueError, TypeError):
            return 0

    def visible_toasts(self):
        """读取可能的提示条文字（上传成功等）。"""
        try:
            v = self.ev(
                "JSON.stringify([].slice.call(document.querySelectorAll"
                "('.el-message,.el-notification,.toast,[class*=message]'))"
                ".map(function(e){return (e.textContent||'').trim();})"
                ".filter(function(t){return t && t.length<60;}).slice(0,5))")
            return json.loads(v) if isinstance(v, str) else []
        except Exception:
            return []

    # ---------------- 动作 ----------------
    def goto_page(self, page_name, timeout=6):
        """导航到指定页面：路由直达优先，拍照页可点击兜底。"""
        if self.page() == page_name:
            return True
        try:
            self.ev("location.hash = %s; void 0;" % json.dumps(self.routes[page_name]))
        except CDPError:
            pass
        end = time.time() + timeout
        while time.time() < end:
            p = self.page()
            if p == page_name:
                if page_name != "photo" or self.has_photo_input():
                    return True
            time.sleep(0.3)
        if page_name == "photo":
            return self._goto_photo_by_click(max(4, timeout))
        return False

    def _goto_photo_by_click(self, timeout=8):
        try:
            self.click_text("更多", exact=True)
            time.sleep(1.0)
            self.click_text("拍照", exact=True)
        except CDPError:
            pass
        end = time.time() + timeout
        while time.time() < end:
            if self.has_photo_input():
                return True
            time.sleep(0.3)
        return False

    def click_text(self, text, exact=True, index=0):
        js = (
            "JSON.stringify((function(){"
            "var target=%s,exact=%s,idx=%d,cands=[];"
            "var all=document.querySelectorAll('*');"
            "for(var i=0;i<all.length && i<9000;i++){var e=all[i];"
            "var t=(e.textContent||'').trim();"
            "var ok=exact?(t===target):(t.indexOf(target)>=0);"
            "if(!ok||!(e.offsetWidth||e.offsetHeight))continue;"
            "var hasSameChild=false;"
            "for(var j=0;j<e.children.length;j++){"
            " var ct=(e.children[j].textContent||'').trim();"
            " if(exact?ct===target:ct.indexOf(target)>=0){hasSameChild=true;break;}}"
            "if(hasSameChild)continue;"
            "cands.push(e);}"
            "if(!cands.length)return{ok:false,why:'notfound'};"
            "var el=cands[Math.min(idx,cands.length-1)];"
            "try{el.click();}catch(e){return{ok:false,why:String(e)}}"
            "return{ok:true,tag:el.tagName,cls:(el.className||'').toString().slice(0,60),"
            "text:(el.textContent||'').trim().slice(0,20),count:cands.length};"
            "})())" % (json.dumps(text), "true" if exact else "false", index))
        try:
            return json.loads(self.ev(js))
        except Exception as e:
            return {"ok": False, "why": str(e)[:120]}

    def enter_barcode(self, code):
        """在拍照页条码输入框输入并触发查询。"""
        js = (
            "JSON.stringify((function(){"
            "var inp=[].slice.call(document.querySelectorAll('input')).filter(function(i){"
            "return (i.placeholder||'').indexOf('衣物条码号')>=0"
            " && (i.offsetWidth||i.offsetHeight);})[0];"
            "if(!inp)return{ok:false,why:'input not found'};"
            "inp.focus();"
            "var setter=Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set;"
            "setter.call(inp,%s);"
            "inp.dispatchEvent(new Event('input',{bubbles:true}));"
            "inp.dispatchEvent(new Event('change',{bubbles:true}));"
            "function vis(e){return !!(e.offsetWidth||e.offsetHeight);}"
            "var ir=inp.getBoundingClientRect();var best=null,bestD=1e9,bestTxt='';"
            "var all=document.querySelectorAll('p,button,span,div');"
            "for(var i=0;i<all.length;i++){var e=all[i];var t=(e.textContent||'').trim();"
            "if((t==='查询'||t==='确认')&&vis(e)&&e.children.length===0){"
            "var r2=e.getBoundingClientRect();"
            "var d=Math.abs(r2.top-ir.top)+Math.abs(r2.left-ir.right);"
            "if(d<bestD){bestD=d;best=e;bestTxt=t;}}}"
            "if(!best)return{ok:false,why:'query button not found'};"
            "best.click();"
            "return{ok:true,clicked:bestTxt,dist:Math.round(bestD)};"
            "})())" % json.dumps(code))
        try:
            return json.loads(self.ev(js))
        except Exception as e:
            return {"ok": False, "why": str(e)[:120]}

    def wait_order_loaded(self, timeout=10):
        end = time.time() + timeout
        while time.time() < end:
            if self.is_order_loaded():
                return True
            time.sleep(0.35)
        return False

    def wait_order_info(self, barcode, timeout=8):
        """等待订单信息区出现指定条码（说明新订单数据已刷新到位）。"""
        js = ("JSON.stringify((function(){var b=%s;var all=document.querySelectorAll('*');"
              "for(var i=0;i<all.length && i<9000;i++){var e=all[i];"
              "if(e.tagName==='INPUT'||e.tagName==='TEXTAREA')continue;"
              "if(e.children.length<=1 && (e.textContent||'').trim()===b"
              " && (e.offsetWidth||e.offsetHeight))return true;}"
              "return false;})())" % json.dumps(barcode))
        end = time.time() + timeout
        while time.time() < end:
            try:
                if self.ev(js):
                    return True
            except CDPError:
                pass
            time.sleep(0.3)
        return False

    def stable_uploaded_photo_count(self, timeout=6, interval=0.5):
        """连续两次读数一致才返回（避免读到上一单遗留/未渲染完的数量）。"""
        end = time.time() + timeout
        last = None
        while time.time() < end:
            c = self.uploaded_photo_count()
            if last is not None and c == last:
                return c
            last = c
            time.sleep(interval)
        return last if last is not None else 0

    def get_upload_button_rect(self):
        """返回「上传图片」按钮（p.upload_btn）中心点视口坐标；找不到返回 None。"""
        js = ("JSON.stringify((function(){var e=document.querySelector('p.upload_btn');"
              "if(!e||!(e.offsetWidth||e.offsetHeight))return null;"
              "var r=e.getBoundingClientRect();"
              "return {x:Math.round(r.left+r.width/2),y:Math.round(r.top+r.height/2)};})())")
        try:
            v = self.ev(js)
        except CDPError:
            return None
        try:
            return json.loads(v) if isinstance(v, str) else None
        except Exception:
            return None

    def click_upload_button_trusted(self):
        """用 CDP Input 域对「上传图片」按钮发送受信任点击（模拟真实用户点击，
        可触发 CefSharp 打开系统文件对话框）。
        注意：对话框一旦打开，渲染进程即被阻塞、CDP 响应会超时，
        因此这里 fire-and-forget（只发不读）。返回 True 表示事件已发出。"""
        rect = self.get_upload_button_rect()
        if not rect:
            return False
        for evt, extra in (("mouseMoved", {}),
                           ("mousePressed", {"button": "left", "clickCount": 1}),
                           ("mouseReleased", {"button": "left", "clickCount": 1})):
            self._mid += 1
            msg = {"id": self._mid, "method": "Input.dispatchMouseEvent",
                   "params": dict({"type": evt, "x": rect["x"], "y": rect["y"]}, **extra)}
            sent = False
            for _attempt in (1, 2):
                try:
                    self.ws.send(json.dumps(msg))
                    sent = True
                    break
                except Exception:
                    try:
                        self._reconnect()
                    except Exception:
                        break
            if not sent:
                return False
            time.sleep(0.1)
        return True

    def _reconnect(self):
        """重连当前页面。"""
        try:
            self.close()
        except Exception:
            pass
        ok, _info = self.connect()
        return ok

    def wait_upload_complete(self, base_count, expected, timeout=60):
        """等待页面出现新增照片（上传完成的判断信号）。
        返回 (ok, 描述)。成功条件：页面照片数 >= 原有 + 本次文件数。"""
        end = time.time() + timeout
        last = base_count
        while time.time() < end:
            try:
                c = self.uploaded_photo_count()
            except Exception:
                c = last
            last = c
            if c >= base_count + expected:
                return True, f"已新增 {c - base_count} 张照片"
            time.sleep(1.0)
        if last > base_count:
            return False, f"疑似部分完成：仅新增 {last - base_count}/{expected} 张，请人工核对"
        return False, "未观察到新增照片（上传可能未开始或未完成）"
