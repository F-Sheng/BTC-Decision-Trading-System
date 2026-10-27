"""测试 Odaily RSS（快讯 + 文章）

RSS 地址来自官方 GitHub: https://github.com/ODAILY/RSS
  快讯: https://rss.odaily.news/rss/newsflash
  文章: https://rss.odaily.news/rss/post
"""

import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta

CST = timezone(timedelta(hours=8))
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/147.0 Safari/537.36")

URLS = {
    "newsflash": "https://rss.odaily.news/rss/newsflash",
    "post": "https://rss.odaily.news/rss/post",
}


def fetch(url, use_proxy=False):
    handlers = []
    if use_proxy:
        handlers.append(urllib.request.ProxyHandler(
            {"http": "http://127.0.0.1:57777", "https": "http://127.0.0.1:57777"}))
    else:
        handlers.append(urllib.request.ProxyHandler({}))
    op = urllib.request.build_opener(*handlers)
    req = urllib.request.Request(url, headers={"User-Agent": UA,
                                               "Accept": "application/rss+xml,*/*"})
    return op.open(req, timeout=20).read()


def parse(raw: bytes):
    root = ET.fromstring(raw)
    ch = root.find("channel")
    items = ch.findall("item") if ch is not None else []
    out = []
    for it in items:
        def g(tag):
            e = it.find(tag)
            return (e.text or "").strip() if e is not None and e.text else ""
        out.append({
            "title": g("title"),
            "desc": g("description")[:300],
            "pub": g("pubDate"),
            "link": g("link"),
        })
    return out


for name, url in URLS.items():
    print("=== %s ===" % name)
    got = None
    for use_proxy in (False, True):
        try:
            raw = fetch(url, use_proxy)
            print("  %s 获取成功 %d bytes" % ("代理" if use_proxy else "直连", len(raw)))
            got = raw
            break
        except Exception as e:
            print("  %s 失败: %s" % ("代理" if use_proxy else "直连", str(e)[:80]))
    if not got:
        continue
    try:
        items = parse(got)
    except Exception as e:
        print("  解析失败:", e)
        print("  原始前 300:", got[:300])
        continue
    print("  条目数:", len(items))
    for it in items[:6]:
        print("   [%s] %s" % (it["pub"][:25], it["title"][:70]))
        if it["desc"]:
            print("        %s" % it["desc"][:110])
    print()
