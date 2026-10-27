"""Odaily 星球日报快讯（RSS，免费无需 key）

来源：官方 GitHub https://github.com/ODAILY/RSS
  快讯: https://rss.odaily.news/rss/newsflash
  文章: https://rss.odaily.news/rss/post

为什么加这个（用户 2026-10-27 提议）：
  金十偏宏观（美联储/地缘/原油），对币圈微观事件覆盖弱；
  Odaily 专注 Web3/加密，快讯更密、更贴近 BTC 交易。
  直连可用，不需要代理，不占浏览器。

用法：
  python tools_odaily.py             # 最近快讯（按重要度排序）
  python tools_odaily.py --post      # 看深度文章
  python tools_odaily.py --hours 6   # 时间窗口
  python tools_odaily.py --json      # 输出 JSON
"""

from __future__ import annotations

import html
import json
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

ROOT = Path(r"D:\DP workspace\btc_system")
LOG = ROOT / "data" / "odaily_log.jsonl"
CST = timezone(timedelta(hours=8))

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/147.0 Safari/537.36")

FEEDS = {
    "newsflash": "https://rss.odaily.news/rss/newsflash",
    "post": "https://rss.odaily.news/rss/post",
}

# 对 BTC 定价有影响的权重
WEIGHTS = {
    # 宏观/政策
    "美联储": 9, "FOMC": 9, "加息": 8, "降息": 8, "CPI": 8, "非农": 8,
    "通胀": 7, "关税": 7, "特朗普": 7, "中期选举": 8, "SEC": 8, "监管": 7,
    # 加密核心
    "比特币": 8, "BTC": 7, "以太坊": 6, "ETF": 8, "现货ETF": 9,
    "爆仓": 7, "清算": 7, "巨鲸": 6, "鲸鱼": 6, "USD": 4,
    "Coinbase": 7, "Binance": 7, "欧易": 7, "OKX": 7, "贝莱德": 8,
    "灰度": 7, "MicroStrategy": 8, "Strategy": 7,
    # 行情异动
    "大涨": 6, "大跌": 6, "暴涨": 7, "暴跌": 7, "创历史新高": 9,
    "跳水": 7, "拉升": 6, "跌破": 7, "突破": 6,
    # 风险事件
    "黑客": 7, "被盗": 7, "攻击": 6, "漏洞": 6, "跑路": 6,
    "破产": 8, "清算": 7, "暂停提现": 8,
    # 美股联动
    "纳斯达克": 6, "标普": 5, "费城半导体": 6, "科技股": 5, "AI": 5,
}

NOISE = ["直播", "点击观看", "立即观看", "邀请注册", "空投领取", "活动报名",
         "AMA", "抽奖", "白皮书下载", "电报群"]


def fetch(kind="newsflash"):
    url = FEEDS[kind]
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req = urllib.request.Request(url, headers={"User-Agent": UA,
                                               "Accept": "application/rss+xml,*/*"})
    return op.open(req, timeout=20).read()


def strip_html(s: str) -> str:
    s = re.sub(r"<[^>]+>", " ", s or "")
    s = html.unescape(s)
    s = s.replace("\u3000", " ")
    return re.sub(r"\s+", " ", s).strip()


def parse(raw: bytes):
    root = ET.fromstring(raw)
    ch = root.find("channel")
    items = ch.findall("item") if ch is not None else []
    out = []
    for it in items:
        def g(tag):
            e = it.find(tag)
            return (e.text or "").strip() if e is not None and e.text else ""
        pub = g("pubDate")
        try:
            t = parsedate_to_datetime(pub).astimezone(CST)
        except Exception:
            t = None
        out.append({
            "title": strip_html(g("title")),
            "desc": strip_html(g("description"))[:400],
            "link": g("link"),
            "t": t,
        })
    return out


def score(text: str):
    s = 0
    hit = []
    for k, w in WEIGHTS.items():
        if k in text:
            if w > s:
                s = w
            hit.append(k)
    # 多条命中加成（说明是复合事件）
    if len(hit) >= 3:
        s += 2
    return min(s, 12), hit


def main():
    args = sys.argv[1:]
    kind = "post" if "--post" in args else "newsflash"
    hours = 6.0
    if "--hours" in args:
        try:
            hours = float(args[args.index("--hours") + 1])
        except Exception:
            pass
    as_json = "--json" in args
    limit = 20

    try:
        rows = parse(fetch(kind))
    except Exception as e:
        print("Odaily RSS 获取失败:", str(e)[:100])
        return 1

    now = datetime.now(CST)
    cutoff = now - timedelta(hours=hours)
    out = []
    for r in rows:
        if r["t"] is None or r["t"] < cutoff:
            continue
        txt = r["title"] + " " + r["desc"]
        if any(n in txt for n in NOISE):
            continue
        sc, hit = score(txt)
        out.append({**r, "score": sc, "hit": hit})

    if not out:
        print("Odaily 最近 %.0f 小时无有效快讯（共取到 %d 条）" % (hours, len(rows)))
        return 0

    out.sort(key=lambda x: (-x["score"], -(x["t"].timestamp() if x["t"] else 0)))
    picked = out[:limit]

    # 存档
    try:
        seen = set()
        if LOG.exists():
            for line in LOG.read_text(encoding="utf-8").splitlines()[-2000:]:
                try:
                    seen.add(json.loads(line)["title"][:50])
                except Exception:
                    pass
        new_rows = [r for r in picked if r["title"][:50] not in seen]
        if new_rows:
            LOG.parent.mkdir(parents=True, exist_ok=True)
            with open(LOG, "a", encoding="utf-8") as f:
                for r in new_rows:
                    f.write(json.dumps({
                        "ts": r["t"].strftime("%Y-%m-%d %H:%M") if r["t"] else "",
                        "txt": r["title"], "desc": r["desc"][:200],
                        "score": r["score"], "hit": r["hit"],
                    }, ensure_ascii=False) + "\n")
    except Exception:
        pass

    if as_json:
        print(json.dumps([{k: (v.strftime("%Y-%m-%d %H:%M") if k == "t" and v else v)
                           for k, v in r.items()} for r in picked],
                         ensure_ascii=False, indent=2))
        return 0

    n_high = sum(1 for r in picked if r["score"] >= 9)
    print("=== Odaily %s · 最近 %.0f 小时 · 输出 %d 条（权重>=9: %d）==="
          % ("文章" if kind == "post" else "快讯", hours, len(picked), n_high))
    for r in picked:
        star = "★★" if r["score"] >= 9 else ("★" if r["score"] >= 7 else "·")
        tm = r["t"].strftime("%H:%M") if r["t"] else "??:??"
        print("  %s %s [%2d] %s" % (star, tm, r["score"], r["title"][:95]))
        if r["desc"]:
            print("        %s" % r["desc"][:120])
    return 0


if __name__ == "__main__":
    sys.exit(main())
