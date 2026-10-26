"""L1 宏观层：全部基于可验证的一手数据（FRED / Coinbase / OKX）。

不使用 LLM 取数——LLM 只做文本分类，避免幻觉。
"""
from __future__ import annotations

import json
import time
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np

from .utils import ROOT, read_json, write_json, safe_div

FRED_KEY = "ff59c3475472692bfc3ab4a76a1e1fa9"
FRED_CACHE = ROOT / "state" / "fred_cache.json"
CB_HIST = ROOT / "state" / "coinbase_premium.json"
CACHE_TTL = 6 * 3600
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0 Safari/537.36"


# ---------------- FRED ----------------
def fred_series(series_id: str, limit: int = 30) -> list[tuple[str, float]]:
    """返回 [(date, value)]，降序。带 6 小时磁盘缓存。"""
    cache = read_json(FRED_CACHE, {}) or {}
    ent = cache.get(series_id)
    if ent and time.time() - ent.get("t", 0) < CACHE_TTL:
        return [(d, float(v)) for d, v in ent["v"]]

    url = (f"https://api.stlouisfed.org/fred/series/observations?series_id={series_id}"
           f"&api_key={FRED_KEY}&file_type=json&sort_order=desc&limit={limit}")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=20) as r:
            raw = json.loads(r.read().decode())
        out = []
        for o in raw.get("observations", []):
            if o.get("value") in (".", "", None):
                continue
            out.append((o["date"], float(o["value"])))
        cache[series_id] = {"t": time.time(), "v": out}
        write_json(FRED_CACHE, cache)
        return out
    except Exception:
        if ent:
            return [(d, float(v)) for d, v in ent["v"]]
        return []


def yoy(series: list[tuple[str, float]], months_back: int = 12) -> float | None:
    """月度序列同比。series 降序。"""
    if len(series) < months_back + 1:
        return None
    latest, base = series[0][1], series[months_back][1]
    return safe_div(latest - base, base) * 100


def pct_change_n(series: list[tuple[str, float]], n: int) -> float | None:
    if len(series) < n + 1:
        return None
    return safe_div(series[0][1] - series[n][1], series[n][1]) * 100


def delta_n(series: list[tuple[str, float]], n: int) -> float | None:
    if len(series) < n + 1:
        return None
    return series[0][1] - series[n][1]


# ---------------- Coinbase 溢价 ----------------
def coinbase_spot() -> float | None:
    try:
        req = urllib.request.Request(
            "https://api.exchange.coinbase.com/products/BTC-USD/ticker",
            headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=15) as r:
            return float(json.loads(r.read().decode())["price"])
    except Exception:
        return None


def coinbase_premium(okx_index: float) -> tuple[float | None, float | None]:
    """返回 (当前溢价%, 近5次观测均值%)。溢价为正 = 美国机构在买。"""
    cb = coinbase_spot()
    cur = None
    if cb and okx_index:
        cur = (cb - okx_index) / okx_index * 100
    hist = read_json(CB_HIST, []) or []
    if cur is not None:
        hist.append({"t": int(time.time()), "p": cur})
        hist = hist[-200:]
        write_json(CB_HIST, hist)
    recent = [h["p"] for h in hist[-5:]] if hist else []
    avg = float(np.mean(recent)) if recent else None
    return cur, avg


# ---------------- 结构 ----------------
@dataclass
class MacroItem:
    name: str
    value: float | None
    unit: str
    score: int
    note: str


@dataclass
class MacroView:
    items: list[MacroItem] = field(default_factory=list)
    score: int = 0
    updated: str = ""

    def summary(self) -> str:
        parts = [f"{i.name}={i.value:.2f}{i.unit}({i.score:+d})" if i.value is not None
                 else f"{i.name}=NA(0)" for i in self.items]
        return f"L1={self.score:+d} | " + " ".join(parts)


def ann3(series: list[tuple[str, float]], offset: int = 0) -> float | None:
    """近 3 个月的年化环比（%）。series 降序。"""
    if len(series) < offset + 4:
        return None
    a, b = series[offset][1], series[offset + 3][1]
    if b <= 0:
        return None
    return ((a / b) ** 4 - 1) * 100


