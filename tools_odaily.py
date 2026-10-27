"""Odaily 星球日报快讯（REST API 版，取代 RSS）

文档：https://github.com/ODAILY/REST-API
  Base URL: https://api.odaily.news   （无认证，公开接口）
  GET /api/v1/newsflash?page=1&size=N&lang=zh-cn[&isImportant=true]
  GET /api/v1/article?page=1&size=N&lang=zh-cn

比 RSS 强的地方：
  · isImportant 布尔字段（官方「爆」标记）
  · 分页，最多可翻到 total=380,765 条
  · publishTimestamp 精确毫秒时间
  · title / content / sourceUrl / link 结构化

注意：Odaily API **只提供新闻**，没有行情/K线/清算数据。
      K线用 OKX API，爆仓用 OKX liquidation-orders，清算地图仍需 CoinGlass。

用法：
  python tools_odaily.py              # 最近 6 小时快讯（按重要度排序）
  python tools_odaily.py --important  # 只看官方重要标记
  python tools_odaily.py --post       # 看深度文章
  python tools_odaily.py --hours 12 --pages 3
"""

from __future__ import annotations

import json
import re
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from html import unescape
from pathlib import Path

ROOT = Path(r"D:\DP workspace\btc_system")
LOG = ROOT / "data" / "odaily_log.jsonl"
CST = timezone(timedelta(hours=8))
BASE = "https://api.odaily.news"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/147.0 Safari/537.36")

WEIGHTS = {
    "美联储": 9, "FOMC": 9, "加息": 8, "降息": 8, "CPI": 8, "非农": 8,
    "通胀": 7, "关税": 7, "特朗普": 7, "中期选举": 8, "SEC": 8, "监管": 7,
    "比特币": 8, "BTC": 7, "以太坊": 6, "ETF": 9, "现货ETF": 9,
    "爆仓": 8, "清算": 8, "巨鲸": 6, "鲸鱼": 6,
    "Coinbase": 7, "Binance": 7, "欧易": 7, "OKX": 7, "贝莱德": 8,
    "灰度": 7, "MicroStrategy": 8, "Strategy": 7,
    "大涨": 6, "大跌": 6, "暴涨": 7, "暴跌": 7, "创历史新高": 9,
    "跳水": 7, "拉升": 6, "跌破": 7, "突破": 6, "止损": 5,
    "黑客": 7, "被盗": 7, "攻击": 6, "漏洞": 6, "跑路": 6,
    "破产": 8, "暂停提现": 8, "脱锚": 8,
    "空头": 5, "多头": 5, "做多": 5, "做空": 5, "逼空": 7,
    "纳斯达克": 6, "标普": 5, "费城半导体": 6, "科技股": 5,
}

NOISE = ["直播", "点击观看", "立即观看", "邀请注册", "空投领取", "活动报名",
         "AMA", "抽奖", "白皮书下载", "电报群", "转发抽奖"]


def api(path: str, timeout=20):
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req = urllib.request.Request(BASE + path,
                                 headers={"User-Agent": UA,
                                          "Accept": "application/json"})
    return json.loads(op.open(req, timeout=timeout).read().decode("utf-8", "replace"))


def strip_html(s: str) -> str:
    s = re.sub(r"<[^>]+>", " ", s or "")
    s = unescape(s).replace("\u3000", " ")
    return re.sub(r"\s+", " ", s).strip()


def score(text: str):
    s, hit = 0, []
    for k, w in WEIGHTS.items():
        if k in text:
            if w > s:
                s = w
            hit.append(k)
    if len(hit) >= 3:
        s += 2
    return min(s, 12), hit


def fetch(kind="newsflash", pages=2, important_only=False):
    out = []
    for pg in range(1, pages + 1):
        q = "/api/v1/%s?page=%d&size=20&lang=zh-cn" % (kind, pg)
        if important_only:
            q += "&isImportant=true"
        try:
            d = api(q)
        except Exception as e:
            if pg == 1:
                raise
            break
        if d.get("code") != 200:
            break
        data = d.get("data") or {}
        lst = data.get("list") or []
        if not lst:
            break
        out.extend(lst)
        if not data.get("hasMore"):
            break
    return out


def main():
    args = sys.argv[1:]
    kind = "article" if "--post" in args else "newsflash"
    imp_only = "--important" in args
    hours = 6.0
    pages = 2
    if "--hours" in args:
        try:
            hours = float(args[args.index("--hours") + 1])
        except Exception:
            pass
    if "--pages" in args:
        try:
            pages = int(args[args.index("--pages") + 1])
        except Exception:
            pass

    try:
        raw = fetch(kind, pages, imp_only)
    except Exception as e:
        print("Odaily API 失败:", str(e)[:120])
        return 1

    now = datetime.now(CST)
    cutoff = now - timedelta(hours=hours)
    rows = []
    for it in raw:
        ts = it.get("publishTimestamp") or 0
        try:
            t = datetime.fromtimestamp(ts / 1000, CST)
        except Exception:
            continue
        if t < cutoff:
            continue
        title = strip_html(it.get("title") or "")
        content = strip_html(it.get("content") or it.get("summary") or "")[:400]
        txt = title + " " + content
        if len(txt) < 15:
            continue
        if any(n in txt for n in NOISE):
            continue
        sc, hit = score(txt)
        is_imp = bool(it.get("isImportant"))
        if is_imp:
            sc = max(sc, 10)
            hit = ["★官方重要"] + hit
        rows.append({"t": t, "title": title, "content": content,
                     "score": sc, "hit": hit, "imp": is_imp,
                     "link": it.get("link") or ""})

    if not rows:
        print("Odaily 最近 %.0f 小时无有效条目（原始 %d 条）" % (hours, len(raw)))
        return 0

    rows.sort(key=lambda x: (-x["score"], -x["t"].timestamp()))
    picked = rows[:25]

    # 存档
    try:
        seen = set()
        if LOG.exists():
            for line in LOG.read_text(encoding="utf-8").splitlines()[-3000:]:
                try:
                    seen.add((json.loads(line).get("txt") or "")[:50])
                except Exception:
                    pass
        with open(LOG, "a", encoding="utf-8") as f:
            for r in picked:
                if r["title"][:50] in seen:
                    continue
                f.write(json.dumps({
                    "ts": r["t"].strftime("%Y-%m-%d %H:%M"),
                    "txt": r["title"], "desc": r["content"][:200],
                    "score": r["score"], "hit": r["hit"],
                    "important": r["imp"],
                }, ensure_ascii=False) + "\n")
    except Exception:
        pass

    n_imp = sum(1 for r in picked if r["imp"])
    print("=== Odaily %s · 最近 %.0f 小时 · 输出 %d 条（官方重要 %d）==="
          % ("文章" if kind == "post" else "快讯", hours, len(picked), n_imp))
    for r in picked:
        if r["imp"]:
            star = "★★官方重要"
        else:
            star = "★★" if r["score"] >= 9 else ("★" if r["score"] >= 7 else "· ")
        print("  %-10s %s [%2d] %s" % (star, r["t"].strftime("%H:%M"),
                                       r["score"], r["title"][:88]))
        if r["content"]:
            print("             %s" % r["content"][:110])
    return 0


if __name__ == "__main__":
    sys.exit(main())
