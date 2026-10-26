"""实时跨资产行情：新浪财经为准（实时期货），Yahoo 兜底 + 提供 5 日序列。

新浪 `hq.sinajs.cn` 免费、免密钥、实时（秒级），字段：
  hf_XX = 现价, ?, 买价, 卖价, 最高, 最低, 时间, 昨结算, 今开, ...
  DINIW = 时间, 现价, 买, 卖, ?, 昨收, 高, 低, ...
Yahoo 负责取 5 日收盘序列（用于算 5 日涨跌）。
"""
from __future__ import annotations

import json
import re
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

from .utils import ROOT, read_json, safe_div, write_json

CACHE = ROOT / "state" / "live_macro.json"
TTL = 120
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

# 新浪代码 -> (中文名, Yahoo 对照)
# 新浪没有 VIX，单独用 Yahoo 取
YAHOO_ONLY = {"vix": "^VIX"}

SINA = {
    "wti": ("hf_CL", "WTI原油", "CL=F"),
    "nasdaq": ("hf_NQ", "纳指期货", "^IXIC"),
    "gold": ("hf_GC", "黄金", "GC=F"),
    "silver": ("hf_SI", "白银", "SI=F"),
    "dxy": ("DINIW", "美元指数", "DX-Y.NYB"),
}


@dataclass
class Quote:
    key: str = ""
    symbol: str = ""
    last: float | None = None
    prev_close: float | None = None
    chg_1d: float | None = None
    chg_5d: float | None = None
    src: str = ""
    quote_ts: float = 0.0
    series: list = field(default_factory=list)

    def ok(self) -> bool:
        return self.last is not None


def _sina_raw() -> dict[str, str]:
    codes = ",".join(v[0] for v in SINA.values())
    url = f"https://hq.sinajs.cn/list={codes}"
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Referer": "https://finance.sina.com.cn/"})
    with urllib.request.urlopen(req, timeout=10) as r:
        txt = r.read().decode("gbk", "ignore")
    out = {}
    for m in re.finditer(r'hq_str_(\w+)="([^"]*)"', txt):
        out[m.group(1)] = m.group(2)
    return out


def _parse_sina(code: str, raw: str) -> tuple[float | None, float | None]:
    parts = raw.split(",")
    try:
        if code == "DINIW":
            # 时间,现价,买,卖,?,昨收,...
            return float(parts[1]), float(parts[5])
        return float(parts[0]), float(parts[7])
    except Exception:
        return None, None


def _yahoo_quote(sym: str) -> tuple[float | None, float | None]:
    """取实时价与昨收（Yahoo meta）。"""
    url = ("https://query1.finance.yahoo.com/v8/finance/chart/"
           + urllib.parse.quote(sym) + "?range=5d&interval=1d")
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=12) as r:
            d = json.loads(r.read().decode())
        m = d["chart"]["result"][0]["meta"]
        return m.get("regularMarketPrice"), m.get("chartPreviousClose")
    except Exception:
        return None, None


def _yahoo_series(sym: str) -> list[tuple[int, float]]:
    url = ("https://query1.finance.yahoo.com/v8/finance/chart/"
           + urllib.parse.quote(sym) + "?range=7d&interval=1d")
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=12) as r:
        d = json.loads(r.read().decode())
    res = d["chart"]["result"][0]
    ts = res.get("timestamp") or []
    cl = res["indicators"]["quote"][0].get("close") or []
    return [(int(t), float(c)) for t, c in zip(ts, cl) if c is not None][-7:]


def load(force: bool = False) -> dict[str, Quote]:
    cache = read_json(CACHE, {}) or {}
    if not force and cache.get("_t") and time.time() - cache["_t"] < TTL:
        return {k: Quote(**v) for k, v in cache["data"].items()}

    prev = (cache.get("data") or {})
    out: dict[str, Quote] = {}

    try:
        raw = _sina_raw()
    except Exception:
        raw = {}

    for key, ysym in YAHOO_ONLY.items():
        q = Quote(key=key, symbol=ysym)
        last, pc = _yahoo_quote(ysym)
        if last:
            q.last, q.prev_close, q.src = float(last), pc and float(pc), "yahoo"
            if pc:
                q.chg_1d = safe_div(q.last - q.prev_close, q.prev_close) * 100
            q.quote_ts = time.time()
        else:
            pv = prev.get(key) or {}
            q.last, q.prev_close = pv.get("last"), pv.get("prev_close")
            q.chg_1d, q.src = pv.get("chg_1d"), "cache"
            q.quote_ts = pv.get("quote_ts") or 0
        try:
            ser = _yahoo_series(ysym)
            q.series = ser
            if len(ser) >= 6:
                q.chg_5d = safe_div(ser[-1][1] - ser[-6][1], ser[-6][1]) * 100
        except Exception:
            q.chg_5d = (prev.get(key) or {}).get("chg_5d")
        out[key] = q

    for key, (code, cn, ysym) in SINA.items():
        q = Quote(key=key, symbol=ysym)
        last = pc = None
        if code in raw and raw[code]:
            last, pc = _parse_sina(code, raw[code])
        if last:
            q.last, q.prev_close, q.src = last, pc, "sina"
            q.quote_ts = time.time()
            if pc:
                q.chg_1d = safe_div(last - pc, pc) * 100
        else:
            p = prev.get(key) or {}
            q.last = p.get("last")
            q.prev_close = p.get("prev_close")
            q.chg_1d = p.get("chg_1d")
            q.src = "cache"
            q.quote_ts = pv.get("quote_ts") or 0

        try:
            ser = _yahoo_series(ysym)
            q.series = ser
            if len(ser) >= 6:
                q.chg_5d = safe_div(ser[-1][1] - ser[-6][1], ser[-6][1]) * 100
        except Exception:
            q.chg_5d = (prev.get(key) or {}).get("chg_5d")

        out[key] = q

    write_json(CACHE, {"_t": time.time(),
                       "data": {k: {kk: getattr(v, kk)
                                    for kk in Quote.__dataclass_fields__}
                                for k, v in out.items()}})
    return out


def summary(q: dict[str, Quote]) -> str:
    def f(k, label):
        v = q.get(k)
        if not v or not v.ok():
            return f"{label}=NA"
        d1 = f"{v.chg_1d:+.2f}%" if v.chg_1d is not None else "NA"
        d5 = f"{v.chg_5d:+.2f}%" if v.chg_5d is not None else "NA"
        return f"{label} {v.last:.1f} (今{d1}/5d{d5})"

    return " | ".join([f("wti", "WTI"), f("nasdaq", "纳指期货"),
                       f("gold", "黄金"), f("dxy", "DXY")])
