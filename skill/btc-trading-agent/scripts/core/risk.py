"""L5 风控层：止损距离钳制、仓位推导、四条硬规则校验。

核心公式（与 v2.0 B 方案一致）：
    账户风险% = 价格止损% × 杠杆 × 保证金占账户%
"""
from __future__ import annotations

import copy
import math
from dataclasses import dataclass, field

from . import liquidation as liqmod
from .strategy import Signal


@dataclass
class Plan:
    ok: bool
    signal: Signal | None = None
    stop_pct: float = 0.0
    leverage: int = 10
    margin_pct: float = 0.20
    margin_usd: float = 0.0
    notional_usd: float = 0.0
    size_contracts: float = 0.0
    account_risk_pct: float = 0.0
    liq_buffer_pct: float = 0.0
    rejects: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def describe(self) -> str:
        if not self.ok:
            return "REJECT: " + "; ".join(self.rejects)
        return (f"PLAN {self.signal.side.upper()} size={self.size_contracts:.2f}张 "
                f"({self.notional_usd:,.0f}U) 保证金={self.margin_usd:,.0f}U "
                f"止损={self.stop_pct*100:.2f}% 账户风险={self.account_risk_pct*100:.2f}% "
                f"强平缓冲={self.liq_buffer_pct*100:.2f}%")


def liquidation_distance(leverage: int, mmr: float = 0.005) -> float:
    """强平距离（价格百分比）。"""
    return 1.0 / leverage - mmr


def clamp_stop(structure_pct: float, cfg: dict) -> tuple[float, str]:
    """把结构距离钳制到 [min,max]，并返回说明。"""
    lo = cfg["stop_pct_min"]
    hi = cfg["stop_pct_max"]
    if structure_pct > hi:
        return hi, f"结构距离{structure_pct*100:.2f}%>{hi*100:.0f}%，无法承载"
    if structure_pct < lo:
        return lo, f"结构距离{structure_pct*100:.2f}%<{lo*100:.0f}%，拉宽防插针"
    return structure_pct, f"采用结构距离{structure_pct*100:.2f}%"


def _inside_cluster(price: float, stop: float, liq: liqmod.LiqView,
                    tol: float = 0.003) -> str | None:
    """止损是否落在清算簇 ±0.3% 内。"""
    for lv in (liq.above or []) + (liq.below or []):
        if abs(stop - lv.price) / max(lv.price, 1e-9) <= tol:
            return f"止损{stop:,.0f}落在清算簇{lv.price:,.0f}±0.3%内"
    return None


