"""CoinGlass 快照接入层。

背景：CoinGlass 的数据是 canvas 渲染 + API 返回 base64 密文，程序无法直接抓取。
可行方案见 `coinglass-agent-playbook.md`：由智能体用 Chrome 读取后写入
`data/coinglass.json`，本模块负责读取、校验新鲜度、并转成打分层可用的结构。

新鲜度策略：
  < 2 小时  → 直接采用（authoritative）
  2–12 小时 → 降级采用（与 PROXY 取平均）
  > 12 小时 → 丢弃，退回 PROXY
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from .utils import ROOT, read_json, safe_div

FILE = ROOT / "data" / "coinglass.json"
FRESH_SEC = 40 * 60   # 2026-10-27: 原 2h 太长，15 分钟节奏下 40 分钟即过期
STALE_SEC = 12 * 3600


@dataclass
class CGView:
    fresh: bool = False
    age_min: float = 1e9
    spot: float = 0.0
    # 清算地图（Binance 1d）—— 只用"对称距离"的累计值，否则比值无意义
    short_cum_near: float = 0.0      # ±1.5% 内 累计空头
    long_cum_near: float = 0.0       # ±1.5% 内 累计多头
    short_cum_next: float = 0.0      # ±3% 累计空头
    long_cum_below: float = 0.0      # ±3% 累计多头
    short_trigger: float = 0.0
    short_trigger_next: float = 0.0
    long_trigger: float = 0.0
    near_pct: float = 1.45
    far_pct: float = 3.2
    # 全交易所
    cex_short_usd: float = 0.0
    cex_long_usd: float = 0.0
    cex_short_trigger: float = 0.0
    cex_long_trigger: float = 0.0
    # 多空比
    retail_binance: float = 0.0
    whale_pos_binance: float = 0.0
    whale_bias_binance: float = 0.0
    whale_bias_long_pct: float = 0.0
    whale_account_okx: float = 0.0
    whale_pos_okx: float = 0.0
    smart_money: str = ""

    @property
    def r_ratio(self) -> float | None:
        """对称 ±3% 的上方空头燃料 / 下方多头燃料。

        ⚠ 早期版本用"上方 1.3% 的空头"除以"下方 10% 的多头"，
        距离不对称，得出 9.5 倍的错误结论。现在只比较对称距离。
        """
        if self.short_cum_next <= 0 or self.long_cum_below <= 0:
            return None
        return safe_div(self.short_cum_next, self.long_cum_below, 1.0)

    @property
    def r_near(self) -> float | None:
        """对称 ±1.5% 的比值（近端）。"""
        if self.short_cum_near <= 0 or self.long_cum_near <= 0:
            return None
        return safe_div(self.short_cum_near, self.long_cum_near, 1.0)

    @property
    def cex_r(self) -> float | None:
        if self.cex_short_usd <= 0 or self.cex_long_usd <= 0:
            return None
        return self.cex_short_usd / self.cex_long_usd

    def summary(self) -> str:
        return (f"CG[{'鲜' if self.fresh else '旧'}{self.age_min:.0f}m] "
                f"BN空头累计{self.short_cum_next/1e6:.0f}M@{self.short_trigger_next:,.0f} "
                f"下方多头{self.long_cum_below/1e6:.0f}M R={self.r_ratio:.1f} "
                f"全CEX {self.cex_short_usd/1e8:.2f}亿/{self.cex_long_usd/1e8:.2f}亿 "
                f"鲸鱼偏{self.whale_bias_binance or self.whale_pos_binance:.2f} {self.smart_money}"
                if self.fresh else
                f"CG 无新鲜数据（{self.age_min:.0f}m 前）")


def load() -> CGView:
    raw = read_json(FILE, {}) or {}
    v = CGView()
    if not raw or not raw.get("captured_at"):
        return v

    age = time.time() - raw["captured_at"] / 1000
    v.age_min = age / 60
    v.fresh = age < FRESH_SEC
    if age > STALE_SEC:
        return v

    m = raw.get("binance_liqmap_1d", {}) or {}
    v.spot = float(m.get("spot") or 0)
    near = m.get("near") or {}
    far = m.get("far") or {}
    if near:
        v.near_pct = float(near.get("pct") or 1.45)
        v.short_trigger = float(near.get("up_px") or 0)
        v.short_cum_near = float(near.get("short") or 0)
        v.long_trigger = float(near.get("dn_px") or 0)
        v.long_cum_near = float(near.get("long") or 0)
    if far:
        v.far_pct = float(far.get("pct") or 3.2)
        v.short_trigger_next = float(far.get("up_px") or 0)
        v.short_cum_next = float(far.get("short") or 0)
        v.long_trigger = float(far.get("dn_px") or v.long_trigger)
        v.long_cum_below = float(far.get("long") or 0)

    cex = raw.get("all_cex_liq", {}) or {}
    v.cex_short_usd = float(cex.get("short_usd") or 0)
    v.cex_long_usd = float(cex.get("long_usd") or 0)
    v.cex_short_trigger = float(cex.get("short_trigger") or 0)
    v.cex_long_trigger = float(cex.get("long_trigger") or 0)

    lsr = raw.get("lsr_by_exchange", {}) or {}
    bn = lsr.get("binance", {}) or {}
    v.retail_binance = float(bn.get("retail") or 0)
    v.whale_pos_binance = float(bn.get("whale_position") or 0)
    v.whale_bias_binance = float(bn.get("whale_bias") or 0)
    v.whale_bias_long_pct = float(bn.get("whale_bias_long_pct") or 0)
    v.smart_money = str(bn.get("smart_money") or "")
    okx = lsr.get("okx", {}) or {}
    v.whale_account_okx = float(okx.get("whale_account") or 0)
    v.whale_pos_okx = float(okx.get("whale_position") or 0)
    return v
