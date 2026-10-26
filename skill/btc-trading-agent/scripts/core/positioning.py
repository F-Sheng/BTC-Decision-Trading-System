"""持仓结构层：散户 vs 大户（机构代理）多空比、资金费率、持仓量变化。

数据源全部为公开可验证接口：
- Binance futures/data/globalLongShortAccountRatio  散户账户多空比
- Binance futures/data/topLongShortPositionRatio    大户持仓多空比
- Binance futures/data/openInterestHist             持仓量历史（算 24h 变化）
- Binance futures/data/takerlongshortRatio          主动买卖比
- OKX  funding-rate                                 资金费率

评分逻辑（逆向 + 顺势混合）：
  1) 拥挤度差 = 散户多头占比 − 大户多头占比
     散户比大户更看多 → 风险（-1）；更看空 → 机会（+1）
  2) 绝对拥挤：散户 >70% 做多 → -1；<30% → +1
  3) 资金费率：>0.01%/8h → -1（多头付费过重）；<0 → +1（空头付费）
  4) 持仓量 + 价格方向：OI 增 & 价跌 → 空头加仓(-1)；OI 增 & 价涨 → 多头加仓(+1)
"""
from __future__ import annotations

import json
import time
import urllib.request
from dataclasses import dataclass, field

from .utils import ROOT, read_json, safe_div, write_json

BINANCE = "https://fapi.binance.com/futures/data/"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
CACHE = ROOT / "state" / "positioning.json"
TTL = 300          # 5 分钟


def _get(path: str, params: dict, timeout: int = 15):
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    req = urllib.request.Request(f"{BINANCE}{path}?{qs}",
                                 headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


@dataclass
class Positioning:
    retail_long_pct: float | None = None      # 散户多头占比 %
    top_long_pct: float | None = None         # 大户持仓多头占比 %
    top_account_long_pct: float | None = None # 大户账户多头占比 %
    divergence: float | None = None           # 散户 − 大户（百分点）
    oi_change_pct: float | None = None        # 24h 持仓量变化 %
    price_change_pct: float | None = None     # 24h 价格变化 %
    taker_ratio: float | None = None          # 主动买/卖
    funding: float | None = None              # 8h 资金费率（小数）
    score: int = 0
    reasons: list[str] = field(default_factory=list)
    ts: int = 0

    def summary(self) -> str:
        def fmt(v, unit="", mul=1.0, nd=2):
            if v is None:
                return "NA"
            return f"{v * mul:.{nd}f}{unit}"

        fund_pct = None if self.funding is None else self.funding * 100
        return (
            f"散户多{fmt(self.retail_long_pct, '%')} "
            f"大户多{fmt(self.top_long_pct, '%')} "
            f"背离{fmt(self.divergence, 'pt')} "
            f"OI24h{fmt(self.oi_change_pct, '%')} "
            f"主动买卖{fmt(self.taker_ratio)} "
            f"费率{fmt(fund_pct, '%', nd=4)}"
            f" → 评分{self.score:+d}"
        )


def fetch(funding: float | None = None) -> Positioning:
    """拉取并缓存（5 分钟）。funding 由调用方从 OKX 传入。"""
    cache = read_json(CACHE, {}) or {}
    if cache and time.time() - cache.get("_t", 0) < TTL:
        d = {k: v for k, v in cache.items() if k != "_t"}
        p = Positioning(**{k: d.get(k) for k in Positioning.__dataclass_fields__})
        p.funding = funding if funding is not None else p.funding
        return p

    p = Positioning(ts=int(time.time() * 1000), funding=funding)

    # 1) 散户账户多空比
    try:
        d = _get("globalLongShortAccountRatio",
                 {"symbol": "BTCUSDT", "period": "1h", "limit": "1"})
        p.retail_long_pct = float(d[0]["longAccount"]) * 100
    except Exception:
        pass

    # 2) 大户持仓多空比
    try:
        d = _get("topLongShortPositionRatio",
                 {"symbol": "BTCUSDT", "period": "1h", "limit": "1"})
        p.top_long_pct = float(d[0]["longAccount"]) * 100
    except Exception:
        pass

    # 3) 大户账户多空比
    try:
        d = _get("topLongShortAccountRatio",
                 {"symbol": "BTCUSDT", "period": "1h", "limit": "1"})
        p.top_account_long_pct = float(d[0]["longAccount"]) * 100
    except Exception:
        pass

    # 4) 持仓量 24h 变化
    try:
        d = _get("openInterestHist",
                 {"symbol": "BTCUSDT", "period": "1h", "limit": "24"})
        if len(d) >= 2:
            a, b = float(d[0]["sumOpenInterest"]), float(d[-1]["sumOpenInterest"])
            p.oi_change_pct = safe_div(b - a, a) * 100
    except Exception:
        pass

    # 5) 主动买卖比
    try:
        d = _get("takerlongshortRatio",
                 {"symbol": "BTCUSDT", "period": "1h", "limit": "1"})
        p.taker_ratio = float(d[0]["buySellRatio"])
    except Exception:
        pass

    if p.retail_long_pct is not None and p.top_long_pct is not None:
        p.divergence = p.retail_long_pct - p.top_long_pct

    # ---- 评分 ----
    s = 0
    if p.divergence is not None:
        if p.divergence >= 8:
            s -= 1
            p.reasons.append(f"散户比大户看多{p.divergence:.1f}pt（拥挤）")
        elif p.divergence <= -8:
            s += 1
            p.reasons.append(f"散户比大户看空{-p.divergence:.1f}pt（错杀）")

    if p.retail_long_pct is not None:
        if p.retail_long_pct >= 70:
            s -= 1
            p.reasons.append(f"散户多头{p.retail_long_pct:.0f}%极拥挤")
        elif p.retail_long_pct <= 30:
            s += 1
            p.reasons.append(f"散户多头仅{p.retail_long_pct:.0f}%（投降）")

    if p.funding is not None:
        if p.funding >= 0.0003:      # 0.03%/8h（基准 0.01% 的 3 倍）才算偏高
            s -= 1
            p.reasons.append(f"资金费率{p.funding*100:.4f}%偏高")
        elif p.funding <= 0:
            s += 1
            p.reasons.append(f"资金费率{p.funding*100:.4f}%转负")

    if p.oi_change_pct is not None and p.price_change_pct is not None:
        if p.oi_change_pct > 1 and p.price_change_pct < 0:
            s -= 1
            p.reasons.append("价跌仓增（空头加仓）")
        elif p.oi_change_pct > 1 and p.price_change_pct > 0:
            s += 1
            p.reasons.append("价涨仓增（多头加仓）")

    p.score = max(-3, min(3, s))

    write_json(CACHE, {**{k: getattr(p, k) for k in Positioning.__dataclass_fields__},
                       "_t": time.time()})
    return p
