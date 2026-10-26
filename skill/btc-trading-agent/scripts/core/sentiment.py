"""市场交易情绪（复合指标）。

五个来源，全部公开可验证；X(Twitter) 因未登录且抓取不稳，不纳入。

1. 恐慌贪婪指数   alternative.me       每日     0-100
2. CoinGecko 情绪投票                10 分钟  看多占比 %
3. StockTwits BTC.X 交易员标记        10 分钟  多/空条数
4. 金十快讯情绪                       5 分钟   -2..+2
5. CoinGlass 情绪投票（来自 Chrome 刷新）  2 小时  5 档分布

综合分 -3..+3：正=乐观（对空单不利），负=悲观（对空单有利，但极度悲观可能反转）
"""
from __future__ import annotations

import json
import time
import urllib.request
from dataclasses import dataclass, field

from .utils import ROOT, read_json, safe_div, write_json

CACHE = ROOT / "state" / "sentiment.json"
TTL = 600
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")


def _get(url: str, timeout: int = 15):
    req = urllib.request.Request(url, headers={"User-Agent": UA,
                                               "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


@dataclass
class Sentiment:
    fng_value: int | None = None
    fng_label: str = ""
    gecko_up_pct: float | None = None
    st_bull: int = 0
    st_bear: int = 0
    jin10_score: int = 0
    cg_poll: dict = field(default_factory=dict)   # {"very_bull":17,...}
    x_score: int = 0
    x_n: int = 0
    x_pro_score: float = 0.0
    x_pro_n: int = 0
    x_retail_score: float = 0.0
    x_retail_n: int = 0
    score: int = 0
    notes: list = field(default_factory=list)

    def summary(self) -> str:
        st = f"ST{self.st_bull}/{self.st_bear}"
        cg = ""
        if self.cg_poll:
            cg = f" CG投票 极多{self.cg_poll.get('very_bull',0)}%"
        return (f"恐贪{self.fng_value}({self.fng_label}) "
                f"Gecko看多{(self.gecko_up_pct or 0):.0f}% {st} "
                f"金十{self.jin10_score:+d} 推特专业{self.x_pro_score:+.2f} 散户{self.x_retail_score:+.2f}{cg}"
                f" → 情绪{self.score:+d}")


def _fng() -> tuple[int | None, str]:
    try:
        d = _get("https://api.alternative.me/fng/?limit=1")
        it = d["data"][0]
        return int(it["value"]), str(it["value_classification"])
    except Exception:
        return None, ""


def _gecko() -> float | None:
    try:
        d = _get("https://api.coingecko.com/api/v3/coins/bitcoin"
                 "?localization=false&tickers=false&market_data=false&community_data=false")
        return float(d.get("sentiment_votes_up_percentage") or 0) or None
    except Exception:
        return None


def _stocktwits() -> tuple[int, int]:
    try:
        d = _get("https://api.stocktwits.com/api/2/streams/symbol/BTC.X.json")
        bull = bear = 0
        for m in d.get("messages", []):
            ent = m.get("entities") or {}
            sent = (ent.get("sentiment") or {})
            b = sent.get("basic")
            if b == "Bullish":
                bull += 1
            elif b == "Bearish":
                bear += 1
        return bull, bear
    except Exception:
        return 0, 0


def load(jin10_score: int = 0, cg_poll: dict | None = None,
         force: bool = False, cfg: dict | None = None) -> Sentiment:
    cache = read_json(CACHE, {}) or {}
    if not force and cache.get("_t") and time.time() - cache["_t"] < TTL:
        d = cache["d"]
        s = Sentiment(**{k: d.get(k) for k in Sentiment.__dataclass_fields__})
        s.x_n = int(s.x_n or 0)
        s.x_score = int(s.x_score or 0)
        s.x_pro_n = int(s.x_pro_n or 0)
        s.x_retail_n = int(s.x_retail_n or 0)
        s.x_pro_score = float(s.x_pro_score or 0)
        s.x_retail_score = float(s.x_retail_score or 0)
        s.jin10_score = int(s.jin10_score or 0)
        # 每次用最新传入的
        if jin10_score:
            s.jin10_score = jin10_score
        if cg_poll:
            s.cg_poll = cg_poll
        s.score = _composite(s)
        return s

    s = Sentiment(jin10_score=jin10_score, cg_poll=cg_poll or {})
    s.fng_value, s.fng_label = _fng()
    s.gecko_up_pct = _gecko()
    s.st_bull, s.st_bear = _stocktwits()
    try:
        from . import x_sentiment
        xv = x_sentiment.load(cfg or {})
        s.x_score, s.x_n = xv.score, xv.n
        s.x_pro_score, s.x_pro_n = xv.pro_score, xv.pro_n
        s.x_retail_score, s.x_retail_n = xv.retail_score, xv.retail_n
    except Exception:
        pass
    s.score = _composite(s)

    write_json(CACHE, {"_t": time.time(), "d": {
        k: getattr(s, k) for k in Sentiment.__dataclass_fields__}})
    return s


def _composite(s: Sentiment) -> int:
    """-3..+3。正=市场乐观，负=市场悲观。"""
    v = 0.0
    n = 0

    if s.fng_value is not None:
        v += (s.fng_value - 50) / 25.0     # 0→0, 75→+1, 25→-1
        n += 1
        if s.fng_value >= 75:
            s.notes.append(f"恐贪{s.fng_value}极度贪婪")
        elif s.fng_value <= 25:
            s.notes.append(f"恐贪{s.fng_value}极度恐惧")

    if s.gecko_up_pct is not None:
        v += (s.gecko_up_pct - 50) / 25.0
        n += 1

    tot = s.st_bull + s.st_bear
    if tot >= 3:
        v += ((s.st_bull - s.st_bear) / tot) * 1.5
        n += 1

    if s.jin10_score:
        v += s.jin10_score * 0.8
        n += 1

    # 推特分两层：专业号小权重正向；散户号按**反向指标**，只在中性以外才发声
    if s.x_pro_n >= 2:
        v += s.x_pro_score * 0.55
        n += 0.55
        if abs(s.x_pro_score) >= 0.6:
            s.notes.append(f"专业交易者{'偏多' if s.x_pro_score > 0 else '偏空'}")
    if s.x_retail_n >= 2:
        if s.x_retail_score >= 0.5:        # 散户极度看多 → 反向利空
            v -= 0.85
            n += 0.55
            s.notes.append("散户集体看多，按反向指标处理")
        elif s.x_retail_score <= -0.5:     # 散户极度看空 → 反向利多
            v += 0.85
            n += 0.55
            s.notes.append("散户集体看空，按反向指标处理")
        else:                               # 中间态几乎不投票
            n += 0.2

    if s.cg_poll:
        vb = float(s.cg_poll.get("very_bull", 0))
        vbe = float(s.cg_poll.get("very_bear", 0))
        if vb or vbe:
            v += (vb - vbe) / 20.0
            n += 1

    if n == 0:
        return 0
    return max(-3, min(3, round(v / n * 1.6)))
