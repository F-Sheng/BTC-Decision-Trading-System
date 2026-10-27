"""探测 Odaily REST API 全部端点（文档: github.com/ODAILY/REST-API）"""

import json
import re
import urllib.request

BASE = "https://api.odaily.news"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/147.0 Safari/537.36")


def get(path, timeout=20):
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req = urllib.request.Request(BASE + path,
                                 headers={"User-Agent": UA,
                                          "Accept": "application/json"})
    raw = op.open(req, timeout=timeout).read().decode("utf-8", "replace")
    return json.loads(raw)


# 1) 先拿文档
print("=== 文档里的端点 ===")
try:
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req = urllib.request.Request(
        "https://raw.githubusercontent.com/ODAILY/REST-API/main/README.md",
        headers={"User-Agent": UA})
    doc = op.open(req, timeout=25).read().decode("utf-8", "replace")
    for m in re.finditer(r"`(GET|POST)\s+(/api/[^`]+)`", doc):
        print("  %s %s" % (m.group(1), m.group(2)))
    heads = re.findall(r"^###?\s+(.+)$", doc, re.M)
    print("\n=== 章节 ===")
    for h in heads:
        print("  -", h)
except Exception as e:
    print("  文档读取失败:", str(e)[:100])

# 2) 实测关键端点
print("\n=== 实测 ===")
tests = [
    ("/api/v1/newsflash?page=1&size=5&lang=zh-cn", "快讯"),
    ("/api/v1/newsflash?page=1&size=5&lang=zh-cn&isImportant=true", "重要快讯"),
    ("/api/v1/article?page=1&size=3&lang=zh-cn", "文章"),
    ("/api/v1/news?page=1&size=3", "news"),
    ("/api/v1/flash?page=1&size=3", "flash"),
    ("/api/v1/quote?symbol=BTC", "行情"),
    ("/api/v1/kline?symbol=BTC&interval=1h", "K线"),
    ("/api/v1/liquidation?symbol=BTC", "清算"),
]
for path, label in tests:
    try:
        d = get(path)
        code = d.get("code")
        data = d.get("data")
        if isinstance(data, dict):
            lst = data.get("list") or []
            print("  OK  %-8s code=%s total=%s 本页%d条"
                  % (label, code, data.get("total"), len(lst)))
            if lst:
                it = lst[0]
                print("       字段:", sorted(it.keys())[:12])
                isimp = it.get("isImportant")
                print("       isImportant=%s  title=%s" % (isimp, (it.get("title") or "")[:40]))
        elif isinstance(data, list):
            print("  OK  %-8s code=%s list=%d" % (label, code, len(data)))
        else:
            print("  --  %-8s code=%s msg=%s" % (label, code, str(d.get("msg"))[:50]))
    except Exception as e:
        print("  FAIL %-8s %s" % (label, str(e)[:70]))
