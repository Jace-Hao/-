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
        res = self._call("Runtime.evaluate",
                         {"expression": expr, "returnByValue": True,
                          "awaitPromise": True}, timeout=timeout)
        r = res.get("result", {})
        if r.get("subtype") == "error":
            raise CDPError(str(r.get("description", ""))[:200])
        return r.get("value")

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

    def upload_files(self, paths):
        """把本地文件直接注入上传输入框（等价于用户在文件对话框中选中这些文件）。"""
        mark = (
            "JSON.stringify((function(){"
            "var fis=document.querySelectorAll('input[type=file]');"
            "var target=null;"
            "for(var i=0;i<fis.length;i++){"
            " if(fis[i].closest && fis[i].closest('.photo_content')){target=fis[i];break;}}"
            "if(!target){for(var i=0;i<fis.length;i++){if(fis[i].multiple){target=fis[i];break;}}}"
            "if(!target&&fis.length){target=fis[0];}"
            "if(!target)return{ok:false};"
            "[].slice.call(document.querySelectorAll('[data-cdp-up]')).forEach(function(e){"
            " e.removeAttribute('data-cdp-up');});"
            "target.setAttribute('data-cdp-up','1');"
            "return{ok:true,count:fis.length};"
            "})())")
        try:
            r = self.ev(mark)
        except CDPError as e:
            return False, f"标记上传输入框失败：{e}"
        try:
            if not json.loads(r).get("ok"):
                return False, "页面上未找到上传输入框"
        except Exception:
            return False, "标记上传输入框失败"
        doc = self._call("DOM.getDocument", {"depth": 0})
        root = doc.get("root", {}).get("nodeId")
        q = self._call("DOM.querySelector", {"nodeId": root, "selector": "input[data-cdp-up]"})
        nid = q.get("nodeId")
        if not nid:
            return False, "未定位到上传输入框节点"
        self._call("DOM.setFileInputFiles", {"files": list(paths), "nodeId": nid}, timeout=30)
        return True, "ok"

    def wait_upload_feedback(self, timeout=15, base_count=None):
        """等待上传结果信号：成功提示 / 失败提示 / 缩略图数量增加。返回 (ok, 描述)。"""
        end = time.time() + timeout
        last_toasts = []
        while time.time() < end:
            toasts = self.visible_toasts()
            for t in toasts:
                if "成功" in t:
                    return True, f"提示：{t}"
                if "失败" in t or "错误" in t:
                    return False, f"提示：{t}"
            if toasts:
                last_toasts = toasts
            if base_count is not None and base_count >= 0:
                c = self.image_count()
                if c > base_count:
                    return True, f"缩略图数量 {base_count} → {c}"
            time.sleep(0.4)
        if last_toasts:
            return False, f"未确认成功，最近提示：{last_toasts[-1]}"
        return False, "未观察到上传成功/失败信号"