def compute(client, okx_index: float | None = None,
            heads: list[dict] | None = None, cfg: dict | None = None) -> MacroView:
    """L1 宏观：8 项（覆盖 CPI / 油价 / 纳指 / 中东 / 选举 / 特朗普 / 市场定价）。"""
    items: list[MacroItem] = []

    core = fred_series("CPILFESL", 40)
    core_yoy = yoy(core, 12)
    s = 1 if (core_yoy is not None and core_yoy < 2.0) else (
        -1 if (core_yoy is not None and core_yoy > 2.5) else 0)
    items.append(MacroItem("核心CPI同比", core_yoy, "%", s, "高于2.5%=利空风险资产"))

    a1, a2 = ann3(core, 0), ann3(core, 3)
    mom = (a1 - a2) if (a1 is not None and a2 is not None) else None
    s = 1 if (mom is not None and mom < -0.3) else (
        -1 if (mom is not None and mom > 0.3) else 0)
    items.append(MacroItem("核心CPI动能", mom, "pt", s, "加速=紧缩压力"))

    eng = fred_series("CPIENGSL", 40)
    eng_mom = None
    if len(eng) >= 2 and eng[1][1] > 0:
        eng_mom = (eng[0][1] / eng[1][1] - 1) * 100
    s = 1 if (eng_mom is not None and eng_mom < -3) else (
        -1 if (eng_mom is not None and eng_mom > 3) else 0)
    items.append(MacroItem("能源CPI环比", eng_mom, "%", s, "油价冲击=紧缩压力"))

    # ---- 实时跨资产（Yahoo，10 分钟缓存）----
    from . import live_macro
    lm = live_macro.load()

    q = lm.get("wti")
    wti_chg = q.chg_5d if q and q.ok() else None
    s = 1 if (wti_chg is not None and wti_chg < -3) else (
        -1 if (wti_chg is not None and wti_chg > 3) else 0)
    items.append(MacroItem("WTI原油5日", wti_chg, "%", s,
                           (f"现价{q.last:.1f}美元" if q and q.ok() else "油价NA")))

    # 风险偏好 = 纳指 5 日 + VIX 5 日 综合
    nq, vx = lm.get("nasdaq"), lm.get("vix")
    n5 = nq.chg_5d if nq and nq.ok() else None
    v5 = vx.chg_5d if vx and vx.ok() else None
    s = 0
    if n5 is not None and v5 is not None:
        if n5 > 0.5 and v5 < 0:
            s = 1
        elif n5 < -0.5 and v5 > 0:
            s = -1
    items.append(MacroItem("风险偏好(纳指VIX)", n5, "%", s,
                           (f"VIX5日{v5:+.1f}%" if v5 is not None else "VIX=NA")))

    dx = lm.get("dxy")
    d5 = dx.chg_5d if dx and dx.ok() else None
    s = 1 if (d5 is not None and d5 < -0.5) else (
        -1 if (d5 is not None and d5 > 0.5) else 0)
    items.append(MacroItem("美元指数5日", d5, "%", s,
                           (f"现价{dx.last:.2f}" if dx and dx.ok() else "DXY=NA")))

    dgs2 = fred_series("DGS2", 60)
    d2 = delta_n(dgs2, 20)
    d2_bp = d2 * 100 if d2 is not None else None
    s = 1 if (d2_bp is not None and d2_bp < -10) else (
        -1 if (d2_bp is not None and d2_bp > 10) else 0)
    items.append(MacroItem("2Y收益率20日Δ", d2_bp, "bp", s, "上行=加息定价升温"))

    cur, avg = coinbase_premium(okx_index or 0)
    v = avg if avg is not None else cur
    s = 1 if (v is not None and v > 0.05) else (-1 if (v is not None and v < -0.05) else 0)
    items.append(MacroItem("Coinbase溢价", v, "%", s, "负值=美国机构折价卖出"))

    geo_sc, geo_note = 0, "无新闻"
    if heads:
        try:
            from . import geo
            gv = geo.compute(heads, cfg or {})
            geo_sc = gv.score
            geo_note = "中东/选举/特朗普/监管→" + str(geo_sc)
        except Exception as e:
            geo_note = "地缘失败"
    items.append(MacroItem("地缘政治", float(geo_sc), "分", geo_sc, geo_note))

    score = sum(i.score for i in items)
    return MacroView(items=items, score=score,
                     updated=datetime.now().strftime("%Y-%m-%d %H:%M"))
