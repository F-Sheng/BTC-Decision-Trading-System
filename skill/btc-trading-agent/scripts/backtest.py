"""模型回测：用历史 15m 数据检验四个入场模型是否有正期望。

方法：
- 逐 bar 构造 TimeframeView（切片最后 120 根，保证形态/量能计算一致）
- 调用与实盘完全相同的 strategy.model_a/b/c/d
- 按结构止损 + 三档止盈逐 bar 模拟，记录 R 倍数
输出：每个模型的样本数、胜率、平均 R、总 R。
"""
from __future__ import annotations

import sys
import os
from dataclasses import dataclass

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import market, strategy
from core.market import TimeframeView, enrich, to_df
from core.okx import OkxClient
from core.scoring import ScoreCard
from core.utils import read_json, ROOT


def build_view_at(df_all: pd.DataFrame, i: int, name: str = "15m",
                  win: int = 160) -> TimeframeView:
    lo = max(0, i - win + 1)
    seg = df_all.iloc[lo:i + 1].copy()
    last, prev = seg.iloc[-1], seg.iloc[-2]
    # 摆动点在全量序列上算，再截取到 <= i
    return TimeframeView(
        name=name, price=float(last["close"]),
        ma5=float(last["ma5"]), ma10=float(last["ma10"]), ma20=float(last["ma20"]),
        macd_hist=float(last["macd_hist"]), macd_hist_prev=float(prev["macd_hist"]),
        rsi=float(last["rsi14"]),
        bb_mid=float(last["bb_mid"]), bb_up=float(last["bb_up"]), bb_low=float(last["bb_low"]),
        atr=float(last["atr14"]),
        swing_highs=_swings(df_all, i, "high", 3, 3)[-4:],
        swing_lows=_swings(df_all, i, "low", 3, 3)[-4:],
        df=seg,
    )


def _swings(df: pd.DataFrame, i: int, col: str, left: int, right: int):
    out = []
    hi = df[col].values
    for j in range(max(left, i - 200), i - right):
        w = hi[j - left:j + right + 1]
        if len(w) < left + right + 1:
            continue
        if col == "high" and hi[j] == w.max():
            out.append((j, float(hi[j])))
        if col == "low" and hi[j] == w.min():
            out.append((j, float(hi[j])))
    return out


@dataclass
class Result:
    model: str
    n: int = 0
    wins: int = 0
    total_r: float = 0.0
    tp1: int = 0
    tp2: int = 0
    tp3: int = 0

    @property
    def win_rate(self) -> float:
        return self.wins / self.n if self.n else 0.0

    @property
    def avg_r(self) -> float:
        return self.total_r / self.n if self.n else 0.0

    def __str__(self) -> str:
        return (f"{self.model}: n={self.n:4d} 胜率={self.win_rate*100:5.1f}% "
                f"平均R={self.avg_r:+.2f} 总R={self.total_r:+.1f} "
                f"TP1/2/3={self.tp1}/{self.tp2}/{self.tp3}")


