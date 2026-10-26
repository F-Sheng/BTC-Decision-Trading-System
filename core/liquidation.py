"""L2 清算层。

说明（重要）：
- Coinglass 的清算热图无免费 API，网页为 canvas，无法稳定抓取。
- 本模块用「公开可验证数据」构建**代理清算图 (PROXY)**：
  以 1H K 线的成交额 × 参与率 估算各价位堆积的名义持仓，
  按杠杆档位(10/25/50/100)推算强平价，再做时间衰减与价格分桶。
- 结论标注为 PROXY，仅用于判断"上下哪边燃料更厚 / 最近簇距离"，
  不用于精确金额。若配置了 COINGLASS_KEY，可切换为真实数据。
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .utils import safe_div

MMR = 0.005              # 维持保证金率近似
LEVERAGES = (10, 25, 50, 100)
PARTICIPATION = 0.035    # 成交额中转化为新持仓的比例（经验值）
HALF_LIFE_H = 24 * 7     # 7 天半衰


@dataclass
class LiqLevel:
    price: float
    notional: float          # USD
    side: str                # 'short'（在上方） / 'long'（在下方）
    dist_pct: float = 0.0

    def __str__(self) -> str:
        return f"{self.price:,.0f}({self.notional/1e6:.1f}M,{self.dist_pct:+.1f}%)"


@dataclass
class LiqView:
    price: float
    variant: str = "PROXY"
    above: list[LiqLevel] = field(default_factory=list)
    below: list[LiqLevel] = field(default_factory=list)
    total_above_3pct: float = 0.0
    total_below_3pct: float = 0.0
    r_ratio: float = 0.0
    nearest_above: LiqLevel | None = None
    nearest_below: LiqLevel | None = None
    vacuum_above: tuple[float, float] | None = None
    vacuum_below: tuple[float, float] | None = None
    oi_btc: float = 0.0
    oi_usd: float = 0.0
    funding: float = 0.0
    ls_ratio: float = 0.0
    taker_buy_ratio: float = 0.5
    pos: object | None = None        # core.positioning.Positioning（散户/大户/费率/OI）
    senti: object | None = None      # core.sentiment.Sentiment（恐贪/Gecko/StockTwits/金十）

    @property
    def score(self) -> int:
        """+1 短期偏多（上方燃料厚），-1 偏空。"""
        if self.r_ratio >= 1.5:
            return 1
        if self.r_ratio <= 0.67:
            return -1
        return 0

    @property
    def fuel_distance_pct(self) -> float | None:
        """最近的、有意义的簇距现价的距离。"""
        cands = []
        if self.nearest_above:
            cands.append(abs(self.nearest_above.dist_pct))
        if self.nearest_below:
            cands.append(abs(self.nearest_below.dist_pct))
        return min(cands) if cands else None

    def summary(self) -> str:
        na = str(self.nearest_above) if self.nearest_above else "NA"
        nb = str(self.nearest_below) if self.nearest_below else "NA"
        ps = f" | {self.pos.summary()}" if self.pos else ""
        return (f"L2[{self.variant}] R={self.r_ratio:.2f} "
                f"上3%={self.total_above_3pct/1e6:.1f}M 下3%={self.total_below_3pct/1e6:.1f}M "
                f"最近上={na} 最近下={nb} OI={self.oi_btc:,.0f}BTC "
                f"funding={self.funding*100:.4f}% LS={self.ls_ratio:.2f}{ps}")


def build_proxy(candles_1h: list[list], price: float,
                oi_btc: float = 0.0, funding: float = 0.0,
                ls_ratio: float = 0.0, taker_buy_ratio: float = 0.5) -> LiqView:
    """由 1H K 线构建代理清算图。"""
    if not candles_1h or price <= 0:
        return LiqView(price=price, oi_btc=oi_btc, funding=funding, ls_ratio=ls_ratio)

    now_ms = time.time() * 1000
    lo = price * 0.85
    hi = price * 1.15
    nbins = 120
    grid = np.linspace(lo, hi, nbins + 1)
    long_bins = np.zeros(nbins)
    short_bins = np.zeros(nbins)

    for c in candles_1h:
        ts = int(c[0])
        o, h, l, cl = float(c[1]), float(c[2]), float(c[3]), float(c[4])
        vol_q = float(c[7]) if len(c) > 7 and c[7] else float(c[5]) * cl
        vwap = (h + l + cl) / 3.0
        if not (lo < vwap < hi):
            continue

        age_h = max(0.0, (now_ms - ts) / 3.6e6)
        decay = 0.5 ** (age_h / HALF_LIFE_H)
        # 该小时的"新增名义敞口"，按多空比例拆分
        notional = vol_q * PARTICIPATION * decay
        n_long = notional * taker_buy_ratio
        n_short = notional * (1 - taker_buy_ratio)

        for lev in LEVERAGES:
            w = 1.0 / len(LEVERAGES)   # 杠杆分布假设：均分
            liq_long = vwap * (1 - 1.0 / lev + MMR)
            liq_short = vwap * (1 + 1.0 / lev - MMR)
            if lo < liq_long < hi:
                i = np.searchsorted(grid, liq_long) - 1
                if 0 <= i < nbins:
                    long_bins[i] += n_long * w
            if lo < liq_short < hi:
                i = np.searchsorted(grid, liq_short) - 1
                if 0 <= i < nbins:
                    short_bins[i] += n_short * w

    centers = (grid[:-1] + grid[1:]) / 2

    def to_levels(bins: np.ndarray, side: str) -> list[LiqLevel]:
        out = []
        for i, b in enumerate(bins):
            if b <= 0:
                continue
            p = float(centers[i])
            out.append(LiqLevel(price=p, notional=float(b), side=side,
                                dist_pct=(p - price) / price * 100))
        out.sort(key=lambda x: x.notional, reverse=True)
        return out

    all_long = to_levels(long_bins, "long")
    all_short = to_levels(short_bins, "short")

    above = sorted([x for x in all_short if x.price > price], key=lambda x: x.price)
    below = sorted([x for x in all_long if x.price < price], key=lambda x: -x.price)

    tot_above = sum(x.notional for x in above if x.dist_pct <= 3.0)
    tot_below = sum(x.notional for x in below if abs(x.dist_pct) <= 3.0)
    r = safe_div(tot_above, tot_below, 1.0 if tot_below == 0 else 0.0)
    if tot_below == 0:
        r = 9.99

    # 只保留"真墙"（>= 相邻中位数 1.5 倍 且 > 300 万）
    def significant(levels: list[LiqLevel], n: int = 3) -> list[LiqLevel]:
        if not levels:
            return []
        med = float(np.median([x.notional for x in levels])) or 1.0
        keep = [x for x in levels if x.notional >= max(med * 1.5, 3e6)]
        return keep[:n] if keep else levels[:1]

    near_above = significant([x for x in above if x.dist_pct <= 6]) or []
    near_below = significant([x for x in below if abs(x.dist_pct) <= 6]) or []

    v = LiqView(
        price=price, variant="PROXY",
        above=near_above, below=near_below,
        total_above_3pct=tot_above, total_below_3pct=tot_below, r_ratio=r,
        nearest_above=min(near_above, key=lambda x: x.price) if near_above else None,
        nearest_below=max(near_below, key=lambda x: x.price) if near_below else None,
        oi_btc=oi_btc, oi_usd=oi_btc * price, funding=funding,
        ls_ratio=ls_ratio, taker_buy_ratio=taker_buy_ratio,
    )

    # 真空区：上下各找一个"两簇之间"的宽间隙
    v.vacuum_below = _vacuum(sorted(v.below, key=lambda x: -x.price), price)
    v.vacuum_above = _vacuum(sorted(v.above, key=lambda x: x.price), price)
    return v


def _vacuum(levels: list[LiqLevel], ref: float) -> tuple[float, float] | None:
    if len(levels) < 2:
        return None
    best = None
    for a, b in zip(levels, levels[1:]):
        gap = abs(a.price - b.price) / ref * 100
        if gap >= 0.8:
            if best is None or gap > best[2]:
                best = (min(a.price, b.price), max(a.price, b.price), gap)
    return (best[0], best[1]) if best else None
