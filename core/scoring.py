"""五层打分引擎：把 L1~L5 合成一个可执行的方向与许可矩阵。"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import macro, liquidation, events, coinglass_file
from .market import TimeframeView

# L1 总分 → 允许的方向
ALLOW_MAP = [
    (-99, -6, "strong_short"),
    (-6, -2, "short"),
    (-2, 2, "range"),
    (2, 6, "long"),
    (6, 99, "strong_long"),
]


@dataclass
class ScoreCard:
    l1_base: int = 0
    l1_adjust: int = 0
    l1_total: int = 0
    l1_allow: str = "range"

    l2_score: int = 0
    l2_crowd: int = 0
    l2_total: int = 0
    l2_r: float = 0.0
    l2_fuel_dist: float | None = None
    cg_used: bool = False
    senti_score: int = 0
    senti_note: str = ""
    cg_note: str = ""

    l3: dict = field(default_factory=dict)   # {'1d','4h','1h','15m':View}
    l3_bias: int = 0
    l3_aligned: str = "mixed"                # 'up' / 'down' / 'mixed'

    d1_score: int = 0                        # 日线趋势分：+1 多 / -1 空 / 0 混合
    d1_note: str = ""

    l4_adjust: int = 0
    l4_blackout: bool = False

    veto: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def bias(self) -> str:
        """主方向（只由 L1+L4 决定，不含 L2 覆盖）。"""
        if self.veto:
            return "none"
        if self.l1_allow in ("strong_short", "short"):
            return "short"
        if self.l1_allow in ("strong_long", "long"):
            return "long"
        return "none"

    @property
    def direction(self) -> str:
        """可交易方向：主方向优先；中性时由 L2 决定。"""
        b = self.bias
        if b != "none":
            return b
        if self.l2_total > 0:
            return "long"
        if self.l2_total < 0:
            return "short"
        return "none"

    @property
    def counter_trend_allowed(self) -> bool:
        """逆势单是否允许。

        强趋势（|L1|≥4）里做逆势，必须有 L2 明确支撑（清算簇/持仓结构反向）。
        """
        if self.l1_allow in ("strong_short", "strong_long"):
            return abs(self.l2_total) >= 1
        return True

    def brief(self) -> str:
        return (f"L1={self.l1_base:+d}{self.l1_adjust:+d}={self.l1_total:+d}[{self.l1_allow}] "
                f"L2={self.l2_total:+d}(燃料{self.l2_score:+d}/持仓{self.l2_crowd:+d},R={self.l2_r:.2f}) "
                f"L3={self.l3_bias:+d}[{self.l3_aligned}] 日线={self.d1_score:+d} "
                f"L4={self.l4_adjust:+d} → 方向={self.direction} "
                f"{'VETO:' + ','.join(self.veto) if self.veto else ''}")


def _l1_allow(total: int) -> str:
    for lo, hi, name in ALLOW_MAP:
        if lo <= total < hi:
            return name
    return "range"


def _l3_bias(l3: dict) -> tuple[int, str]:
    """三周期打分：4h+1h 定基调，15m 做确认。"""
    v4, v1, v15 = l3.get("4h"), l3.get("1h"), l3.get("15m")
    if not (v4 and v1):
        return 0, "mixed"
    s4, s1 = v4.score, v1.score
    bias = s4 + s1
    if s4 > 0 and s1 > 0:
        aligned = "up"
    elif s4 < 0 and s1 < 0:
        aligned = "down"
    else:
        aligned = "mixed"
    return bias, aligned


def build(macro_view: macro.MacroView, liq: liquidation.LiqView,
          event_view: events.EventView, l3: dict, cfg: dict) -> ScoreCard:
    sc = ScoreCard()
    sc.l1_base = macro_view.score
    sc.l1_adjust = event_view.adjust
    sc.l1_total = max(-8, min(8, sc.l1_base + sc.l1_adjust))
    sc.l1_allow = _l1_allow(sc.l1_total)

    sc.l2_score = liq.score
    sc.l2_r = liq.r_ratio
    sc.l2_fuel_dist = liq.fuel_distance_pct
    # 持仓结构（散户/大户背离、费率、OI）独立计入 L2
    sc.l2_crowd = getattr(getattr(liq, "pos", None), "score", 0)

    # ---------- CoinGlass 快照覆盖（若有新鲜数据）----------
    cg = coinglass_file.load()
    sc.cg_used = cg.fresh
    if not cg.fresh:
        # CoinGlass 快照过期 → 改用 OKX 真实强平流（代码自取，无需浏览器）
        from . import liq_live
        ll = liq_live.load()
        r = ll.squeeze_ratio
        if ll.ok and ll.n >= 20 and r is not None:
            sc.l2_r = r
            sc.l2_score = 1 if r >= 1.5 else (-1 if r <= 0.67 else 0)
            sc.notes.append("CoinGlass 过期，改用真实强平流：" + ll.summary())
        else:
            sc.l2_score = 0
            sc.l2_r = None
            sc.notes.append(
                f"清算数据不可用（CoinGlass 过期 {cg.age_min:.0f} 分钟，"
                f"真实强平流仅 {ll.n} 笔）→ 按中性处理，不用估算模型冒充数据")
    if cg.fresh:
        r = cg.r_ratio
        if r is not None:
            # 用真实清算地图的 R 覆盖代理值
            sc.l2_r = r
            sc.l2_score = 1 if r >= 1.5 else (-1 if r <= 0.67 else 0)
        # 鲸鱼方向（按持仓加权，非账户数）
        if cg.whale_bias_binance > 0:
            if cg.whale_bias_binance >= 1.33:
                sc.l2_crowd += 1
            elif cg.whale_bias_binance <= 0.75:
                sc.l2_crowd -= 1
        # 散户 vs 鲸鱼持仓的背离
        if cg.retail_binance > 0 and cg.whale_pos_binance > 0:
            ratio = cg.retail_binance / cg.whale_pos_binance
            if ratio >= 1.30:
                sc.l2_crowd -= 1
            elif ratio <= 0.77:
                sc.l2_crowd += 1
    sc.cg_note = cg.summary()

    # 市场情绪（逆向）：极度乐观→利空，极度悲观→利多
    sn = getattr(liq, "senti", None)
    if sn is not None:
        sc.senti_score = int(getattr(sn, "score", 0) or 0)
        sc.senti_note = sn.summary()
        if sc.senti_score >= 2:
            sc.l2_crowd -= 1
        elif sc.senti_score <= -2:
            sc.l2_crowd += 1

    sc.l2_crowd = max(-3, min(3, sc.l2_crowd))
    sc.l2_total = max(-3, min(3, sc.l2_score + sc.l2_crowd))

    sc.l3 = l3
    sc.l3_bias, sc.l3_aligned = _l3_bias(l3)

    # 日线：判断"趋势中的回调"还是"趋势反转"
    v1d = l3.get("1d")
    if v1d is not None:
        sc.d1_score = v1d.score
        sc.d1_note = (f"日线 MA5={v1d.ma5:,.0f} MA20={v1d.ma20:,.0f} "
                      f"MACD柱={v1d.macd_hist:.1f} RSI={v1d.rsi:.0f}")

    sc.l4_adjust = event_view.adjust
    sc.l4_blackout = event_view.blackout
    sc.notes.extend(event_view.notes[:4])

    # ---------- 否决项 ----------
    # 波动率过窄 → 止盈空间不足（用 1H 带宽，15m 天然窄不能作为否决）
    v1h = l3.get("1h")
    v15 = l3.get("15m")
    if v1h and v1h.bb_width_pct < 0.008:
        sc.veto.append(f"1H布林带过窄({v1h.bb_width_pct*100:.2f}%<0.8%)")
    if v15 and v15.bb_width_pct < 0.0020:
        sc.veto.append(f"15M布林带过窄({v15.bb_width_pct*100:.2f}%)")
    # 2026-10-28 用户明确要求：不要用「事件静默期」禁止开仓。
    # 改为「提示 + 缩仓」，由风控决定仓位，不直接 veto。
    if event_view.blackout:
        sc.notes.append("事件窗口：建议缩仓（不禁止开仓）")
        try:
            sc.size_penalty = min(0.5, float(getattr(sc, "size_penalty", 1.0)))
        except Exception:
            pass
    if liq.fuel_distance_pct is not None and liq.fuel_distance_pct > 5:
        sc.notes.append("最近清算簇>5%，短期无磁铁")

    return sc
