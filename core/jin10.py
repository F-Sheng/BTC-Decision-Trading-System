"""金十数据快讯：宏观/政策/商品的实时中文快讯源。

API（公开、免密钥）：https://flash-api.jin10.com/get_flash_list
默认频道 -8200 是全量快讯，需要按宏观关键词过滤才有价值。
"""
from __future__ import annotations

import json
import re
import time
import urllib.request
from dataclasses import dataclass, field

from .utils import ROOT, read_json, write_json

CACHE = ROOT / "state" / "jin10.json"
TTL = 300
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
URL = "https://flash-api.jin10.com/get_flash_list?channel=-8200&vip=1"

# 与 BTC 定价相关的宏观/商品/政策关键词
KW_HIGH = ["美联储", "鲍威尔", "沃什", "fomc", "加息", "降息", "利率决议",
           "cpi", "通胀", "非农", "失业", "gdp", "pce"]
KW_MID = ["特朗普", "关税", "中期选举", "参议院", "众议院", "白宫",
          "原油", "opec", "伊朗", "以色列", "中东", "霍尔木兹",
          "比特币", "加密", "etf", "美元指数", "黄金", "国债", "收益率",
          "制裁", "战争", "停火"]
NEG = ["加息", "通胀超", "高于预期", "制裁", "战争", "袭击", "下跌", "暴跌",
       "抛售", "流出", "违约", "衰退", "封锁", "冲突升级"]
POS = ["降息", "低于预期", "停火", "达成协议", "流入", "上涨", "批准",
       "缓和", "刺激", "回升"]


@dataclass
class Flash:
    lst: list = field(default_factory=list)   # [{"time","text","level"}]
    high: int = 0
    mid: int = 0
    score: int = 0
    updated: int = 0

    def summary(self, n: int = 5) -> str:
        if not self.lst:
            return "金十快讯=NA"
        head = " | ".join(f"{x['time']} {x['text'][:34]}" for x in self.lst[:n])
        return f"金十 {len(self.lst)}条(高{self.high}/中{self.mid}) 情绪{self.score:+d} :: {head}"


def _clean(s: str) -> str:
    s = re.sub(r"<[^>]+>", "", s or "")
    s = s.replace("&nbsp;", " ").replace("\u3000", " ")
    return re.sub(r"\s+", " ", s).strip()


def fetch(force: bool = False) -> Flash:
    cache = read_json(CACHE, {}) or {}
    if not force and cache.get("_t") and time.time() - cache["_t"] < TTL:
        return Flash(**{k: cache["d"].get(k) for k in Flash.__dataclass_fields__})

    out = Flash(updated=int(time.time()))
    try:
        req = urllib.request.Request(URL, headers={
            "User-Agent": UA, "x-app-id": "bVBF4FyRTn5NJF5n",
            "x-version": "1.0.0", "Referer": "https://www.jin10.com/"})
        with urllib.request.urlopen(req, timeout=15) as r:
            d = json.loads(r.read().decode())
    except Exception:
        d = {}

    pos_n = neg_n = 0
    for it in (d.get("data") or [])[:200]:
        dd = it.get("data") or {}
        txt = _clean(str(dd.get("content") or dd.get("title") or ""))
        if not txt or len(txt) < 8:
            continue
        low = txt.lower()
        level = 2 if any(k in low for k in KW_HIGH) else (
            1 if any(k in low for k in KW_MID) else 0)
        if level == 0:
            continue
        t = ""
        try:
            t = time.strftime("%H:%M", time.localtime(int(it.get("time") or 0)))
        except Exception:
            t = str(it.get("time") or "")[-8:-3]
        out.lst.append({"time": t, "text": txt[:120], "level": level})
        if level == 2:
            out.high += 1
        else:
            out.mid += 1
        pos_n += sum(1 for w in POS if w in txt)
        neg_n += sum(1 for w in NEG if w in txt)

    out.lst = out.lst[:30]
    total = pos_n + neg_n
    out.score = 0 if total == 0 else max(-2, min(2, round((pos_n - neg_n) / max(total, 1) * 2)))

    write_json(CACHE, {"_t": time.time(), "d": {
        "lst": out.lst, "high": out.high, "mid": out.mid,
        "score": out.score, "updated": out.updated}})
    return out


def headline_objects(f: Flash) -> list[dict]:
    """转成 events.py 能吃的标题格式，便于并入现有 NLP 管道。"""
    now = time.time()
    return [{"source": "金十", "title": x["text"], "ts": now - i * 60}
            for i, x in enumerate(f.lst[:20])]
