"""统一决策汇报：一张总表 + 一行决策路径。

用法：
    python report.py            # 生成 logs/snapshot-YYYYMMDD-HHMM.md
    python report.py --stdout   # 直接打印
"""
from __future__ import annotations

import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import (coinglass_file, events, jin10, journal, liquidation,
                  live_macro, macro, market, positioning, risk, scoring,
                  sentiment, strategy)
from core.okx import OkxClient
from core.utils import CST, LOG_DIR, ROOT, read_json, safe_div


def _pct(v, nd=2, sign=True):
    if v is None:
        return "NA"
    return f"{v:+.{nd}f}%" if sign else f"{v:.{nd}f}%"


def build() -> str:
    cfg = read_json(ROOT / "config.json", {}) or {}
    sec = read_json(ROOT / "secrets.json", {}) or {}
    cfg["deepseek_key"] = sec.get("deepseek_key", "")

    c = OkxClient(simulated=True)
    inst = cfg["inst_id"]
    ins = c.instrument(inst)
    ct_val, lot = float(ins["ctVal"]), float(ins["lotSz"])

    tick = c.ticker(inst)
    price = float(tick["last"])
    eq = imr = 0.0
    try:
        b = c.balance()
        eq = float(b.get("totalEq") or 0)
        imr = float(b.get("imr") or 0)
    except Exception:
        pass
    st = journal.load_state()

    # 数据层
    heads = events.fetch_headlines()
    jf = jin10.fetch()
    sn = sentiment.load(jin10_score=jf.score)
    mv = macro.compute(c, okx_index=c.index_price("BTC-USDT"), heads=heads, cfg=cfg)
    lm = live_macro.load()

    c1 = c.candles_bulk(inst, "1H", 720)
    oi = c.open_interest(inst)
    fr = c.funding_rate(inst)
    ls = c.long_short_ratio("BTC")
    tv = c.taker_volume("BTC", limit=1)
    tb = 0.5
    if tv:
        s_, b_ = float(tv[0][1]), float(tv[0][2])
        tb = b_ / (b_ + s_) if (b_ + s_) else 0.5
    lv = liquidation.build_proxy(c1, price, float(oi["oiCcy"]),
                                 float(fr["fundingRate"]),
                                 float(ls[0][1]) if ls else 0, tb)
    try:
        p = positioning.fetch(funding=float(fr["fundingRate"]))
        try:
            p.price_change_pct = (price / float(tick["open24h"]) - 1) * 100
        except Exception:
            pass
        lv.pos = p
    except Exception:
        p = None
    lv.senti = sn
    cg = coinglass_file.load()

    dfs = market.load_all(c, inst)
    l3 = {k: market.build_view(k, dfs[k]) for k in ("15m", "1h", "4h", "1d")}
    ev = events.compute(cfg, heads)
    sc = scoring.build(mv, lv, ev, l3, cfg)
    sig = strategy.detect(sc, l3, lv, cfg)
    plan = risk.finalize(sig, eq, cfg, lv, ct_val, lot, price,
                         open_positions=1 if st.position.is_open else 0,
                         used_margin_pct=safe_div(imr, eq)) if sig else None

    now = datetime.now(CST)
    v15, v1h, v4, v1d = l3.get("15m"), l3.get("1h"), l3.get("4h"), l3.get("1d")
    q = lm
    wti, nq, vx, dxy = q.get("wti"), q.get("nasdaq"), q.get("vix"), q.get("dxy")

    def cross(v):
        if not v:
            return "NA"
        if v.macd_hist > 0 and v.macd_hist > v.macd_hist_prev:
            return "金叉"
        if v.macd_hist < 0 and v.macd_hist < v.macd_hist_prev:
            return "死叉"
        return "多头" if v.macd_hist > 0 else "空头"

    def ma_txt(v):
        if not v:
            return "NA"
        return "多" if v.ma_score > 0 else ("空" if v.ma_score < 0 else "缠")

    def rsi_txt(v):
        if not v:
            return "NA"
        z = "超卖" if v.rsi < 30 else ("超买" if v.rsi > 70 else "")
        return f"{v.rsi:.0f}{z}"

    L: list[str] = []
    A = L.append

    A(f"# BTC 决策台 · {now.strftime('%Y-%m-%d %H:%M')} CST")
    A("")
    A(f"**BTC {price:,.1f}**　24h {float(tick['low24h']):,.0f}~{float(tick['high24h']):,.0f}　"
      f"权益 {eq:,.0f} U　持仓 {'空' if st.position.is_open else '空仓'}")
    A("")

    # ============ 总表 ============
    A("## 一屏总览")
    A("")
    A("| 模块 | ① | ② | ③ | ④ | ⑤ | 评分 |")
    A("|---|---|---|---|---|---|---|")
    # 宏观
    ci = {i.name: i for i in mv.items}
    A(f"| **宏观 L1** "
      f"| CPI {ci['核心CPI同比'].value:.2f}% " if ci.get('核心CPI同比') and ci['核心CPI同比'].value is not None else "| **宏观 L1** | CPI NA ")
    L[-1] += (f"| 动能 {_pct(ci['核心CPI动能'].value if ci.get('核心CPI动能') else None)} "
              f"| 油价 {wti.last:.1f}({_pct(wti.chg_1d)}) " if wti and wti.ok() else "| 油价 NA ")
    L[-1] += (f"| 纳指 {nq.last:,.0f}({_pct(nq.chg_1d)}) " if nq and nq.ok() else "| 纳指 NA ")
    L[-1] += (f"| DXY {dxy.last:.2f} | **{mv.score:+d}→{sc.l1_total:+d}** |")
    # 清算
    rr = cg.r_ratio if cg.fresh else lv.r_ratio
    A(f"| **清算 L2** | 燃料比 R={rr:.2f} | "
      f"上方 {(cg.short_cum_next/1e6 if cg.fresh else lv.total_above_3pct/1e6):.0f}M "
      f"@{cg.short_trigger_next if cg.fresh else lv.nearest_above.price if lv.nearest_above else 0:,.0f} | "
      f"下方 {(cg.long_cum_below/1e6 if cg.fresh else lv.total_below_3pct/1e6):.0f}M | "
      f"{'CoinGlass' if cg.fresh else '代理'} {cg.age_min:.0f}m前 | "
      f"燃料{sc.l2_score:+d} | **{sc.l2_score:+d}** |")
    # 持仓结构
    if p:
        A(f"| **持仓结构** | 散户多 {p.retail_long_pct:.0f}% | 大户多 {p.top_long_pct:.0f}% | "
          f"背离 {_pct(p.divergence,1)} | 费率 {p.funding*100:.4f}% | "
          f"鲸鱼 {'%.2f' % cg.whale_bias_binance if cg.fresh else 'NA'} | **{sc.l2_crowd:+d}** |")
    # 情绪
    A(f"| **市场情绪** | 恐贪 {sn.fng_value or 'NA'}({sn.fng_label}) | "
      f"Gecko {sn.gecko_up_pct or 0:.0f}%多 | ST {sn.st_bull}多/{sn.st_bear}空 | "
      f"金十 {jf.score:+d} | {sn.cg_poll.get('very_bull','-') if sn.cg_poll else '-'} | "
      f"**{sn.score:+d}** |")
    # 技术
    A(f"| **技术 L3** | 15m {cross(v15)} {rsi_txt(v15)} | 1h {cross(v1h)} {rsi_txt(v1h)} | "
      f"4h {cross(v4)} {rsi_txt(v4)} | **日线 {cross(v1d)} {rsi_txt(v1d)}** | "
      f"ATR {v4.atr if v4 else 0:,.0f} | **{sc.l3_bias:+d}** |")
    # 事件
    ev_txt = " / ".join(
        f"{e['name'].replace('BTC 期权月度到期','月度期权').replace('BTC 期权周度到期','周度期权').replace('FOMC 利率决议','FOMC').replace('美国 CPI','CPI')} T{e['days']:+d}"
        for e in ev.events[:3])
    A(f"| **事件 L4** | {ev_txt} | 新闻情绪 {ev.sentiment.get('score')} | "
      f"冲击 {len(ev.shock)}条 | blackout {ev.blackout} | 日线分 {sc.d1_score:+d} | **{ev.adjust:+d}** |")
    # 结论
    if sig:
        tag = sig.side.upper() + ("(逆势)" if sig.counter_trend else "")
        concl = f"{tag} ×{sig.size_mult:g}"
    else:
        concl = "空仓等待" if not st.position.is_open else "持仓中"
    A(f"| **结论** | 主方向 **{sc.bias}** | 可交易 **{sc.direction}** | "
      f"本次信号 {concl} conf {sig.confidence if sig else 0:.2f} | "
      f"否决 {','.join(sc.veto) if sc.veto else '无'} | "
      f"**L1{sc.l1_total:+d}/L2{sc.l2_total:+d}/L3{sc.l3_bias:+d}/L4{sc.l4_adjust:+d}** | "
      f"**{'执行' if (plan and plan.ok and not st.position.is_open) else '观望'}** |")
    A("")

    # ============ 决策路径 ============
    A("## 决策路径")
    A("")
    A("```")
    A(f"L1 宏观    {mv.score:+d} 基础 {ev.adjust:+d} 事件 = {sc.l1_total:+d}  → {sc.l1_allow}")
    A(f"L2 清算    燃料 {sc.l2_score:+d}（R={sc.l2_r:.2f}） + 持仓 {sc.l2_crowd:+d} = {sc.l2_total:+d}")
    A(f"L3 技术    4H+1H {sc.l3_bias:+d} [{sc.l3_aligned}]   日线 {sc.d1_score:+d}")
    A(f"L4 事件    {ev.adjust:+d}")
    A(f"────────────────────────────────────────")
    A(f"bias={sc.bias}  direction={sc.direction}  veto={sc.veto or '无'}")
    A("```")
    A("")

    # ============ 关键位 ============
    if v15 and v1h:
        res, sup = strategy.collect_levels(v15, v1h, lv, price)
        A("**压力位**：" + (" | ".join(f"{x.price:,.0f}({x.source})" for x in res[:5]) or "无"))
        A("")
        A("**支撑位**：" + (" | ".join(f"{x.price:,.0f}({x.source})" for x in sup[:5]) or "无"))
        A("")

    # ============ 信号 ============
    if sig and plan:
        ps = plan.signal
        A(f"**信号** `{sig.describe()}`")
        A("")
        A("| 执行 | 方向 | 入场 | 止损 | 止盈 | 张数 | 保证金 | 账户风险 | 状态 |")
        A("|---|---|---|---|---|---|---|---|---|")
        A(f"| | {ps.side} | {ps.entry:,.1f} | {ps.stop:,.1f}({plan.stop_pct*100:.2f}%) | "
          f"{'/'.join(f'{t:,.0f}' for t in ps.targets)} | {plan.size_contracts:.1f} | "
          f"{plan.margin_usd:,.0f}U({plan.margin_pct*100:.0f}%) | "
          f"{plan.account_risk_pct*100:.2f}% | "
          f"{'可执行' if plan.ok else '拒绝:' + ';'.join(plan.rejects)} |")
    else:
        A("**无信号**（未触及关键位 / 形态量能不足 / 置信度不达标 / 已持仓）")
    A("")

    # ============ 持仓 ============
    if st.position.is_open:
        pos = st.position
        move = safe_div((price - pos.entry) if pos.side == "long" else (pos.entry - price), pos.entry)
        A("**当前持仓**")
        A("")
        A("| 方向 | 张数 | 开仓 | 现价 | 浮动 | 止损 | 止盈 | 已平档 | 模型 | 时长 |")
        A("|---|---|---|---|---|---|---|---|---|---|")
        A(f"| {pos.side} | {pos.size:.2f} | {pos.entry:,.1f} | {price:,.1f} | "
          f"**{move*100:+.2f}%** | {pos.stop:,.1f}({abs(pos.stop-price)/price*100:.2f}%) | "
          f"{'/'.join(f'{t:,.0f}' for t in pos.targets)} | {pos.tp_filled or '-'} | "
          f"{pos.model} | {(datetime.now(CST).timestamp()*1000-pos.opened_at)/3.6e6:.1f}h |")
        A("")

    # ============ 金十快讯 ============
    if jf.lst:
        A("**金十快讯（宏观相关）**")
        A("")
        for x in jf.lst[:6]:
            mark = "🔴" if x["level"] == 2 else "·"
            A(f"- {mark} `{x['time']}` {x['text'][:70]}")
        A("")

    # ============ 统计 ============
    s = journal.stats(30)
    A(f"---\n> 参数 10×/{cfg['stop_pct_min']*100:.0f}-{cfg['stop_pct_max']*100:.0f}%/"
      f"{cfg['margin_pct']*100:.0f}%｜近30日 {s['trades']} 笔 胜率 {s['win_rate']*100:.0f}% "
      f"均R {s['avg_r']:+.2f}｜{now.strftime('%H:%M:%S')} 生成")
    return "\n".join(L)


def main():
    txt = build()
    if "--stdout" in sys.argv:
        print(txt)
        return
    p = LOG_DIR / f"snapshot-{datetime.now(CST).strftime('%Y%m%d-%H%M')}.md"
    p.write_text(txt, encoding="utf-8")
    print(txt)
    print(f"\n... 已写入 {p}")


if __name__ == "__main__":
    main()
