"""CDP 直连客户端 —— 绕过 chrome-control 扩展，直接驱动 Chrome-自动化 (9222)

为什么需要它：
  chrome-control 依赖浏览器扩展，扩展在多个 Chrome 实例并存时会掉线。
  本地脚本改用 CDP 协议直连 9222 端口，稳定且不占用 Agent 上下文。

关键能力：
  · 列出/新建/关闭标签页
  · 在指定页面执行 JS 并拿回结果（Runtime.evaluate）
  · 截图（Page.captureScreenshot）—— 图片直接存盘，不进 Agent 上下文
"""

from __future__ import annotations

import base64
import json
import time
import urllib.request

import websocket  # websocket-client

BASE = "http://127.0.0.1:9222"


def _http(path: str, method: str = "GET", timeout: float = 8.0) -> str:
    req = urllib.request.Request(BASE + path, method=method)
    return urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", "replace")


def version() -> dict:
    return json.loads(_http("/json/version"))


def list_pages() -> list[dict]:
    tabs = json.loads(_http("/json/list"))
    return [t for t in tabs
            if t.get("type") == "page"
            and not (t.get("url") or "").startswith("chrome://")]


def open_tab(url: str) -> dict:
    for m in ("PUT", "GET"):
        try:
            return json.loads(_http("/json/new?" + url, method=m, timeout=15))
        except Exception:
            continue
    raise RuntimeError("open_tab failed")


def close_tab(tab_id: str) -> None:
    try:
        _http("/json/close/" + tab_id)
    except Exception:
        pass


def find_tab(url_part: str) -> dict | None:
    for p in list_pages():
        if url_part in (p.get("url") or ""):
            return p
    return None


class Page:
    """一个标签页的 CDP 会话。"""

    def __init__(self, tab: dict, timeout: float = 20.0):
        self.tab = tab
        self.ws_url = tab["webSocketDebuggerUrl"]
        # Chrome 会拒绝带 Origin 头的 WebSocket（403），suppress_origin=True 可绕过
        self.ws = websocket.create_connection(
            self.ws_url, timeout=timeout, suppress_origin=True)
        self._id = 0

    def call(self, method: str, params: dict | None = None) -> dict:
        self._id += 1
        mid = self._id
        self.ws.send(json.dumps({"id": mid, "method": method,
                                 "params": params or {}}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(msg["error"])
                return msg.get("result", {})

    def eval_js(self, expr: str, await_promise: bool = False):
        r = self.call("Runtime.evaluate", {
            "expression": expr,
            "returnByValue": True,
            "awaitPromise": await_promise,
        })
        res = r.get("result", {})
        if res.get("subtype") == "error":
            raise RuntimeError(res.get("description", "js error"))
        return res.get("value")

    def goto(self, url: str, wait: float = 3.0) -> None:
        self.call("Page.navigate", {"url": url})
        time.sleep(wait)

    def screenshot(self, path: str, quality: int = 45) -> str:
        r = self.call("Page.captureScreenshot", {"format": "jpeg",
                                                 "quality": quality})
        data = base64.b64decode(r["data"])
        with open(path, "wb") as f:
            f.write(data)
        return path

    def close(self) -> None:
        try:
            self.ws.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def open_page(url: str, reuse: bool = True, wait: float = 3.0) -> Page:
    """找到（或新开）指定 URL 的标签页，返回可操作的 Page。"""
    tab = find_tab(url.split("//")[-1].split("?")[0]) if reuse else None
    if tab is None:
        tab = open_tab(url)
        time.sleep(wait)
    return Page(tab)


if __name__ == "__main__":
    v = version()
    print("Browser:", v.get("Browser"))
    print("Pages:")
    for p in list_pages():
        print(" -", (p.get("title") or "")[:45], "|", (p.get("url") or "")[:60])
