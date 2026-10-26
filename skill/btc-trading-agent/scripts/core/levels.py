"""技术面支撑/压力位（标准算法，可复现）。

不用拍脑袋的权重。只用三种业内通用的方法：

1. **枢轴点 Pivot Points**（TradingView / 欧易默认的 "Pivot Points Standard"）
   由上一周期的 高/低/收 算出：
       PP = (H + L + C) / 3
       R1 = 2PP - L        S1 = 2PP - H
       R2 = PP + (H - L)   S2 = PP - (H - L)
       R3 = H + 2(PP - L)  S3 = L - 2(H - PP)
   日线和周线各算一套。

2. **成交量分布 Volume Profile**（机构常用）
   把区间价格分桶，累计成交量：
       POC = 成交量最大的价位（最集中的成交区，天然支撑/压力）
       VAH / VAL = 70% 成交量所在区间的上下沿

3. **分形摆动点 Fractal Swings**（Bill Williams，已在 market.py 里）
   取多周期的确认摆动高低点。

布林带不再当"压力位"——它是波动率指标，单独归为"波动带"。
清算簇、整数关口也不是技术位，单独归类，不混进技术位排序。
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class TechLevel:
    price: float
    kind: str        # pivot / vp / swing
    name: str
    weight: float    # 仅用于同价区合并时的优先显示，不代表"重要性"


def pivot_points(h: float, l: float, c: float) -> dict[str, float]:
    """经典枢轴点。输入上一周期的 高/低/收。"""
    if not (h > 0 and l > 0 and c > 0) or h < l:
        return {}
    pp = (h + l + c) / 3
    rng = h - l
    return {
        "PP": pp,
        "R1": 2 * pp - l,
        "S1": 2 * pp - h,
        "R2": pp + rng,
        "S2": pp - rng,
        "R3": h + 2 * (pp - l),
        "S3": l - 2 * (h - pp),
    }


def _period_hlc(df: pd.DataFrame, rule: str) -> tuple[float, float, float] | None:
    """把 K 线按周期聚合，取**上一个完整周期**的 (高, 低, 收)。"""
    if df is None or len(df) < 5:
        return None
    d = df.copy()
    if not isinstance(d.index, pd.DatetimeIndex):
        try:
            d.index = pd.to_datetime(d["ts"], unit="ms", utc=True)
        except Exception:
            return None
    try:
        g = d.resample(rule).agg({"high": "max", "low": "min", "close": "last"}).dropna()
    except Exception:
        return None
    if len(g) < 2:
        return None
    prev = g.iloc[-2]          # 上一完整周期（避开当前未走完的）
    return float(prev["high"]), float(prev["low"]), float(prev["close"])


def volume_profile(df: pd.DataFrame, bins: int = 60,
                   value_area: float = 0.70) -> tuple[float, float, float] | None:
    """成交量分布 → (POC, VAH, VAL)。

    用每根 K 线 (high+low+close)/3 作为成交价，成交量（或成交额）分配到所在桶。
    """
    if df is None or len(df) < 20:
        return None
    h = df["high"].values.astype(float)
    l = df["low"].values.astype(float)
    c = df["close"].values.astype(float)
    v = df["vol"].values.astype(float) if "vol" in df.columns else np.ones(len(df))
    tp = (h + l + c) / 3.0

    lo, hi = float(np.nanmin(l)), float(np.nanmax(h))
    if not (hi > lo > 0):
        return None
    edges = np.linspace(lo, hi, bins + 1)
    idx = np.clip(np.digitize(tp, edges) - 1, 0, bins - 1)
    hist = np.zeros(bins)
    np.add.at(hist, idx, v)

    centers = (edges[:-1] + edges[1:]) / 2
    poc_i = int(np.argmax(hist))
    poc = float(centers[poc_i])

    # 从 POC 向两侧扩张，直到覆盖 value_area 的成交量
    total = hist.sum()
    if total <= 0:
        return None
    acc = hist[poc_i]
    lo_i = hi_i = poc_i
    while acc < total * value_area and (lo_i > 0 or hi_i < bins - 1):
        left = hist[lo_i - 1] if lo_i > 0 else -1
        right = hist[hi_i + 1] if hi_i < bins - 1 else -1
        if right >= left:
            hi_i += 1
            acc += hist[hi_i]
        else:
            lo_i -= 1
            acc += hist[lo_i]
    return poc, float(centers[hi_i]), float(centers[lo_i])


def build(dfs: dict, price: float, swing_fn=None,
          lookback_bars: int = 500) -> tuple[list[TechLevel], list[TechLevel]]:
    """返回 (技术压力位, 技术支撑位)，全部为确认过的标准技术位。"""
    out: list[TechLevel] = []
    h4 = dfs.get("4h")
    h1 = dfs.get("1h")

    # ---- 1. 枢轴点：日线 + 周线 ----
    for tf, rule, label in ((h1, "1D", "日线"), (h4, "W-MON", "周线")):
        if tf is None:
            continue
        hlc = _period_hlc(tf, rule)
        if not hlc:
            continue
        pp = pivot_points(*hlc)
        for k, v in pp.items():
            if v <= 0:
                continue
            out.append(TechLevel(v, "pivot", f"{label}枢轴{k}", 1.4))

    # ---- 2. 成交量分布（4 小时，取最近 500 根 ≈ 83 天）----
    if h4 is not None and len(h4) > 60:
        vp = volume_profile(h4.tail(lookback_bars))
        if vp:
            poc, vah, val = vp
            out.append(TechLevel(poc, "vp", "成交量POC", 1.5))
            out.append(TechLevel(vah, "vp", "成交量VAH", 1.45))
            out.append(TechLevel(val, "vp", "成交量VAL", 1.45))

    # ---- 3. 分形摆动点（多周期，已确认）----
    if swing_fn and h4 is not None and h1 is not None:
        for name, df, w in (("4h", h4, 1.3), ("1h", h1, 1.2)):
            try:
                highs, lows = swing_fn(df, 3, 3)
            except Exception:
                continue
            for _, p in highs[-4:]:
                out.append(TechLevel(float(p), "swing", f"{name}摆动高", w))
            for _, p in lows[-4:]:
                out.append(TechLevel(float(p), "swing", f"{name}摆动低", w))

    res = [x for x in out if x.price > price * 1.0005]
    sup = [x for x in out if x.price < price * 0.9995]
    return res, sup


def merge_zones(levels: list[TechLevel], price: float,
                merge_pct: float = 0.004, n: int = 6) -> list[TechLevel]:
    """把相距很近的位合并成"区域"，按区域内的位数量（共振）排序。"""
    if not levels:
        return []
    pts = sorted(levels, key=lambda z: abs(z.price - price))
    zones: list[list[TechLevel]] = []
    for x in pts:
        if zones and abs(x.price - zones[-1][0].price) / price <= merge_pct:
            zones[-1].append(x)
        else:
            zones.append([x])
    out = []
    for z in zones:
        strong = max(z, key=lambda v: v.weight)
        # 共振：同一区域里来自不同方法的位越多，越可靠
        kinds = {v.kind for v in z}
        names = "、".join(sorted({v.name for v in z}))[:24]
        out.append(TechLevel(strong.price, "+".join(sorted(kinds)), names,
                             float(len(z)) + strong.weight * 0.1))
    out.sort(key=lambda v: -v.weight)
    return out[:n]
