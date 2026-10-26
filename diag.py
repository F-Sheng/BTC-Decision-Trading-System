"""诊断：打印五层明细、关键价位、四个模型的逐条评估。用于调参与每日复盘。"""
from __future__ import annotations

import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import events, liquidation, macro, market, risk, scoring, strategy
from core.okx import OkxClient
from core.utils import ROOT, read_json, setup_logging

LOG = setup_logging("diag")
CFG = read_json(ROOT / "config.json", {}) or {}
SECRETS = read_json(ROOT / "secrets.json", {}) or {}
CFG["deepseek_key"] = SECRETS.get("deepseek_key", "")


def hr(t=""):
    print("\n" + "=" * 78)
    if t:
        print(t)
        print("=" * 78)


def main():
    c = OkxClient(simulated=True)
    inst = CFG["inst_id"]
    ins = c.instrument(inst)
    CFG["ct_val"] = float(ins["ctVal"])
    CFG["lot_sz"] = float(ins["lotSz"])

    tick = c.ticker(inst)
    price = float(tick["last"])
    equity = float(c.balance().get("totalEq") or 0)
    print(f"价格 {price:,.1f}   权益 {equity:,.0f} USDT   24H {tick['low24h']}~{tick['high24h']}")

    # ---------- L1 ----------
    hr("L1 宏观")
    idx = c.index_price("BTC-USDT")
    mv = macro.compute(c, okx_index=idx)
    for it in mv.items:
        v = f"{it.value:.2f}{it.unit}" if it.value is not None else "NA"
        print(f"  {it.name:<18} {v:>12}   {it.score:+d}   {it.note}")
    print(f"  → L1 基础分 = {mv.score:+d}")

    # ---------- L2 ----------
    hr("L2 清算（PROXY）")
    candles_1h = c.candles_bulk(inst, bar="1H", total=720)
    oi = c.open_interest(inst)
    fr = c.funding_rate(inst)
    ls = c.long_short_ratio("BTC")
    lv = liquidation.build_proxy(candles_1h, price, float(oi["oiCcy"]),
                                 float(fr["fundingRate"]),
                                 float(ls[0][1]) if ls else 0)
    print(f"  R={lv.r_ratio:.2f}   上方3%={lv.total_above_3pct/1e6:.1f}M  下方3%={lv.total_below_3pct/1e6:.1f}M")
    print(f"  上方簇: {', '.join(str(x) for x in lv.above[:4])}")
    print(f"  下方簇: {', '.join(str(x) for x in lv.below[:4])}")
    print(f"  OI={lv.oi_btc:,.0f}BTC  funding={lv.funding*100:.4f}%  LS={lv.ls_ratio:.2f}"
          f"  taker买={lv.taker_buy_ratio:.1%}")

    # ---------- L3 ----------
    hr("L3 技术（三周期）")
    dfs = market.load_all(c, inst)
    l3 = {}
    for k in ("15m", "1h", "4h", "1d"):
        v = market.build_view(k, dfs[k])
        l3[k] = v
        print(f"  [{k:>3}] MA={v.ma_score:+d} MACD={v.macd_score:+d} 结构={v.structure_score:+d}"
              f" → {v.score:+d} | RSI={v.rsi:.0f} BB宽={v.bb_width_pct*100:.2f}%"
              f" MA20={v.ma20:,.0f} ATR={v.atr:,.0f}")
        print(f"        摆动高={[f'{p:,.0f}' for _, p in v.swing_highs[-3:]]}"
              f" 摆动低={[f'{p:,.0f}' for _, p in v.swing_lows[-3:]]}")

    # ---------- L4 ----------
    hr("L4 事件")
    ev = events.compute(CFG)
    for e in ev.events[:6]:
        print(f"  {e['name']:<18} {e['date']}  T{e['days']:+d}  [{e['impact']}]")
    print(f"  新闻 {len(ev.news)} 条，其中冲击性 {len(ev.shock)} 条")
    for s in ev.shock[:5]:
        print(f"    ⚠ {s[:70]}")
    print(f"  情绪分 {ev.sentiment.get('score')}  理由: {ev.sentiment.get('reason')}")
    for f in ev.sentiment.get("risk_flags", [])[:3]:
        print(f"    ⚑ {f[:70]}")
    print(f"  → L4 修正 = {ev.adjust:+d}   blackout={ev.blackout}")

    # ---------- 综合 ----------
    hr("五层汇总")
    sc = scoring.build(mv, lv, ev, l3, CFG)
    print(f"  日线：{sc.d1_note}")
    print("  " + sc.brief())
    print(f"  veto: {sc.veto or '无'}")
    for n in sc.notes:
        print(f"  note: {n}")

    # ---------- 模型 ----------
    hr("模型评估")
    v15, v1h = l3["15m"], l3["1h"]
    res, sup = strategy.collect_levels(v15, v1h, lv, price)
    print(f"  压力位: {', '.join(str(x) for x in res[:6])}")
    print(f"  支撑位: {', '.join(str(x) for x in sup[:6])}")
    for name, fn, arg in (("A 阻力回抽空", strategy.model_a, res),
                          ("B 支撑回踩多", strategy.model_b, sup),
                          ("C 假跌破多", strategy.model_c, sup),
                          ("D 假突破空", strategy.model_d, res)):
        s = fn(sc, v15, arg, CFG)
        print(f"  {name:<12} → {s.describe() if s else '不满足'}")

    sig = strategy.detect(sc, l3, lv, CFG)
    hr("最终信号")
    if not sig:
        print("  无信号")
        return
    print("  " + sig.describe())
    for r in sig.reasons:
        print(f"    · {r}")
    plan = risk.finalize(sig, equity, CFG, lv, CFG["ct_val"], CFG["lot_sz"], price,
                         open_positions=0, used_margin_pct=0.0,
                         loss_streak=0)
    print("  " + plan.describe())
    for n in plan.notes:
        print(f"    note: {n}")


if __name__ == "__main__":
    main()
