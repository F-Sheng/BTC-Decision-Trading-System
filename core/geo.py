"""地缘 / 政治 / 事件主题层（L1 的定性部分）。

覆盖用户要求关注的六类宏观因子中"新闻驱动"的四类：
  - 中东局势（战争、封锁、袭击、油价供给）
  - 美国中期选举
  - 特朗普动向 / 政策
  - 美联储 / 监管表态
（原油价格与纳指走 FRED 定量，不在这里）

设计原则：**只用 LLM 做"对给定标题的分类"，绝不让它凭记忆报数字。**
无 LLM 时退化为关键词计数，保证永远有输出。
"""
from __future__ import annotations

import json
import re
import time
import urllib.request
from dataclasses import dataclass, field

from .utils import ROOT, read_json, write_json

CACHE = ROOT / "state" / "geo_cache.json"
TTL = 1800
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

THEMES = {
    "middle_east": {
        "label": "中东局势",
        "kw": ["iran", "israel", "hamas", "hezbollah", "gaza", "hormuz", "houthi",
               "middle east", "airstrike", "ceasefire", "opec", "red sea",
               "伊朗", "以色列", "中东", "霍尔木兹", "停火", "袭击"],
    },
    "election": {
        "label": "美国中期选举",
        "kw": ["midterm", "election", "senate", "house", "congress", "ballot",
               "poll", "swing state", "中期选举", "参议院", "众议院", "民调"],
    },
    "trump": {
        "label": "特朗普动向",
        "kw": ["trump", "white house", "tariff", "executive order", "truth social",
               "特朗普", "白宫", "关税", "行政令"],
    },
    "fed_reg": {
        "label": "美联储/监管",
        "kw": ["federal reserve", "fomc", "powell", "warsh", "rate cut", "rate hike",
               "sec ", "cftc", "regulation", "lawsuit", "etf approval",
               "美联储", "加息", "降息", "监管", "立法"],
    },
}

# 关键词极性（fallback 用）：命中即粗略定向
NEG = ["war", "attack", "strike", "ban", "crackdown", "lawsuit", "escalat",
       "sanction", "tariff", "hawkish", "hike", "outflow", "sell-off", "panic",
       "战争", "袭击", "制裁", "关税", "加息", "抛售", "封锁"]
POS = ["ceasefire", "truce", "deal", "approval", "cut", "dovish", "stimulus",
       "inflow", "reserve", "adopt", "agreement",
       "停火", "协议", "批准", "降息", "刺激", "流入"]


@dataclass
class GeoView:
    themes: dict = field(default_factory=dict)   # name -> {"label","count","score","note"}
    score: int = 0                                # -2..+2
    reason: str = ""
    source: str = "kw"                            # 'llm' / 'kw'
    updated: int = 0

    def summary(self) -> str:
        parts = [f"{v['label']}{v['score']:+d}({v['count']})"
                 for v in self.themes.values() if v["count"]]
        return f"地缘政治={self.score:+d}[{self.source}] " + (" ".join(parts) if parts else "无相关新闻")

    def top_theme(self) -> str:
        if not self.themes:
            return ""
        best = max(self.themes.values(), key=lambda v: v["count"])
        return f"{best['label']}" if best["count"] else ""


def _match(heads: list[dict], kw: list[str]) -> list[str]:
    out = []
    for h in heads:
        t = h["title"].lower()
        if any(k in t for k in kw):
            out.append(h["title"])
    return out


def _kw_score(titles: list[str]) -> int:
    s = 0
    for t in titles:
        tl = t.lower()
        # 约定 -1=利空：负面词降低分数，正面词提高分数
        s -= sum(1 for w in NEG if w in tl)
        s += sum(1 for w in POS if w in tl)
    return max(-1, min(1, s))


def _llm_score(theme: str, titles: list[str], cfg: dict) -> int | None:
    key = cfg.get("deepseek_key")
    if not key or not titles:
        return None
    base = cfg.get("deepseek_base", "https://api.deepseek.com")
    model = cfg.get("deepseek_model", "deepseek-chat")
    listing = "\n".join(f"- {t}" for t in titles[:12])
    prompt = (
        f"下面是关于「{THEMES[theme]['label']}」的加密/宏观新闻标题。\n{listing}\n\n"
        '只依据这些标题，判断该主题对 BTC 未来 1-3 天的方向影响。'
        '输出 JSON：{"score": -1|0|1, "reason": "不超过20字"}。'
        "-1=利空风险资产，0=中性/无关，1=利好。不要使用标题以外的信息。"
    )
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": "你是严格的新闻分类器，只依据给定标题判断，输出 JSON。"},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0, "max_tokens": 120,
        "response_format": {"type": "json_object"},
    }
    req = urllib.request.Request(
        f"{base}/chat/completions", data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                 "User-Agent": UA}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            d = json.loads(r.read().decode())
        v = json.loads(d["choices"][0]["message"]["content"])
        return max(-1, min(1, int(v.get("score", 0) or 0)))
    except Exception:
        return None


def compute(heads: list[dict], cfg: dict, use_llm: bool = True) -> GeoView:
    cache = read_json(CACHE, {}) or {}
    sig = str(hash("|".join(sorted(h["title"] for h in heads[:30]))[:300]))
    if cache.get("sig") == sig and time.time() - cache.get("t", 0) < TTL:
        d = cache["v"]
        return GeoView(**d)

    gv = GeoView(updated=int(time.time()), source="kw")
    for name, meta in THEMES.items():
        hits = _match(heads, meta["kw"])
        if not hits:
            gv.themes[name] = {"label": meta["label"], "count": 0, "score": 0, "note": ""}
            continue
        sc = _llm_score(name, hits, cfg) if use_llm else None
        src = "llm" if sc is not None else "kw"
        if sc is None:
            sc = _kw_score(hits)
        else:
            gv.source = "llm"
        gv.themes[name] = {"label": meta["label"], "count": len(hits),
                           "score": sc, "note": hits[0][:60],
                           "src": src}

    total = sum(v["score"] for v in gv.themes.values())
    gv.score = max(-2, min(2, total))
    gv.reason = gv.summary()

    write_json(CACHE, {"sig": sig, "t": time.time(), "v": {
        "themes": gv.themes, "score": gv.score, "reason": gv.reason,
        "source": gv.source, "updated": gv.updated}})
    return gv
