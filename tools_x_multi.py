"""X 推特多账号分层检索（25+ 账号，分批查询）

用户 2026-10-28 提供博主清单（C:\\Users\\81559\\Desktop\\新建 文本文档 (2).txt），
分四类：加密核心 / 新闻链上 / 宏观分析 / 政治监管。

设计：
  · 每批 7 个账号（X 搜索 query 长度有限，太多会失效）
  · 4 类各一批，共 4 批
  · 每批滚动加载 + 精确解析互动数
  · 结果合并去重，按账号分类打 tier

用法：
  python tools_x_multi.py              # 全部 4 批
  python tools_x_multi.py --tier macro # 只跑某一类
  python tools_x_multi.py --json
"""

from __future__ import annotations

import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(r"D:\DP workspace\btc_system")
sys.path.insert(0, str(ROOT))

OUT = ROOT / "data" / "x_posts.json"
LOG = ROOT / "data" / "x_log.jsonl"

# 用户清单分四类，每类控制在 7 个以内以适配 query 长度
TIERS = {
    "core": [           # 加密核心人物与项目方
        "VitalikButerin", "cz_binance", "saylor",
        "brian_armstrong", "justinsuntron",
    ],
    "news": [           # 新闻、链上数据与调查
        "WatcherGuru", "zachxbt", "WuBlockchain",
        "lookonchain", "AltcoinDaily",
    ],
    "macro": [          # 宏观分析、比特币与交易视角
        "APompliano", "RaoulGMI", "CryptoHayes",
        "LynAldenContact", "woonomic", "DefiIgnas", "CryptoMichNL",
    ],
    "politic": [        # 政治与监管（与加密高度交叉）
        "elonmusk", "balajis", "EleanorTerrett",
        "nayibbukele", "BRICSinfo", "KillaXBT", "ColeMacro",
        "NickTimiraos",
    ],
}

# 额外的通用检索（捕捉清单外的热点）
RETWEET_QUERY = "$BTC lang:en min_faves:2000"

JS_X = r"""(() => {
  const arts = [];
  document.querySelectorAll('article').forEach(a => {
    const txt = (a.innerText || '').replace(/\s+/g, ' ').trim();
    if (txt.length < 40) return;
    const meta = {};
    a.querySelectorAll('[aria-label]').forEach(el => {
      const lb = el.getAttribute('aria-label') || '';
      const m = lb.match(/^(\d[\d,.]*[KMB]?)\s+(repl|likes?|reposts?|views?)/i);
      if (m) meta[m[2].toLowerCase()] = m[1];
    });
    const t = a.querySelector('time');
    const link = a.querySelector('a[href*="/status/"]');
    arts.push({txt: txt.slice(0, 320), ts: t ? (t.getAttribute('datetime') || '') : '',
               likes: meta.likes || meta.like || '',
               reposts: meta.reposts || meta.repost || '',
               replies: meta.repl || '', views: meta.views || meta.view || '',
               url: link ? link.href : ''});
  });
  return JSON.stringify(arts.slice(0, 10));
})()"""

JS_SCROLL = "(() => { window.scrollBy(0, 2200); return 1; })()"

JS_RETRY = r"""(() => {
  if (/出错了|Try reloading|Something went wrong/i.test(document.body.innerText)) {
    const els = [...document.querySelectorAll('div[role="button"], span, button')];
    const b = els.find(e => /重试|Retry|Try again/i.test(e.innerText || ''));
    if (b) { b.click(); return 'clicked'; }
  }
  return 'ok';
})()"""


def _query(handles: list[str]) -> str:
    return "(" + " OR ".join("from:" + h for h in handles) + ") BTC"


