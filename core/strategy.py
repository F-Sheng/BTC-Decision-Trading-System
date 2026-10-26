"""入场模型 A/B/C/D：从结构与形态生成可执行信号。

每个信号自带：入场价、止损价、三档止盈、模型标签、触发理由。
风险层再决定是否放行与仓位。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from . import liquidation as liqmod
from .market import TimeframeView, shape, engulfing
from .scoring import ScoreCard
from . import market

def _touch_tol(cfg: dict | None) -> float:
    """K 线是否'触到'关键位的容差（美元绝对值）。默认 100 点。"""
    try:
        return float((cfg or {}).get("touch_tol_usd", 100.0))
    except Exception:
        return 100.0


NEAR_TOL = 0.005   # 价格与关键位的最大容许距离（0.5%）；原 0.3% 过紧，几乎不触发


@dataclass
class Level:
    price: float
    source: str
    weight: float = 1.0

    def __str__(self) -> str:
        return f"{self.price:,.0f}({self.source})"


@dataclass
class Signal:
    side: str                 # 'short' / 'long'
    model: str                # 'A' / 'B' / 'C' / 'D'
    entry: float
    stop: float
    targets: list[float]
    counter_trend: bool = False
    confidence: float = 0.0
    reasons: list[str] = field(default_factory=list)
    structure: float = 0.0
    size_mult: float = 1.0    # 量能分级后的仓位倍数（1.0 / 0.5）
    vol_ratio: float = 1.0    # 当前量 ÷ 前 6 根均量

    @property
    def stop_pct(self) -> float:
        return abs(self.stop - self.entry) / self.entry

    def describe(self) -> str:
        t = "/".join(f"{x:,.0f}" for x in self.targets)
        sm = "" if self.size_mult >= 1.0 else f" 半仓×{self.size_mult:g}"
        return (f"[{self.model}] {self.side.upper()} @{self.entry:,.0f} "
                f"SL={self.stop:,.0f}({self.stop_pct*100:.2f}%) TP={t} "
                f"conf={self.confidence:.2f} 量比={self.vol_ratio:.2f}{sm} "
                f"{'逆势' if self.counter_trend else '顺势'}")


# ---------------- 关键价位 ----------------
def collect_levels(v15: TimeframeView, v1h: TimeframeView,
                   liq: liqmod.LiqView, price: float,
                   extra: dict | None = None) -> tuple[list[Level], list[Level]]:
    """技术面压力/支撑（标准算法）+ 清算簇（单独归类）。

    技术位来源只有三种，全部可复现、可核对：
      枢轴点 Pivot Points（日线/周线，TradingView 默认口径）
      成交量分布 Volume Profile 的 POC / VAH / VAL
      分形摆动点 Fractal Swings（4 小时 / 1 小时）
    布林带不再当压力位——它是波动率指标；整数关口只保留最近的作为心理位参考。
    """
    from . import levels as lvmod

    res: list[Level] = []
    sup: list[Level] = []
    lim = 0.06

    def add(bucket: list[Level], p: float, src: str, w: float = 1.0):
        if p <= 0:
            return
        if abs((p - price) / price) > lim:
            return
        bucket.append(Level(p, src, w))

    v4 = (extra or {}).get("4h")
    dfs = {}
    if v1h.df is not None:
        dfs["1h"] = v1h.df
    if v4 is not None and v4.df is not None:
        dfs["4h"] = v4.df

    if dfs:
        tr, ts = lvmod.build(dfs, price, swing_fn=market.swing_points)
        for x in tr:
            add(res, x.price, x.name, x.weight)
        for x in ts:
            add(sup, x.price, x.name, x.weight)

    # 清算簇：真实市场结构，但不是"技术位"，命名上区分开
    for lv in sorted((liq.above or []), key=lambda x: x.notional, reverse=True)[:5]:
        add(res, lv.price, "清算簇", 1.3)
    for lv in sorted((liq.below or []), key=lambda x: x.notional, reverse=True)[:5]:
        add(sup, lv.price, "清算簇", 1.3)

    # 近 40 根 1 小时极值
    if v1h.df is not None and len(v1h.df) >= 40:
        tail = v1h.df.tail(40)
        add(res, float(tail["high"].max()), "40H高", 1.1)
        add(sup, float(tail["low"].min()), "40H低", 1.1)

    # 心理关口：只留最近的上下各一档
    base = round(price / 500) * 500
    for k in (-1, 1):
        p = base + k * 500
        if abs(p - price) / price > 0.001:
            add(res if p > price else sup, p, "整数关口", 1.15)

    def dedupe(levels_list: list[Level], tol: float = 0.0003) -> list[Level]:
        levels_list.sort(key=lambda x: x.weight, reverse=True)
        out: list[Level] = []
        for lv in levels_list:
            if all(abs(lv.price - o.price) / price > tol for o in out):
                out.append(lv)
        return out

    return dedupe(res), dedupe(sup)


def display_levels(res: list[Level], sup: list[Level], price: float,
                   n: int = 5, min_pct: float = 0.003,
                   merge_pct: float = 0.005) -> tuple[list[Level], list[Level]]:
    """展示用关键价位：剔除噪声、合并密集区、按区域强度排序。

    为什么不能直接取"最近的 N 个"：
      清算簇是每 0.25% 一个的网格，最近的永远是噪声（曾出现压力 +0.08%）。
    做法：
      1. 丢弃距现价 < min_pct（默认 0.3%）的价位——太近没有交易意义
      2. 把相距 < merge_pct（默认 0.5%）的价位合并成一个"区域"
      3. 区域强度 = 成员权重之和，按强度取前 n 个
    """

    def prep(src: list[Level], above: bool) -> list[Level]:
        pts = []
        for x in src:
            d = (x.price - price) / price
            if above and d < min_pct:
                continue
            if (not above) and d > -min_pct:
                continue
            if abs(d) > 0.06:
                continue
            pts.append(x)
        pts.sort(key=lambda z: abs(z.price - price))

        zones: list[list[Level]] = []
        for x in pts:
            if zones and abs(x.price - zones[-1][0].price) / price <= merge_pct:
                zones[-1].append(x)
            else:
                zones.append([x])

        out = []
        for z in zones:
            strongest = max(z, key=lambda v: v.weight)
            kinds = {v.source for v in z}
            label = strongest.source if len(kinds) == 1 else f"{strongest.source}+{len(kinds)-1}"
            out.append(Level(strongest.price, label, sum(v.weight for v in z)))
        out.sort(key=lambda v: -v.weight)
        picked = out[:n]
        picked.sort(key=lambda v: abs(v.price - price))   # 展示时由近到远
        return picked

    return prep(res, True), prep(sup, False)


def _vol_ratio(df, lookback: int = 6) -> float:
    """当前 K 线成交额 ÷ 前 N 根均量。数据不足返回 1.0。"""
    if df is None or len(df) < lookback + 1:
        return 1.0
    avg = float(df["vol_quote"].iloc[-lookback - 1:-1].mean())
    cur = float(df["vol_quote"].iloc[-1])
    if avg <= 0:
        return 1.0
    return cur / avg


# 量能折扣开关：默认关闭。
# 关闭后不再按量比缩仓，固定满仓（仓位规模由风控层的"单笔风险预算"统一决定），
# 这样资金利用率更高，也不会出现"信号对但只下 1/2 仓"的错配。
VOL_SIZE_DISCOUNT = False


def _vol_grade(ratio: float, strong: float = 1.5, ok: float = 1.0,
               weak: float = 0.3) -> tuple[bool, float]:
    """量能分级 → (是否放行, 仓位倍数)。

    VOL_SIZE_DISCOUNT=False（当前）：量能只用于记录，不缩仓（返回 1.0）。
    VOL_SIZE_DISCOUNT=True：回复旧行为——
      量比 ≥ ok : 1.0 ｜ weak~ok : 0.5 ｜ weak/2~weak : 0.25 ｜ < weak/2 : 否决
    """
    if not VOL_SIZE_DISCOUNT:
        return True, 1.0
    if ratio >= ok:
        return True, 1.0
    if ratio >= weak:
        return True, 0.5
    if ratio >= weak / 2:
        return True, 0.25
    return False, 0.0


def _near(a: float, b: float, tol: float) -> bool:
    return abs(a - b) / max(b, 1e-9) <= tol


# ---------------- 模型 ----------------
def model_a(sc: ScoreCard, v15: TimeframeView, res: list[Level],
            cfg: dict) -> Signal | None:
    """阻力回抽做空：价格抵近压力位 + 15m 拒绝形态。"""
    if not v15.df is not None or len(v15.df) < 10:
        return None
    df, price = v15.df, v15.price
    row = df.iloc[-1]
    sh = shape(row)
    eng = engulfing(df, -1)

    for lv in sorted(res, key=lambda x: x.price):
        if lv.weight < 1.1:            # 只认摆动点/清算簇/极值，不认普通均线
            continue
        if not _near(price, lv.price, NEAR_TOL):
            continue
        if price > lv.price * 1.002:      # 已明显突破，不是回抽
            continue
        # 关键：这根 K 线的最高价必须触到阻力位附近，否则只是"在阻力下方收阴"，
        # 不构成"阻力位被拒绝"。容差用美元绝对值（BTC 上 100 点以内的偏差算触碰），
        # 比按百分比更贴合实战：8 万时 0.125%、16 万时 0.0625%，用户指定 100 点。
        if float(row["high"]) < lv.price - _touch_tol(cfg):
            continue
        reject = sh.long_upper or eng == "bear" or (not sh.is_bull and sh.close_pos < 0.45)
        if not reject:
            continue
        vr = _vol_ratio(df)
        ok_vol, size_mult = _vol_grade(vr, ok=1.0, weak=0.3)
        if not ok_vol:
            continue
        # 结构止损：压力位上方 0.5%
        stop_struct = lv.price * 1.005
        # 但入场距压力位 >1% 说明位置太差
        if (lv.price - price) / price > 0.01:
            continue
        conf = 0.45 + (0.15 if sh.long_upper else 0) + (0.1 if eng == "bear" else 0) \
            + (0.1 if lv.weight >= 1.2 else 0) \
            + (0.05 if vr >= 1.5 else (-0.05 if vr < 1.0 else 0))
        # 指标共振：RSI 超买 / MACD 动能转弱 / 触及布林上轨 —— 每条加分，不改变触发条件
        ind = 0.0
        notes = [f"触及{lv}", f"上影={sh.upper_wick:.0f}",
                 f"收于区间{sh.close_pos:.0%}", f"量比{vr:.2f}"]
        if v15.rsi >= 65:
            ind += 0.08
            notes.append(f"RSI{v15.rsi:.0f}超买")
        if v15.macd_hist < v15.macd_hist_prev:
            ind += 0.05
            notes.append("MACD柱转弱")
        if v15.bb_up > 0 and abs(price - v15.bb_up) / price <= 0.003:
            ind += 0.05
            notes.append("触及布林上轨")
        conf += ind
        return Signal(
            side="short", model="A", entry=round(price, 1),
            stop=round(stop_struct, 1), targets=[],
            confidence=min(conf, 0.9), size_mult=size_mult, vol_ratio=vr,
            reasons=notes,
            structure=max((stop_struct - price) / price, 0.005),
        )
    return None


def model_b(sc: ScoreCard, v15: TimeframeView, sup: list[Level],
            cfg: dict) -> Signal | None:
    """支撑回踩做多（A 的镜像）。"""
    if v15.df is None or len(v15.df) < 10:
        return None
    df, price = v15.df, v15.price
    sh = shape(df.iloc[-1])
    eng = engulfing(df, -1)

    for lv in sorted(sup, key=lambda x: -x.price):
        if lv.weight < 1.1:
            continue
        if not _near(price, lv.price, NEAR_TOL):
            continue
        if price < lv.price * 0.998:
            continue
        # 镜像检查：K 线最低价必须触到支撑位附近（同样 100 点容差）
        if float(row["low"]) > lv.price + _touch_tol(cfg):
            continue
        reject = sh.long_lower or eng == "bull" or (sh.is_bull and sh.close_pos > 0.55)
        if not reject:
            continue
        vr = _vol_ratio(df)
        ok_vol, size_mult = _vol_grade(vr, ok=1.0, weak=0.3)
        if not ok_vol:
            continue
        if (price - lv.price) / price > 0.01:
            continue
        stop_struct = lv.price * 0.995
        conf = 0.45 + (0.15 if sh.long_lower else 0) + (0.1 if eng == "bull" else 0) \
            + (0.1 if lv.weight >= 1.2 else 0) \
            + (0.05 if vr >= 1.5 else (-0.05 if vr < 1.0 else 0))
        # 指标共振（做多的镜像）：RSI 超卖 / MACD 动能转强 / 触及布林下轨
        ind = 0.0
        notes = [f"触及{lv}", f"下影={sh.lower_wick:.0f}",
                 f"收于区间{sh.close_pos:.0%}", f"量比{vr:.2f}"]
        if v15.rsi <= 35:
            ind += 0.08
            notes.append(f"RSI{v15.rsi:.0f}超卖")
        if v15.macd_hist > v15.macd_hist_prev:
            ind += 0.05
            notes.append("MACD柱转强")
        if v15.bb_low > 0 and abs(price - v15.bb_low) / price <= 0.003:
            ind += 0.05
            notes.append("触及布林下轨")
        conf += ind
        return Signal(
            side="long", model="B", entry=round(price, 1),
            stop=round(stop_struct, 1), targets=[],
            confidence=min(conf, 0.9), size_mult=size_mult, vol_ratio=vr,
            reasons=notes,
            structure=max((price - stop_struct) / price, 0.005),
        )
    return None


def model_c(sc: ScoreCard, v15: TimeframeView, sup: list[Level],
            cfg: dict) -> Signal | None:
    """假跌破做多：跌破前低后收回 + 长下影 + 放量。"""
    if not sc.counter_trend_allowed:
        return None
    if v15.df is None or len(v15.df) < 25:
        return None
    df, price = v15.df, v15.price
    row = df.iloc[-1]
    sh = shape(row)

    # 最近 20 根（不含当前）的最低点作为"前低"
    prior_low = float(df["low"].iloc[-21:-1].min())
    broke = float(row["low"]) < prior_low
    reclaimed = price > prior_low
    if not (broke and reclaimed):
        return None
    if not sh.long_lower:
        return None
    vr = _vol_ratio(df)
    ok_vol, size_mult = _vol_grade(vr, ok=1.2, weak=0.5)
    if not ok_vol:
        return None

    stop_struct = float(row["low"]) * 0.995
    if (price - stop_struct) / price < 0.004:
        return None
    conf = 0.5 + (0.15 if sh.long_lower else 0) + (0.1 if sh.close_pos > 0.6 else 0) \
        + (0.05 if vr >= 2.0 else 0)
    return Signal(
        side="long", model="C", entry=round(price, 1), stop=round(stop_struct, 1),
        targets=[], counter_trend=True, confidence=min(conf, 0.9),
        size_mult=size_mult, vol_ratio=vr,
        reasons=[f"下破前低{prior_low:,.0f}后收回", f"下影={sh.lower_wick:.0f}",
                 f"量比{vr:.2f}"],
        structure=(price - stop_struct) / price,
    )


def model_d(sc: ScoreCard, v15: TimeframeView, res: list[Level],
            cfg: dict) -> Signal | None:
    """假突破做空：突破前高后收回 + 长上影 + 放量。"""
    if not sc.counter_trend_allowed:
        return None
    if v15.df is None or len(v15.df) < 25:
        return None
    df, price = v15.df, v15.price
    row = df.iloc[-1]
    sh = shape(row)

    prior_high = float(df["high"].iloc[-21:-1].max())
    broke = float(row["high"]) > prior_high
    reclaimed = price < prior_high
    if not (broke and reclaimed):
        return None
    if not sh.long_upper:
        return None
    vr = _vol_ratio(df)
    ok_vol, size_mult = _vol_grade(vr, ok=1.2, weak=0.5)
    if not ok_vol:
        return None

    stop_struct = float(row["high"]) * 1.005
    if (stop_struct - price) / price < 0.004:
        return None
    conf = 0.5 + (0.15 if sh.long_upper else 0) + (0.1 if sh.close_pos < 0.4 else 0) \
        + (0.05 if vr >= 2.0 else 0)
    return Signal(
        side="short", model="D", entry=round(price, 1), stop=round(stop_struct, 1),
        targets=[], counter_trend=True, confidence=min(conf, 0.9),
        size_mult=size_mult, vol_ratio=vr,
        reasons=[f"上破前高{prior_high:,.0f}后收回", f"上影={sh.upper_wick:.0f}",
                 f"量比{vr:.2f}"],
        structure=(stop_struct - price) / price,
    )


# ---------------- 调度 ----------------
def trace_model_a(v15: TimeframeView, res: list[Level]) -> str:
    """把模型 A 的漏斗逐条打印出来，用于线上排障。"""
    df, price = v15.df, v15.price
    if df is None or len(df) < 10:
        return "df 不足"
    sh = shape(df.iloc[-1])
    eng = engulfing(df, -1)
    vr = _vol_ratio(df)
    qual = [x for x in res if x.weight >= 1.1]
    near = [x for x in qual if _near(price, x.price, NEAR_TOL)]
    parts = [
        f"价{price:,.0f}",
        f"合格压力{len(qual)}个",
        f"邻域{len(near)}个",
        f"最近={min(qual, key=lambda x: x.price).price:,.0f}"
        f"({(min(qual, key=lambda x: x.price).price-price)/price*100:+.2f}%)" if qual else "无压力",
        f"量比{vr:.2f}",
        f"上影{sh.upper_wick:.1f}/实体{sh.body:.1f}",
        f"收盘位{sh.close_pos:.2f}",
        f"拒绝={'Y' if (sh.long_upper or eng == 'bear' or (not sh.is_bull and sh.close_pos < 0.45)) else 'N'}",
    ]
    return " ".join(parts)


def detect(sc: ScoreCard, l3: dict, liq: liqmod.LiqView, cfg: dict,
           dbg=None) -> Signal | None:
    """按优先级依次尝试模型，返回第一个满足的。"""
    if sc.veto:
        if dbg:
            dbg(f"   [trace] VETO {sc.veto}")
        return None
    v15, v1h = l3.get("15m"), l3.get("1h")
    if not (v15 and v1h):
        if dbg:
            dbg("   [trace] 缺少 15m/1h 视图")
        return None
    price = v15.price
    _res, _sup = collect_levels(v15, v1h, liq, price,
                                extra={"4h": l3.get("4h"), "1d": l3.get("1d")})
    # 触发与展示同源：先剔除噪声与密集区，避免在 0.1% 外的清算网格上开仓
    res, sup = display_levels(_res, _sup, price, n=8)

    bias = sc.bias                 # L1+L4 的主方向；不再用它做硬性门控
    cands: list[Signal] = []

    # 四个模型全部评估。逆势不再被"禁止"，而是被"减半"——
    # 这样清算结构反向时，系统仍能先做一段轧空/杀多。
    for fn, arg in ((model_a, res), (model_b, sup), (model_c, sup), (model_d, res)):
        s = fn(sc, v15, arg, cfg)
        if s:
            cands.append(s)

    if dbg and not cands:
        dbg("   [trace] A漏斗: " + trace_model_a(v15, res))
        dbg(f"   [trace] 主方向={bias} 支撑位{len(sup)}个 压力位{len(res)}个")

    if not cands:
        return None

    # ---------- 第一层：主方向与日线 ----------
    for s in cands:
        # 逆主方向
        if bias in ("short", "long") and s.side != bias:
            s.counter_trend = True
            if not sc.counter_trend_allowed:
                s.size_mult = 0.0      # 强趋势且 L2 无支撑 → 放弃
                s.reasons.append("逆强趋势且L2无支撑→放弃")
            else:
                # 逆势折扣只在这里打一次；risk.finalize 不再重复乘 counter_trend
                s.size_mult *= cfg.get("counter_trend_mult", 0.5)
                s.reasons.append("逆主方向→半仓")
            s.confidence -= 0.05
        # 逆日线
        if sc.d1_score != 0 and ((s.side == "long" and sc.d1_score < 0)
                                 or (s.side == "short" and sc.d1_score > 0)):
            s.size_mult *= cfg.get("d1_counter_trend_mult", 0.5)
            s.confidence -= 0.05
            s.reasons.append(f"逆日线({sc.d1_score:+d})→减半")

    cands = [s for s in cands if s.size_mult > 0]
    if not cands:
        if dbg:
            dbg("   [trace] 全部候选被逆势规则剔除")
        return None

    # 逆势仓位的下限：逆主方向×0.5 × 逆日线×0.5 × 逆L2×0.5 = 0.125 其实等于不做。
    # 要给"双向交易"留出真实可执行的仓位，把系数钳到 >= 0.25（最差情况等于 1/4 主仓）。
    floor = float(cfg.get("counter_size_floor", 0.25))
    for s in cands:
        if s.counter_trend and 0 < s.size_mult < floor:
            s.reasons.append(f"逆势仓位下限 {s.size_mult:.3f}→{floor:.2f}")
            s.size_mult = floor

    # 与 L3 一致性加权：
    #   同向（4h/1h 都朝信号方向） → +0.10
    #   分歧（4h/1h 互相打架）     →  0.00
    #   反向（4h/1h 都逆着信号）   → -0.15
    for s in cands:
        if s.side == "long":
            agree, oppose = sc.l3_aligned == "up", sc.l3_aligned == "down"
        else:
            agree, oppose = sc.l3_aligned == "down", sc.l3_aligned == "up"
        if agree:
            s.confidence += 0.10
        elif oppose:
            s.confidence -= 0.15
        s.confidence = max(0.0, min(0.95, s.confidence))
    # ---------- 第二层：L2 持仓/清算结构 ----------
    #   同向（L2 指向信号方向）→ 仓位 ×1.0，置信度 +0.05
    #   反向（L2 指向相反方向）→ **仓位 ×0.5**，置信度 -0.05
    # 这是把"清算地图 + 散户/大户背离"真正接入决策的地方：
    # 即使 L1 明确看空，只要 L2 显示上方有厚燃料/散户过度拥挤反向，就自动减半。
    for s in cands:
        if s.side == "long":
            agree, oppose = sc.l2_total > 0, sc.l2_total < 0
        else:
            agree, oppose = sc.l2_total < 0, sc.l2_total > 0
        if agree:
            s.confidence += 0.05
            s.reasons.append(f"L2同向({sc.l2_total:+d})")
        elif oppose:
            s.size_mult *= 0.5
            s.confidence -= 0.05
            s.reasons.append(f"L2反向({sc.l2_total:+d})→半仓")

    cands.sort(key=lambda x: x.confidence, reverse=True)
    return cands[0]
