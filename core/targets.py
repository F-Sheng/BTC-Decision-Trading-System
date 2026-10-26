"""止盈目标：从真实市场结构算，而不是拍 R 倍数。

一个做空单的目标价候选池（做多镜像）：
  1. **前低**（多周期分形摆动低点）—— 最直接的支撑
  2. **枢轴点** S1/S2/S3 —— 标准技术位
  3. **成交量分布** VAL / POC —— 机构成本区
  4. **真实爆仓热区**（OKX 强平流）—— 多头被强平的价位，天然磁吸
  5. **斐波那契扩展**（1.272 / 1.618）—— 用最近一段摆动的幅度推

选法：
  · 只保留距入场 0.5R ~ 4R 之间的候选
  · 相距 <0.35% 的合并成"区域"，**区域里来自不同方法的位越多越优先**（共振）
  · 取前 3 个，保证距离递增
  · 不足 3 个就用 R 倍数补齐

R 倍数不再是主角，只作为"下限/上限"和兜底。
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass
class Cand:
    price: float
    kind: str       # swing / pivot / vp / liq / fib
    label: str


def _swings(df: pd.DataFrame, left: int = 3, right: int = 3):
    if df is None or len(df) < 20:
        return [], []
    h, l = df["high"].values, df["low"].values
    n = len(df)
    highs, lows = [], []
    for i in range(left, n - right):
        if h[i] == max(h[i - left:i + right + 1]):
            highs.append(float(h[i]))
        if l[i] == min(l[i - left:i + right + 1]):
            lows.append(float(l[i]))
    return highs[-6:], lows[-6:]


def _fib(highs: list[float], lows: list[float], entry: float, is_short: bool) -> list[Cand]:
    """用最近一段摆动推斐波那契扩展。"""
    if not highs or not lows:
        return []
    hi, lo = max(highs), min(lows)
    if hi <= lo:
        return []
    leg = hi - lo
    out = []
    for ratio, name in ((1.272, "1.272"), (1.618, "1.618")):
        p = (lo - leg * (ratio - 1)) if is_short else (hi + leg * (ratio - 1))
        if p > 0:
            out.append(Cand(p, "fib", f"斐波那契扩展{name}"))
    return out


def build(side: str, entry: float, stop: float, price: float,
          dfs: dict, pivots: dict | None = None,
          vp: tuple | None = None, liq_zones: list | None = None,
          cfg: dict | None = None) -> tuple[list[float], list[str]]:
    """返回 (三档目标价, 说明)。"""
    cfg = cfg or {}
    is_short = (side == "short")
    risk = abs(stop - entry) or entry * 0.03
    lo_r, hi_r = 0.5, 4.0

    cands: list[Cand] = []

    # 1. 前低 / 前高
    for name, df, w in (("15m", dfs.get("15m"), 1), ("1h", dfs.get("1h"), 2),
                        ("4h", dfs.get("4h"), 3)):
        highs, lows = _swings(df)
        if is_short:
            for p in lows:
                cands.append(Cand(p, "swing", f"{name}前低"))
        else:
            for p in highs:
                cands.append(Cand(p, "swing", f"{name}前高"))
        if name == "1h":
            cands.extend(_fib(highs, lows, entry, is_short))

    # 2. 枢轴点
    if pivots:
        keys = ("S1", "S2", "S3") if is_short else ("R1", "R2", "R3")
        for k in keys:
            v = pivots.get(k)
            if v:
                cands.append(Cand(float(v), "pivot", f"枢轴{k}"))

    # 3. 成交量分布
    if vp:
        poc, vah, val = vp
        if is_short:
            cands.append(Cand(float(val), "vp", "成交量VAL"))
            cands.append(Cand(float(poc), "vp", "成交量POC"))
        else:
            cands.append(Cand(float(vah), "vp", "成交量VAH"))
            cands.append(Cand(float(poc), "vp", "成交量POC"))

    # 4. 真实爆仓热区
    for z in (liq_zones or []):
        try:
            p = float(z[0]) if isinstance(z, (list, tuple)) else float(z)
        except Exception:
            continue
        cands.append(Cand(p, "liq", "真实爆仓热区"))

    # 过滤：方向正确 + 距离在 0.5R~4R
    filt = []
    for c in cands:
        d = (entry - c.price) if is_short else (c.price - entry)
        if d <= 0:
            continue
        if not (lo_r * risk <= d <= hi_r * risk):
            continue
        filt.append(c)
    if not filt:
        # 兜底：R 倍数
        mults = cfg.get("tp_r_multiples") or [0.5, 0.9, 1.3]
        sign = -1 if is_short else 1
        tg = [round(entry * (1 + sign * (risk / entry) * m), 1) for m in mults]
        return tg, ["无结构位可用，退回 R 倍数"]

    # 按价格聚合 0.35% 的区域
    filt.sort(key=lambda c: c.price)
    zones: list[list[Cand]] = []
    for c in filt:
        if zones and abs(c.price - zones[-1][0].price) / entry <= 0.0035:
            zones[-1].append(c)
        else:
            zones.append([c])

    scored = []
    for z in zones:
        kinds = {x.kind for x in z}
        rep = max(z, key=lambda x: {"swing": 3, "pivot": 2, "vp": 2, "liq": 2, "fib": 1}[x.kind])
        scored.append((len(kinds) * 10 + len(z), rep.price, rep.label, sorted(kinds)))
    scored.sort(key=lambda t: -t[0])

    picked: list[float] = []
    notes: list[str] = []
    for _, p, label, kinds in scored:
        if len(picked) >= 3:
            break
        if any(abs(p - q) / entry < 0.004 for q in picked):
            continue
        sign = -1 if is_short else 1
        if picked and sign * (p - picked[-1]) <= 0:
            continue
        picked.append(round(p, 1))
        notes.append(f"{label}(共振{len(kinds)})")

    # 补齐到三档
    mults = cfg.get("tp_r_multiples") or [0.5, 0.9, 1.3]
    sign = -1 if is_short else 1
    k = 0
    while len(picked) < 3 and k < len(mults):
        p = round(entry * (1 + sign * (risk / entry) * mults[len(picked)]), 1)
        if not picked or sign * (p - picked[-1]) > 0:
            picked.append(p)
            notes.append(f"R倍数补齐{mults[len(picked)-1]}")
        k += 1
    return picked[:3], notes