def simulate(df_all: pd.DataFrame, i: int, sig, cfg: dict,
             max_hold: int = 288) -> tuple[float, str]:
    """从 i+1 开始逐 bar 模拟，返回 (R 倍数, 结束原因)。"""
    entry = sig.entry
    stop_pct = max(cfg["stop_pct_min"], min(cfg["stop_pct_max"], sig.structure))
    risk = entry * stop_pct
    if sig.side == "short":
        stop = entry + risk
        tps = [entry - entry * p for p in cfg["tp_pcts"]]
    else:
        stop = entry - risk
        tps = [entry + entry * p for p in cfg["tp_pcts"]]

    filled = [False, False, False]
    realised = 0.0
    w = cfg["tp_weights"]
    remain = 1.0
    end = min(len(df_all) - 1, i + max_hold)

    for j in range(i + 1, end + 1):
        hi = float(df_all["high"].iloc[j])
        lo = float(df_all["low"].iloc[j])

        # 先判止损（保守：同 bar 内先假设止损优先）
        hit_stop = (hi >= stop) if sig.side == "short" else (lo <= stop)
        if hit_stop:
            r_remain = ((entry - stop) / risk) if sig.side == "short" else ((stop - entry) / risk)
            return realised + remain * r_remain, ("SL" if stop != entry else "BE")

        for k in range(3):
            if filled[k]:
                continue
            hit = (lo <= tps[k]) if sig.side == "short" else (hi >= tps[k])
            if hit:
                filled[k] = True
                part = remain if k == 2 else w[k] / max(1e-9, sum(w[k:])) * remain
                r_mult = (abs(entry - tps[k]) / risk)
                realised += part * r_mult
                remain -= part
                if remain <= 1e-9:
                    return realised, f"TP{k+1}"
                if k == 0:
                    stop = entry          # 移保本
    return realised, "TIME"


def main():
    cfg = read_json(ROOT / "config.json", {}) or {}
    c = OkxClient(simulated=True)
    raw = c.candles_bulk(cfg["inst_id"], bar="15m", total=3000, deep=True)
    df = enrich(to_df(raw))
    print(f"样本 {len(df)} 根 15m（约 {len(df)*15/60/24:.0f} 天），"
          f"{df['dt'].iloc[0]} → {df['dt'].iloc[-1]}")

    res = {m: Result(m) for m in ("A", "B", "C", "D")}
    detail = []
    need = 220

    for i in range(need, len(df) - 300):
        v = build_view_at(df, i)
        sc = ScoreCard(l1_allow="range", l2_score=0, l3_aligned="mixed", l3={})
        res_lv, sup_lv = strategy.collect_levels(v, v, _DummyLiq(v.price), v.price)

        for tag, fn, arg in (("A", strategy.model_a, res_lv),
                             ("B", strategy.model_b, sup_lv),
                             ("C", strategy.model_c, sup_lv),
                             ("D", strategy.model_d, res_lv)):
            s = fn(sc, v, arg, cfg)
            if not s:
                continue
            # 与实盘一致：结构距离 > 上限才拒绝；过窄的由风控钳制到 3%
            if s.structure > cfg["stop_pct_max"]:
                continue
            r, why = simulate(df, i, s, cfg)
            rr = res[tag]
            rr.n += 1
            rr.total_r += r
            if r > 0:
                rr.wins += 1
            if why.startswith("TP1"):
                rr.tp1 += 1
            if why.startswith("TP2"):
                rr.tp2 += 1
            if why.startswith("TP3"):
                rr.tp3 += 1
            detail.append((tag, str(df["dt"].iloc[i]), s.side, s.entry,
                           s.stop, round(s.structure * 100, 2), round(r, 2), why))

    print("\n===== 结果 =====")
    for m in ("A", "B", "C", "D"):
        print("  " + str(res[m]))

    tot = sum(r.n for r in res.values())
    if tot:
        allr = sum(r.total_r for r in res.values())
        win = sum(r.wins for r in res.values())
        print(f"\n  合计 n={tot} 胜率={win/tot*100:.1f}% 平均R={allr/tot:+.2f} 总R={allr:+.1f}")

    print("\n===== 最近 15 笔 =====")
    for d in detail[-15:]:
        print("  ", d)


class _DummyLiq:
    """回测时不需要清算层，给空实现。"""
    def __init__(self, price):
        self.price = price
        self.above, self.below = [], []
        self.r_ratio = 1.0
        self.variant = "NONE"
        self.total_above_3pct = self.total_below_3pct = 0.0
        self.nearest_above = self.nearest_below = None
        self.oi_btc = self.oi_usd = self.funding = self.ls_ratio = 0.0
        self.taker_buy_ratio = 0.5

    @property
    def score(self):
        return 0


if __name__ == "__main__":
    main()
