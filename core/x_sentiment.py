"""X（推特）情绪采样 —— 分层 + 反向。

设计前提（用户指出的关键点）：
  推特上主流是散户与营销号，天然带"诱多诱空"，方向性不可信，但**拥挤度可信**。
  因此分两层处理：
    tier=pro    专业交易员 / 宏观研究员 / 链上分析师  → 小权重正向
    tier=retail 散户与喊单号                        → **反向指标**（极度看多视为利空）

分层依据：handle 白名单。白名单外的账号一律按 retail 处理（宁可低估，不可误信）。
"""
from __future__ import annotations

import json
import time
import urllib.request
from dataclasses import dataclass, field

from .utils import ROOT, read_json, write_json

FILE = ROOT / "data" / "x_posts.json"
CACHE = ROOT / "state" / "x_sentiment.json"
FRESH_SEC = 4 * 3600
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

# 专业账号白名单（宏观 / 交易 / 链上）。未列入的一律当散户。
PRO_HANDLES = {
    "@colemacro", "@killaxbt", "@woonomic", "@raoulgmi", "@nicktimiraos",
    "@elerianm", "@lynladencontact", "@glassnode", "@checkonchain",
    "@tedtalksmacro", "@dylanleclair", "@intheredmacro",
}

BULL = ["long", "buy", "accumulat", "breakout", "ath", "parabolic", "bullish",
        "higher", "moon", "adopt", "treasury", "acquir", "inflow", "bounce",
        "reclaim", "pump", "bottom", "extremely good", "prepared for what"]
BEAR = ["short", "sell", "dump", "crash", "bearish", "lower", "retrace",
        "drawdown", "outflow", "liquidat", "capitulat", "breakdown", "fear",
        "warning", "risk", "weak", "hawkish", "excuse not to buy"]


@dataclass
class XView:
    fresh: bool = False
    age_min: float = 1e9
    n: int = 0
    pro_n: int = 0
    pro_score: float = 0.0
    retail_n: int = 0
    retail_score: float = 0.0
    bull: int = 0
    bear: int = 0
    score: int = 0
    top: list = field(default_factory=list)
    top_pro: list = field(default_factory=list)
    note: str = ""

    def summary(self) -> str:
        if not self.n:
            return "推特=无样本"
        tag = "鲜" if self.fresh else "旧"
        return (f"推特[{tag}{self.age_min:.0f}分] 专业{self.pro_score:+.2f}({self.pro_n}条)"
                f" 散户{self.retail_score:+.2f}({self.retail_n}条,反向)")


def _kw_score(text: str) -> int:
    t = text.lower()
    b = sum(1 for w in BULL if w in t)
    s = sum(1 for w in BEAR if w in t)
    if b == s:
        return 0
    return 1 if b > s else -1


def _llm_scores(texts: list[str], cfg: dict) -> list[int] | None:
    key = (cfg or {}).get("deepseek_key")
    if not key or not texts:
        return None
    base = (cfg or {}).get("deepseek_base", "https://api.deepseek.com")
    model = (cfg or {}).get("deepseek_model", "deepseek-chat")
    listing = "\n".join(f"{i+1}. {t}" for i, t in enumerate(texts[:12]))
    prompt = (
        "判断下列加密交易者发言对 BTC 短线（1-3 天）的方向倾向，并区分"
        "「有事实依据的分析」与「无信息量的喊单」。\n"
        f"{listing}\n\n"
        '只输出 JSON：{"scores": [-1|0|1, ...]}，与条目顺序一致。'
        "有事实支撑的看空/看多才给 ±1，纯喊单或纯情绪给 0。"
    )
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": "你是严格的文本极性分类器，只输出 JSON。"},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0, "max_tokens": 200,
        "response_format": {"type": "json_object"},
    }
    try:
        req = urllib.request.Request(
            f"{base}/chat/completions", data=json.dumps(body).encode(),
            headers={"Authorization": f"Bearer {key}",
                     "Content-Type": "application/json", "User-Agent": UA},
            method="POST")
        with urllib.request.urlopen(req, timeout=40) as r:
            d = json.loads(r.read().decode())
        arr = json.loads(d["choices"][0]["message"]["content"]).get("scores", [])
        return [max(-1, min(1, int(x))) for x in arr][:len(texts)]
    except Exception:
        return None


def _tier(p: dict) -> str:
    h = str(p.get("handle") or "").lower()
    t = str(p.get("tier") or "").lower()
    if t in ("pro", "retail"):
        return t
    return "pro" if h in PRO_HANDLES else "retail"


def _avg(posts: list[dict], scores: list[int]) -> tuple[float, int]:
    """按点赞加权（封顶 5 万），返回 (-1..1, 条数)。"""
    if not posts:
        return 0.0, 0
    tot = wsum = 0.0
    for p, sc in zip(posts, scores):
        w = min(float(p.get("likes") or 1), 50000)
        tot += w * sc
        wsum += w
    return ((tot / wsum) if wsum else 0.0), len(posts)


def load(cfg: dict | None = None, use_llm: bool = True) -> XView:
    raw = read_json(FILE, {}) or {}
    v = XView()
    if not raw or not raw.get("captured_at"):
        return v

    age = time.time() - raw["captured_at"] / 1000
    v.age_min = age / 60
    v.fresh = age < FRESH_SEC
    if age > FRESH_SEC:
        return v

    all_posts = [p for p in (raw.get("posts") or []) if p.get("text")]
    v.n = len(all_posts)
    if not all_posts:
        return v

    texts = [p["text"] for p in all_posts]
    llm = _llm_scores(texts, cfg or {}) if use_llm else None
    if llm is None or len(llm) < len(texts):
        llm = [_kw_score(t) for t in texts]

    # 专业号：用 LLM 判「有事实依据的倾向」；散户号：用语气强度（喊单也算数，
    # 因为我们要的是"散户一边倒的程度"，不是他们说得对不对）。
    scores = []
    for p, t, ls in zip(all_posts, texts, llm):
        scores.append(_kw_score(t) if _tier(p) == "retail" else ls)

    pro = [(p, s) for p, s in zip(all_posts, scores) if _tier(p) == "pro"]
    ret = [(p, s) for p, s in zip(all_posts, scores) if _tier(p) == "retail"]

    v.pro_score, v.pro_n = _avg([p for p, _ in pro], [s for _, s in pro])
    v.retail_score, v.retail_n = _avg([p for p, _ in ret], [s for _, s in ret])

    v.bull = sum(1 for s in scores if s > 0)
    v.bear = sum(1 for s in scores if s < 0)
    # 综合分仅供展示：专业正向、散户反向
    v.score = max(-2, min(2, round(v.pro_score - v.retail_score)))

    def fmt(p):
        return f"{p.get('user','')}：{(p.get('text_cn') or p['text'])[:58]}"

    v.top = [fmt(p) for p, _ in (pro[:2] + ret[:1])]
    v.top_pro = [fmt(p) for p, _ in pro[:3]]
    v.note = v.summary()

    write_json(CACHE, {"t": time.time(), "d": {
        k: getattr(v, k) for k in XView.__dataclass_fields__}})
    return v
