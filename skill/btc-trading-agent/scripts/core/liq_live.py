"""真实强平流（OKX 公共接口）—— 替代自建估算模型。

为什么换：
  原来的 `liquidation.py` 是个**估算模型**（假设成交额 3.5% 转为持仓、7 天半衰、
  杠杆档位 10/25/50/100 全是假设），它输出的数字和真数据长得一样，但都是编的。
  OKX 的 `/api/v5/public/liquidation-orders` 返回**实际发生的爆仓记录**，
  代码可以直连，不需要浏览器，也不需要密钥。

用途两条：
  1. **爆仓热图**：按价格分桶累计真实爆仓金额 → 这些价位就是真实的多空防线
  2. **挤压力度**：最近 1 小时多头爆仓 vs 空头爆仓的金额比 → 谁在被强平

局限（必须说清）：
  接口只回最近约 100 条（约 40–60 分钟），所以需要持续轮询累积。
  刚启动时窗口很短，攒满 24 小时才有意义。
"""
from __future__ import annotations

import json
import time
import urllib.request
from dataclasses import dataclass, field

from .utils import ROOT, read_json, write_json

FILE = ROOT / "state" / "liq_live.json"
URL = ("https://www.okx.com/api/v5/public/liquidation-orders"
       "?instType=SWAP&uly=BTC-USDT&state=filled&limit=100")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
KEEP_SEC = 24 * 3600          # 只保留 24 小时内的爆仓
POLL_GAP_SEC = 45             # 两次抓取的最小间隔


@dataclass
class LiqLive:
    ok: bool = False
    n: int = 0
    window_h: float = 0.0
    long_usd_1h: float = 0.0      # 近 1 小时多头爆仓金额
    short_usd_1h: float = 0.0     # 近 1 小时空头爆仓金额
    clusters_up: list = field(default_factory=list)    # [(price, usd)]
    clusters_dn: list = field(default_factory=list)
    updated: int = 0

    @property
    def squeeze_ratio(self) -> float | None:
        """空头爆仓 / 多头爆仓。>1 表示空头在被挤（利多），<1 表示多头在被杀。"""
        if self.long_usd_1h + self.short_usd_1h <= 0:
            return None
        return self.short_usd_1h / max(self.long_usd_1h, 1.0)

    def summary(self) -> str:
        if not self.ok or self.n == 0:
            return "真实强平=无数据"
        r = self.squeeze_ratio
        side = "空头被挤(利多)" if (r or 0) > 1.2 else (
            "多头被杀(利空)" if (r or 9) < 0.83 else "多空均衡")
        return (f"真实强平 {self.n}笔/{self.window_h:.1f}h  "
                f"近1h 空爆${self.short_usd_1h/1e6:.1f}M/多爆${self.long_usd_1h/1e6:.1f}M"
                f" 比{r:.2f} {side}" if r else f"真实强平 {self.n}笔")


def _fetch() -> list[dict]:
    req = urllib.request.Request(URL, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=12) as r:
        d = json.loads(r.read().decode())
    out = []
    for inst in (d.get("data") or []):
        for x in inst.get("details", []):
            try:
                px = float(x["bkPx"])
                sz = float(x["sz"])
                ts = int(x["time"])
            except (KeyError, ValueError, TypeError):
                continue
            out.append({
                "t": ts,
                "px": round(px, 1),
                "sz": sz,                      # 张数
                "usd": round(px * sz * 0.01, 2),   # BTC-USDT-SWAP ctVal=0.01
                "side": x.get("posSide", ""),  # long / short —— 被强平的方向
            })
    return out


def _key(x: dict) -> str:
    return f"{x['t']}_{x['px']}_{x['sz']}_{x['side']}"


def poll(force: bool = False) -> LiqLive:
    """抓一次并累积到本地。返回当前视图。"""
    raw = read_json(FILE, {}) or {}
    seen = set(raw.get("seen") or [])
    rows = raw.get("rows") or []
    last_t = float(raw.get("_t") or 0)

    if force or time.time() - last_t >= POLL_GAP_SEC:
        try:
            fresh = _fetch()
            added = 0
            for x in fresh:
                k = _key(x)
                if k in seen:
                    continue
                seen.add(k)
                rows.append(x)
                added += 1
        except Exception:
            added = 0

        cutoff = int((time.time() - KEEP_SEC) * 1000)
        rows = [x for x in rows if x["t"] >= cutoff]
        if len(seen) > 25000:
            seen = {_key(x) for x in rows}
        write_json(FILE, {"_t": time.time(), "rows": rows, "seen": sorted(seen)})

    return _view(rows)


def _view(rows: list[dict]) -> LiqLive:
    v = LiqLive()
    if not rows:
        return v
    ts = [x["t"] for x in rows]
    v.n = len(rows)
    v.window_h = (max(ts) - min(ts)) / 3.6e6
    v.updated = int(max(ts) / 1000)

    now1h = int((time.time() - 3600) * 1000)
    for x in rows:
        if x["t"] < now1h:
            continue
        if x["side"] == "short":
            v.short_usd_1h += x["usd"]
        else:
            v.long_usd_1h += x["usd"]

    # 按 0.25% 分桶做热图
    prices = [x["px"] for x in rows]
    lo, hi = min(prices), max(prices)
    if hi <= lo:
        return v
    step = max(lo * 0.0025, 1.0)
    buckets: dict[float, float] = {}
    for x in rows:
        b = round(x["px"] / step) * step
        buckets[b] = buckets.get(b, 0.0) + x["usd"]

    ordered = sorted(buckets.items(), key=lambda kv: kv[1], reverse=True)[:12]
    mid = (lo + hi) / 2
    v.clusters_up = [(p, u) for p, u in ordered if p >= mid]
    v.clusters_dn = [(p, u) for p, u in ordered if p < mid]
    v.ok = True
    return v


def load() -> LiqLive:
    raw = read_json(FILE, {}) or {}
    return _view(raw.get("rows") or [])