def fetch_batch(handles: list[str], tier: str) -> list[dict]:
    from core import cdp

    q = _query(handles)
    url = ("https://x.com/search?q=" + __import__("urllib.parse", fromlist=["quote"])
           .quote(q) + "&f=live")
    pg = cdp.open_page("https://x.com/home")
    try:
        pg.goto(url, wait=8)
        time.sleep(3)
        arr = []
        for attempt in range(3):
            pg.eval_js(JS_RETRY)
            time.sleep(2)
            raw = pg.eval_js(JS_X)
            arr = json.loads(raw) if raw else []
            if arr:
                pg.eval_js(JS_SCROLL)
                time.sleep(2)
                raw2 = pg.eval_js(JS_X)
                arr2 = json.loads(raw2) if raw2 else []
                if len(arr2) > len(arr):
                    arr = arr2
                break
            time.sleep(3)
        out = []
        for a in arr:
            txt = a.get("txt", "")
            # 识别是哪个账号发的
            who = ""
            for h in handles:
                if "@" + h.lower() in txt.lower():
                    who = h
                    break
            out.append({"tier": tier, "who": who, "text": txt,
                        "likes": a.get("likes", ""), "views": a.get("views", ""),
                        "reposts": a.get("reposts", ""),
                        "ts": a.get("ts", ""), "url": a.get("url", "")})
        return out
    finally:
        pg.close()


def fetch_general() -> list[dict]:
    from core import cdp
    import urllib.parse
    url = ("https://x.com/search?q=" + urllib.parse.quote(RETWEET_QUERY) + "&f=live")
    pg = cdp.open_page("https://x.com/home")
    try:
        pg.goto(url, wait=8)
        time.sleep(3)
        for _ in range(2):
            pg.eval_js(JS_RETRY)
            time.sleep(2)
            raw = pg.eval_js(JS_X)
            arr = json.loads(raw) if raw else []
            if arr:
                pg.eval_js(JS_SCROLL)
                time.sleep(2)
                raw2 = pg.eval_js(JS_X)
                arr2 = json.loads(raw2) if raw2 else []
                if len(arr2) > len(arr):
                    arr = arr2
                return [{"tier": "hot", "who": "", "text": a.get("txt", ""),
                         "likes": a.get("likes", ""), "views": a.get("views", ""),
                         "reposts": a.get("reposts", ""), "ts": a.get("ts", ""),
                         "url": a.get("url", "")} for a in arr]
            time.sleep(3)
        return []
    finally:
        pg.close()


def main():
    args = sys.argv[1:]
    only = None
    if "--tier" in args:
        try:
            only = args[args.index("--tier") + 1]
        except Exception:
            pass

    allp: list[dict] = []
    for tier, handles in TIERS.items():
        if only and tier != only:
            continue
        try:
            ps = fetch_batch(handles, tier)
            print("  %-8s %d 条" % (tier, len(ps)))
            allp.extend(ps)
        except Exception as e:
            print("  %-8s 失败: %s" % (tier, str(e)[:60]))

    if not only:
        try:
            hot = fetch_general()
            print("  %-8s %d 条" % ("hot", len(hot)))
            allp.extend(hot)
        except Exception as e:
            print("  hot 失败:", str(e)[:60])

    # 去重（同 URL 或同前 60 字）
    seen, uniq = set(), []
    for p in allp:
        k = p.get("url") or p.get("text", "")[:60]
        if k in seen:
            continue
        seen.add(k)
        uniq.append(p)

    payload = [{"tier": p["tier"], "user": p.get("who") or "unknown",
                "age": (p.get("ts") or "")[:16],
                "likes": p.get("likes", ""), "views": p.get("views", ""),
                "text": p.get("text", "")[:240], "text_cn": "",
                "url": p.get("url", "")} for p in uniq]

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "captured_at": int(time.time() * 1000),
        "note": "tools_x_multi.py（25+ 账号分层检索）",
        "count": len(payload), "posts": payload,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    # 存档
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            for p in payload:
                f.write(json.dumps({"ts": datetime.now().strftime("%Y-%m-%d %H:%M"),
                                    "txt": p["text"][:200], "user": p["user"],
                                    "tier": p["tier"]}, ensure_ascii=False) + "\n")
    except Exception:
        pass

    if "--json" in args:
        print(json.dumps(payload, ensure_ascii=False, indent=1)[:3000])
        return 0

    print()
    print("=== X 合并结果 %d 条（去重后）===" % len(payload))
    for p in payload[:14]:
        print("  [%-7s] @%-16s %s" % (p["tier"], p["user"][:16], p["text"][:80]))
    print()
    print("  已写入", OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