def finalize(sig: Signal, equity: float, cfg: dict, liq: liqmod.LiqView,
             ct_val: float, lot_sz: float, price: float,
             open_positions: int = 0, used_margin_pct: float = 0.0,
             loss_streak: int = 0, dfs_source: dict | None = None) -> Plan:
    # 不在调用方对象上做就地修改（避免重复 finalize 时副作用累积）
    sig = copy.copy(sig)
    sig.targets = list(sig.targets)
    sig.reasons = list(sig.reasons)

    p = Plan(ok=False, signal=sig, leverage=cfg["leverage"])
    lev = int(cfg["leverage"])

    # --- R3/R4：并发与总保证金 ---
    if open_positions >= cfg["max_positions"]:
        p.rejects.append(f"已达最大持仓数{cfg['max_positions']}")
    # 仓位由"单笔风险预算"反推：risk = 止损% × 杠杆 × 保证金%
    # 先按 margin_pct 上限给一个基准，止损确定后再按风险目标回头修正
    base_margin_pct = (cfg["margin_pct"]
                       # 逆势折扣已在 strategy.detect 里打过一次，此处不再重复
                       * max(0.0, sig.size_mult))
    if sig.size_mult < 1.0:
        p.notes.append(f"量比{sig.vol_ratio:.2f}偏弱 → 仓位×{sig.size_mult:g}")
    if loss_streak >= cfg["loss_streak_halve"]:
        base_margin_pct *= 0.5
        p.notes.append("连亏减半")
    if used_margin_pct + base_margin_pct > cfg["max_total_margin_pct"] + 1e-9:
        p.rejects.append(f"总保证金将超{cfg['max_total_margin_pct']*100:.0f}%")

    # --- 止损距离 ---
    stop_pct, note = clamp_stop(sig.structure, cfg)
    p.notes.append(note)
    if sig.structure > cfg["stop_pct_max"]:
        p.rejects.append(f"结构止损{sig.structure*100:.2f}%超过上限")

    # 重新计算实际止损价（按钳制后的距离）
    if sig.side == "short":
        stop = sig.entry * (1 + stop_pct)
    else:
        stop = sig.entry * (1 - stop_pct)
    sig.stop = round(stop, 1)
    p.stop_pct = stop_pct

    # --- 按单笔风险预算反推保证金规模 ---
    # 目标：账户风险 = account_risk_target（默认 5%）
    # 于是 保证金% = 目标风险 ÷ (杠杆 × 止损%)
    target_risk = float(cfg.get("account_risk_target", 0.0) or 0.0)
    if target_risk > 0 and stop_pct > 0:
        want = target_risk / (lev * stop_pct)
        if want < base_margin_pct:
            p.notes.append(
                f"风险预算 {target_risk*100:.1f}% → 保证金 {base_margin_pct*100:.1f}%"
                f"降到 {want*100:.1f}%（止损{stop_pct*100:.2f}%×{lev}倍）")
            base_margin_pct = want
        else:
            p.notes.append(
                f"风险预算 {target_risk*100:.1f}% 要求保证金 {want*100:.1f}%，"
                f"受上限 {cfg['margin_pct']*100:.0f}% 约束，实际风险 "
                f"{stop_pct*lev*base_margin_pct*100:.2f}%")

    # --- R1：止损不在清算簇内（硬规则）---
    hit = _inside_cluster(sig.entry, sig.stop, liq)
    if hit:
        if cfg.get("r1_hard_reject", True):
            p.rejects.append(f"R1 {hit}")
        else:
            p.notes.append(f"⚠ {hit}（可交易但需注意）")

    # --- R2：强平缓冲 ---
    liq_dist = liquidation_distance(lev)
    buffer = liq_dist - stop_pct
    p.liq_buffer_pct = buffer
    if buffer < 0.5 * stop_pct:
        p.rejects.append(f"强平缓冲{buffer*100:.2f}%不足止损的0.5倍")

    # --- 仓位 ---
    margin_usd = equity * base_margin_pct
    notional = margin_usd * lev
    qty_coin = notional / sig.entry
    n = qty_coin / ct_val
    n = math.floor(n / lot_sz) * lot_sz
    if n <= 0:
        p.rejects.append("计算张数为0，权益或参数过小")

    p.margin_pct = base_margin_pct
    p.margin_usd = margin_usd
    p.notional_usd = notional
    p.size_contracts = n
    p.account_risk_pct = stop_pct * lev * base_margin_pct

    # --- 三档止盈：从真实结构算（前低/枢轴/成交量分布/爆仓热区/斐波那契） ---
    tg = None
    try:
        from . import levels as lvmod
        from . import liq_live, targets
        if dfs_source:
            h1 = dfs_source.get("1h")
            h4 = dfs_source.get("4h")
            piv = None
            hlc = lvmod._period_hlc(h1, "1D") if h1 is not None else None
            if hlc:
                piv = lvmod.pivot_points(*hlc)
            vp = lvmod.volume_profile(h4.tail(500)) if h4 is not None else None
            ll = liq_live.load()
            zones = ll.clusters_dn if sig.side == "short" else ll.clusters_up
            tg, tg_notes = targets.build(
                sig.side, sig.entry, stop, price or sig.entry,
                dfs_source, pivots=piv, vp=vp, liq_zones=zones, cfg=cfg)
            if tg:
                sig.targets = tg
                for n in tg_notes:
                    p.notes.append("目标：" + n)
    except Exception as e:
        p.notes.append(f"结构止盈计算失败({e})，退回R倍数")
        tg = None
    if not tg:
        r_mults = cfg.get("tp_r_multiples") or [0.5, 0.9, 1.3]
        dists = [stop_pct * m for m in r_mults]
        sign = -1 if sig.side == "short" else 1
        sig.targets = [round(sig.entry * (1 + sign * d), 1) for d in dists]

    p.ok = not p.rejects
    return p
