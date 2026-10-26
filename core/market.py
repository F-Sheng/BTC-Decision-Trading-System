"""行情与指标：K线、MA/MACD/RSI/BOLL/ATR、摆动点、K线形态。"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .okx import OkxClient


# ---------------- 指标 ----------------
def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def macd(close: pd.Series, fast: int = 12, slow: int = 26, sig: int = 9):
    dif = ema(close, fast) - ema(close, slow)
    dea = ema(dif, sig)
    hist = (dif - dea) * 2
    return dif, dea, hist


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    delta = close.diff()
    up = delta.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-delta.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    rs = up / dn.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def boll(close: pd.Series, n: int = 20, k: float = 2.0):
    mid = close.rolling(n).mean()
    sd = close.rolling(n).std(ddof=0)
    return mid, mid + k * sd, mid - k * sd


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    h, l, c = df["high"], df["low"], df["close"]
    pc = c.shift(1)
    tr = pd.concat([(h - l).abs(), (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def to_df(candles: list[list]) -> pd.DataFrame:
    """OKX K线 -> DataFrame（升序）。保留 confirm 标记（1=已收盘，0=正在形成）。"""
    rows = []
    for c in candles:
        rows.append({
            "ts": int(c[0]),
            "open": float(c[1]),
            "high": float(c[2]),
            "low": float(c[3]),
            "close": float(c[4]),
            "vol": float(c[5]),
            "vol_ccy": float(c[6]) if len(c) > 6 and c[6] else 0.0,
            "vol_quote": float(c[7]) if len(c) > 7 and c[7] else 0.0,
            "confirm": int(c[8]) if len(c) > 8 and c[8] != "" else 1,
        })
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["dt"] = pd.to_datetime(df["ts"], unit="ms", utc=True).dt.tz_convert("Asia/Shanghai")
    return df.reset_index(drop=True)


def drop_forming(df: pd.DataFrame) -> pd.DataFrame:
    """丢弃尚未收盘的 K 线。

    这是关键修复：OKX 的 candles 接口会把"正在形成"的那根也返回，
    若直接用 iloc[-1] 判断形态与量能，得到的是半根 K 线（量比常低至 0.1~0.2、
    没有完整的上/下影），会导致模型永远无法触发。
    """
    if df is None or df.empty or "confirm" not in df.columns:
        return df
    out = df[df["confirm"] == 1]
    return out.reset_index(drop=True) if len(out) else df


def enrich(df: pd.DataFrame) -> pd.DataFrame:
    """一次性算全套指标。"""
    if df.empty:
        return df
    c = df["close"]
    df["ma5"] = c.rolling(5).mean()
    df["ma10"] = c.rolling(10).mean()
    df["ma20"] = c.rolling(20).mean()
    df["ma60"] = c.rolling(60).mean()
    dif, dea, hist = macd(c)
    df["dif"], df["dea"], df["macd_hist"] = dif, dea, hist
    df["rsi14"] = rsi(c, 14)
    mid, ub, lb = boll(c, 20, 2.0)
    df["bb_mid"], df["bb_up"], df["bb_low"] = mid, ub, lb
    df["atr14"] = atr(df, 14)
    return df


# ---------------- 结构 ----------------
def swing_points(df: pd.DataFrame, left: int = 3, right: int = 3) -> tuple[list, list]:
    """分形摆动高/低点。返回 [(idx, price), ...]。"""
    highs, lows = [], []
    h, l = df["high"].values, df["low"].values
    n = len(df)
    for i in range(left, n - right):
        if h[i] == max(h[i - left:i + right + 1]):
            highs.append((i, float(h[i])))
        if l[i] == min(l[i - left:i + right + 1]):
            lows.append((i, float(l[i])))
    return highs, lows


@dataclass
class CandleShape:
    """单根 K 线的形态量化。"""
    body: float
    upper_wick: float
    lower_wick: float
    total: float
    is_bull: bool
    long_upper: bool      # 上影 >= 2倍实体
    long_lower: bool      # 下影 >= 2倍实体
    close_pos: float      # 收盘在区间中的位置 0..1


def shape(row) -> CandleShape:
    o, h, l, c = float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"])
    total = max(h - l, 1e-9)
    body = abs(c - o)
    up = h - max(o, c)
    dn = min(o, c) - l
    return CandleShape(
        body=body, upper_wick=up, lower_wick=dn, total=total,
        is_bull=c >= o,
        long_upper=up >= max(body * 2, total * 0.35),
        long_lower=dn >= max(body * 2, total * 0.35),
        close_pos=(c - l) / total,
    )


def engulfing(df: pd.DataFrame, i: int = -1) -> str:
    """返回 'bull' / 'bear' / ''。"""
    if len(df) < 2:
        return ""
    a, b = df.iloc[i - 1], df.iloc[i]
    a_bear = a["close"] < a["open"]
    b_bull = b["close"] > b["open"]
    if a_bear and b_bull and b["close"] > a["open"] and b["open"] < a["close"]:
        return "bull"
    a_bull = a["close"] > a["open"]
    b_bear = b["close"] < b["open"]
    if a_bull and b_bear and b["close"] < a["open"] and b["open"] > a["close"]:
        return "bear"
    return ""


# ---------------- 快照 ----------------
@dataclass
class TimeframeView:
    name: str
    price: float
    ma5: float
    ma10: float
    ma20: float
    macd_hist: float
    macd_hist_prev: float
    rsi: float
    bb_mid: float
    bb_up: float
    bb_low: float
    atr: float
    swing_highs: list = field(default_factory=list)
    swing_lows: list = field(default_factory=list)
    df: pd.DataFrame | None = None

    @property
    def ma_score(self) -> int:
        if self.ma5 > self.ma10 > self.ma20:
            return 1
        if self.ma5 < self.ma10 < self.ma20:
            return -1
        return 0

    @property
    def macd_score(self) -> int:
        if self.macd_hist > 0 and self.macd_hist > self.macd_hist_prev:
            return 1
        if self.macd_hist < 0 and self.macd_hist < self.macd_hist_prev:
            return -1
        if self.macd_hist > 0:
            return 1
        if self.macd_hist < 0:
            return -1
        return 0

    @property
    def structure_score(self) -> int:
        """最近两个摆动高/低点的相对关系。"""
        hh = len(self.swing_highs) >= 2 and self.swing_highs[-1][1] > self.swing_highs[-2][1]
        hl = len(self.swing_lows) >= 2 and self.swing_lows[-1][1] > self.swing_lows[-2][1]
        lh = len(self.swing_highs) >= 2 and self.swing_highs[-1][1] < self.swing_highs[-2][1]
        ll = len(self.swing_lows) >= 2 and self.swing_lows[-1][1] < self.swing_lows[-2][1]
        if hh and hl:
            return 1
        if lh and ll:
            return -1
        return 0

    @property
    def score(self) -> int:
        return self.ma_score + self.macd_score + self.structure_score

    @property
    def bb_width_pct(self) -> float:
        return (self.bb_up - self.bb_low) / max(self.price, 1e-9)


def build_view(name: str, df: pd.DataFrame) -> TimeframeView | None:
    if df is None or len(df) < 60:
        return None
    last, prev = df.iloc[-1], df.iloc[-2]
    highs, lows = swing_points(df, 3, 3)
    return TimeframeView(
        name=name,
        price=float(last["close"]),
        ma5=float(last["ma5"]), ma10=float(last["ma10"]), ma20=float(last["ma20"]),
        macd_hist=float(last["macd_hist"]), macd_hist_prev=float(prev["macd_hist"]),
        rsi=float(last["rsi14"]),
        bb_mid=float(last["bb_mid"]), bb_up=float(last["bb_up"]), bb_low=float(last["bb_low"]),
        atr=float(last["atr14"]),
        swing_highs=[(i, p) for i, p in highs[-6:]],
        swing_lows=[(i, p) for i, p in lows[-6:]],
        df=df,
    )


def load_all(client: OkxClient, inst_id: str) -> dict[str, pd.DataFrame]:
    """拉取 4 个周期并计算指标。**只保留已收盘的 K 线**。"""
    out = {}
    for name, bar, n in (("15m", "15m", 300), ("1h", "1H", 400),
                         ("4h", "4H", 400), ("1d", "1D", 200)):
        raw = client.candles_bulk(inst_id, bar=bar, total=n)
        df = drop_forming(to_df(raw))
        out[name] = enrich(df)
    return out
