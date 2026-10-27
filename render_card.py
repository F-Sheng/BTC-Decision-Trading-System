"""BTC 自动交易策略 V1 —— 中文决策报告图。

画布约束（实测）：宽 ≤1610、高 ≤1428、面积 ≤约 167 万像素，超出会被预览缩放。
版式：顶部概览 → 中部（左六层 / 右决策路径）→ 底部通栏持仓横幅。
输出：桌面\\BTC决策报告\\YYYY-MM-DD\\
"""
from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PIL import Image, ImageDraw, ImageFont

from core import (coinglass_file, events, jin10, journal, liquidation,
                  live_macro, macro, market, positioning, risk, scoring,
                  sentiment, strategy, x_sentiment)
from core.okx import OkxClient
from core.utils import CST, LOG_DIR, ROOT, read_json, safe_div

BG, PANEL, PANEL2 = (13, 17, 23), (22, 27, 34), (27, 32, 39)
BAND = (38, 31, 20)
LINE, LINE2 = (44, 50, 58), (62, 70, 80)
FG, MUTED, DIM = (235, 241, 247), (152, 162, 174), (110, 120, 132)
GREEN, GREEN2 = (74, 222, 100), (129, 199, 132)
RED, RED2 = (255, 96, 84), (255, 138, 128)
AMBER, BLUE, PURPLE, CYAN = (245, 180, 70), (105, 185, 250), (205, 158, 255), (85, 212, 202)

FONT, FONTB = "C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/msyhbd.ttc"
W, M = 1600, 34
H = 1120
LW = 800
RX = M + LW + 18
RW = W - M - RX
SIDE = {"short": "做空", "long": "做多", "none": "无"}


def f(sz, bold=False):
    return ImageFont.truetype(FONTB if bold else FONT, sz)


def clip(d, text, font, maxw):
    if d.textlength(text, font=font) <= maxw:
        return text
    while text and d.textlength(text + "…", font=font) > maxw:
        text = text[:-1]
    return text + "…"


def level_of(score, maxabs):
    r = (score / maxabs) if maxabs else 0.0
    if r <= -0.55:
        return "强烈看空", RED
    if r <= -0.18:
        return "看空", RED2
    if r < 0.18:
        return "中性", MUTED
    if r < 0.55:
        return "看多", GREEN2
    return "强烈看多", GREEN


def side_cn(s):
    return SIDE.get(s, s)


def cross_cn(v):
    if not v:
        return "—"
    if v.macd_hist > 0 and v.macd_hist > v.macd_hist_prev:
        return "金叉"
    if v.macd_hist < 0 and v.macd_hist < v.macd_hist_prev:
        return "死叉"
    return "多头" if v.macd_hist > 0 else "空头"


def rsi_cn(v):
    if not v:
        return "—"
    z = "超卖" if v.rsi < 30 else ("超买" if v.rsi > 70 else "中性")
    return f"{v.rsi:.0f}（{z}）"


def fng_cn(v):
    if v is None:
        return "—"
    return ("极度恐惧" if v <= 25 else "恐惧" if v <= 45 else "中性"
            if v <= 55 else "贪婪" if v <= 75 else "极度贪婪")


def gather():
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
        eq, imr = float(b.get("totalEq") or 0), float(b.get("imr") or 0)
    except Exception:
        pass
    st = journal.load_state()
    heads = events.fetch_headlines()
    jf = jin10.fetch()
    sn = sentiment.load(jin10_score=jf.score, cfg=cfg)
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
    p = None
    try:
        p = positioning.fetch(funding=float(fr["fundingRate"]))
        try:
            p.price_change_pct = (price / float(tick["open24h"]) - 1) * 100
        except Exception:
            pass
        lv.pos = p
    except Exception:
        pass
    lv.senti = sn
    cg = coinglass_file.load()
    from core import liq_live
    ll = liq_live.load()
    xv = x_sentiment.load(cfg)
    dfs = market.load_all(c, inst)
    l3 = {k: market.build_view(k, dfs[k]) for k in ("15m", "1h", "4h", "1d")}
    ev = events.compute(cfg, heads)
    sc = scoring.build(mv, lv, ev, l3, cfg)
    sig = strategy.detect(sc, l3, lv, cfg)
    plan = risk.finalize(sig, eq, cfg, lv, ct_val, lot, price,
                         open_positions=1 if st.position.is_open else 0,
                         used_margin_pct=safe_div(imr, eq)) if sig else None
    return dict(cfg=cfg, price=price, tick=tick, eq=eq, st=st, mv=mv, lm=lm,
                jf=jf, sn=sn, p=p, cg=cg, lv=lv, l3=l3, ev=ev, sc=sc,
                sig=sig, plan=plan, xv=xv, ct_val=ct_val, ll=ll)


def render(D):
    price, tick, eq, st = D["price"], D["tick"], D["eq"], D["st"]
    mv, lm, jf, sn = D["mv"], D["lm"], D["jf"], D["sn"]
    p, cg, lv, l3, ev, sc = D["p"], D["cg"], D["lv"], D["l3"], D["ev"], D["sc"]
    sig, plan, xv, cfg = D["sig"], D["plan"], D["xv"], D["cfg"]
    ll = D.get("ll")

    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    f_price, f_sec, f_lt = f(54, True), f(20, True), f(21, True)
    f_m, f_bd, f_sm = f(17), f(18, True), f(13)

    def section(x, y, text, color=CYAN, sub=""):
        d.rounded_rectangle([x, y + 3, x + 4, y + 22], radius=2, fill=color)
        d.text((x + 14, y), text, font=f_sec, fill=color)
        if sub:
            d.text((x + 14 + d.textlength(text, font=f_sec) + 12, y + 5),
                   sub, font=f(13), fill=DIM)

    # ================= 顶部 =================
    t0 = (cfg.get("run_started_at") or cfg.get("v1_started_at")
          or getattr(st, "started_at", 0) or 0)
    se = (cfg.get("run_start_equity") or cfg.get("v1_start_equity")
          or getattr(st, "start_equity", 0) or 0)
    run_h = (datetime.now(CST).timestamp() * 1000 - t0) / 3.6e6 if t0 else 0.0
    pnl = (eq - se) if se else 0.0
    pnl_pct = (pnl / se * 100) if se else 0.0
    pc_pnl = GREEN if pnl > 0 else (RED if pnl < 0 else MUTED)
    f_title = f(28, True)
    _ver = cfg.get("strategy_version", "V2")
    _title = f"BTC自动交易策略{_ver}"
    d.text((M, 16), _title, font=f_title, fill=FG)
    tx = M + d.textlength(_title, font=f_title) + 18
    d.text((tx, 24), f"运行 {run_h:.1f} 小时", font=f(17, True), fill=MUTED)
    tx += d.textlength(f"运行 {run_h:.1f} 小时", font=f(17, True)) + 20
    d.text((tx, 24), f"盈亏 {pnl:+,.2f} USDT（{pnl_pct:+.2f}%）",
           font=f(17, True), fill=pc_pnl)

    up = price >= float(tick["open24h"])
    pc = GREEN if up else RED
    chg = (price / float(tick["open24h"]) - 1) * 100
    d.text((M, 52), f"{price:,.1f}", font=f_price, fill=pc)
    pw = d.textlength(f"{price:,.1f}", font=f_price)
    d.text((M + pw + 12, 80), f"{chg:+.2f}%", font=f(20, True), fill=pc)

    d.text((W - M, 20), f"账户权益 {eq:,.0f} USDT", font=f(17, True), fill=FG, anchor="ra")
    d.text((W - M, 46), f"24小时区间 {float(tick['low24h']):,.0f} ~ {float(tick['high24h']):,.0f}",
           font=f_sm, fill=MUTED, anchor="ra")
    d.text((W - M, 66), datetime.now(CST).strftime("%Y-%m-%d %H:%M:%S 北京时间"),
           font=f_sm, fill=DIM, anchor="ra")
    d.line([(M, 122), (W - M, 122)], fill=LINE, width=1)

    # ================= 左：六层 =================
    section(M, 136, "六层判断", CYAN, "每层独立打分后转成五档结论")
    wti, nq, dxy = lm.get("wti"), lm.get("nasdaq"), lm.get("dxy")
    ci = {i.name: i for i in mv.items}

    def v_of(n, u="", nd=2):
        it = ci.get(n)
        return "—" if not it or it.value is None else f"{it.value:.{nd}f}{u}"

    v15, v1h, v4, v1d = l3.get("15m"), l3.get("1h"), l3.get("4h"), l3.get("1d")
    rr = cg.r_ratio if cg.fresh else lv.r_ratio
    upm = cg.short_cum_next / 1e6 if cg.fresh else lv.total_above_3pct / 1e6
    upp = cg.short_trigger_next if cg.fresh else (lv.nearest_above.price if lv.nearest_above else 0)
    dnm = cg.long_cum_below / 1e6 if cg.fresh else lv.total_below_3pct / 1e6
    ldp = cg.long_trigger if cg.fresh else (lv.nearest_below.price if lv.nearest_below else 0)

    lv_l1 = level_of(sc.l1_total, 9)
    lv_l2f = level_of(sc.l2_score, 2)
    lv_l2c = level_of(sc.l2_crowd, 3)
    lv_l3 = level_of(sc.l3_bias, 3)
    lv_l4 = level_of(ev.adjust, 2)
    lv_senti = level_of(sn.score, 3)

    cg_src = f"CoinGlass 真实图 · {cg.age_min:.0f} 分钟前" if cg.fresh else "本地代理图（已过期）"
    _SHORT = {"FOMC 利率决议": "FOMC", "美国 CPI": "CPI",
              "BTC 期权月度到期": "月度期权", "BTC 期权周度到期": "周度期权"}
    ev_cal = "  ".join(
        _SHORT.get(str(e["name"]), str(e["name"])) + " T" + f"{e['days']:+d}"
        for e in ev.events[:2])
    cards = [
        (0, "宏观面", lv_l1, [
            f"核心通胀　{v_of('核心CPI同比','%')}　目标 2%",
            f"通胀动能　{v_of('核心CPI动能','pt')}　近3月加速",
            f"美原油　　{wti.last:.1f} 美元（{wti.chg_1d:+.2f}%）" if wti and wti.ok() else "美原油　—",
            f"纳指期货　{nq.last:,.0f}（{nq.chg_1d:+.2f}%）" if nq and nq.ok() else "纳指期货　—",
            f"美元指数　{dxy.last:.2f}" if dxy and dxy.ok() else "美元指数　—",
            f"地缘政治　{v_of('地缘政治','分',0)}",
        ]),
        (1, "资金博弈", lv_l2f, [
            f"上方 ±{cg.far_pct:.1f}%　空头 {upm:.0f}M",
            f"下方 ±{cg.far_pct:.1f}%　多头 {dnm:.0f}M",
            f"多空比　　R={rr:.2f}",
            f"来源　　　{cg_src}",
            (f"强平挤压　近1h 空爆${ll.short_usd_1h/1e6:.1f}M／多爆${ll.long_usd_1h/1e6:.1f}M"
             if ll.ok and ll.squeeze_ratio else "强平挤压　数据累积中"),
        ]),
        (2, "持仓结构", lv_l2c, [
            f"散户做多　{p.retail_long_pct:.0f}%" if p else "散户做多　—",
            f"大户做多　{p.top_long_pct:.0f}%" if p else "大户做多　—",
            f"多空背离　{p.divergence:+.1f} 点" if p else "多空背离　—",
            f"资金费率　{p.funding*100:.4f}%" if p else "资金费率　—",
            f"鲸鱼多空　{cg.whale_bias_binance:.2f}" if cg.fresh else "鲸鱼多空　—",
        ]),
        (3, "市场情绪", lv_senti, [
            f"恐惧贪婪　{sn.fng_value}（{fng_cn(sn.fng_value)}）" if sn.fng_value is not None else "恐惧贪婪　—",
            f"社区投票　{sn.gecko_up_pct or 0:.0f}% 看多",
            f"交易员　　{sn.st_bull} 多 / {sn.st_bear} 空",
            f"专业大V　{sn.x_pro_score:+.2f}（{sn.x_pro_n} 条）",
            f"散户反向　{sn.x_retail_score:+.2f}（{sn.x_retail_n} 条）",
            f"金十快讯　{sn.jin10_score:+d}",
        ]),
        (4, "技术形态", lv_l3, [
            f"日线　　　{cross_cn(v1d)}　RSI {rsi_cn(v1d)}",
            f"4 小时　　{cross_cn(v4)}　RSI {rsi_cn(v4)}",
            f"1 小时　　{cross_cn(v1h)}",
            f"15 分钟　　{cross_cn(v15)}",
            f"日均波幅　{v1d.atr:,.0f} 美元" if v1d else "日均波幅　—",
        ]),
        (5, "事件驱动", lv_l4, [
            f"近期日历　{ev_cal or '无'}",
            f"新闻情绪　{ev.sentiment.get('score')}",
            f"突发消息　{len(ev.shock)} 条",
            f"静默期　　{'是' if ev.blackout else '否'}",
        ]),
    ]

    y = 172
    cwid = (LW - 12) / 2
    ends = []
    for colcards, xoff in (([c for c in cards if c[0] % 2 == 0], 0),
                           ([c for c in cards if c[0] % 2 == 1], cwid + 12)):
        yy = y
        for _, name, (label, colr), rows in colcards:
            hgt = 44 + len(rows) * 25 + 8
            x0 = M + xoff
            d.rounded_rectangle([x0, yy, x0 + cwid, yy + hgt], radius=12, fill=PANEL)
            d.rounded_rectangle([x0, yy, x0 + cwid, yy + hgt], radius=12, outline=LINE, width=1)
            d.rounded_rectangle([x0 + 1, yy + 16, x0 + 6, yy + hgt - 16], radius=3, fill=colr)
            d.text((x0 + 18, yy + 11), name, font=f_lt, fill=BLUE)
            tw = d.textlength(label, font=f_bd)
            bx = x0 + cwid - tw - 30
            d.rounded_rectangle([bx, yy + 10, x0 + cwid - 14, yy + 38], radius=8,
                                fill=(colr[0] // 8, colr[1] // 8, colr[2] // 8))
            d.text((bx + 8, yy + 13), label, font=f_bd, fill=colr)
            for k, txt in enumerate(rows):
                d.text((x0 + 18, yy + 44 + k * 25), clip(d, txt, f_m, cwid - 30),
                       font=f_m, fill=FG if k == 0 else MUTED)
            yy += hgt + 10
        ends.append(yy - 10)

    # ================= 右：决策路径 / 价位 / 声音 =================
    ry = 136
    section(RX, ry, "决策路径", CYAN, "为什么得出这个结论")
    ry += 34

    steps = [
        ("① 宏观定方向",
         (f"通胀 {v_of('核心CPI同比','%')} 高于 2%，油价 {wti.chg_1d:+.2f}%、"
          f"纳指 {nq.chg_1d:+.2f}%、美元 {dxy.last:.2f}")
         if (wti and wti.ok() and nq and nq.ok() and dxy and dxy.ok()) else "通胀偏高、外部环境偏紧",
         "钱变贵、风险资产承压", lv_l1),
        ("② 资金定路径",
         (f"±{cg.far_pct:.1f}% 上空头 {upm:.0f}M、下多头 {dnm:.0f}M，比值 {rr:.2f}；"
          f"散户 {p.retail_long_pct:.0f}% 做多") if p else f"上空头 {upm:.0f}M、下多头 {dnm:.0f}M",
         "两边都会爆仓、力量接近，但多头更拥挤", lv_l2f),
        ("③ 技术定时机",
         f"日线 {cross_cn(v1d)}、4 小时 {cross_cn(v4)}、日线 RSI {v1d.rsi:.0f}" if v1d else "日线走弱",
         "趋势向下，反抽就是空点", lv_l3),
        ("④ 事件定节奏",
         (f"{ev.events[0]['name']} T{ev.events[0]['days']:+d}，新闻情绪 {ev.sentiment.get('score')}"
          if ev.events else f"无重大事件，新闻情绪 {ev.sentiment.get('score')}"),
         "波动会放大，仓位要收着", lv_l4),
    ]
    for title, fact, why, (label, colr) in steps:
        bh = 64
        d.rounded_rectangle([RX, ry, W - M, ry + bh], radius=10, fill=PANEL)
        d.rounded_rectangle([RX, ry, W - M, ry + bh], radius=10, outline=LINE, width=1)
        d.text((RX + 14, ry + 8), title, font=f(15, True), fill=CYAN)
        tw = d.textlength(label, font=f_bd)
        d.rounded_rectangle([W - M - tw - 24, ry + 7, W - M - 12, ry + 33], radius=7,
                            fill=(colr[0] // 8, colr[1] // 8, colr[2] // 8))
        d.text((W - M - tw - 18, ry + 9), label, font=f_bd, fill=colr)
        d.text((RX + 14, ry + 30), clip(d, fact, f(14), RW - 30), font=f(14), fill=MUTED)
        d.text((RX + 14, ry + 47), clip(d, "→ " + why, f(14), RW - 30), font=f(14), fill=FG)
        ry += bh + 8

    labels = [s[3][0] for s in steps]
    bears = sum(1 for x in labels if "空" in x)
    bulls = sum(1 for x in labels if "多" in x)
    verdict, vcol = ("做空", RED) if bears > bulls else (("做多", GREEN) if bulls > bears else ("观望", MUTED))
    d.rounded_rectangle([RX, ry, W - M, ry + 50], radius=10, fill=(31, 37, 45))
    d.rounded_rectangle([RX, ry, W - M, ry + 50], radius=10, outline=LINE2, width=1)
    d.text((RX + 14, ry + 8), "四层汇总", font=f(14, True), fill=CYAN)
    d.text((RX + 14, ry + 27),
           f"{bears} 层看空 · {bulls} 层看多 · {4-bears-bulls} 层中性　→　"
           f"主方向 {side_cn(sc.direction)}，本周期 {verdict}",
           font=f(15, True), fill=vcol)
    ry += 62

    section(RX, ry, "关键价位", CYAN)
    ry += 30
    _r, _s = strategy.collect_levels(v15, v1h, lv, price,
                                       extra={"4h": l3.get("4h"), "1d": l3.get("1d")}) \
        if (v15 and v1h) else ([], [])
    res, sup = strategy.display_levels(_r, _s, price, n=5)
    for label, lst, c in (("压力", res, RED), ("支撑", sup, GREEN)):
        d.text((RX, ry + 5), label, font=f(14, True), fill=c)
        cx = RX + 42
        for x in lst:
            t = f"{x.price:,.0f}"
            tw = d.textlength(t, font=f(14, True))
            d.rounded_rectangle([cx, ry, cx + tw + 16, ry + 26], radius=7,
                                fill=(c[0] // 8, c[1] // 8, c[2] // 8))
            d.text((cx + 8, ry + 4), t, font=f(14, True), fill=c)
            cx += tw + 20
        ry += 32

    ry += 8
    section(RX, ry, "市场声音", CYAN)
    ry += 28
    feeds = [("推特", tp, PURPLE) for tp in xv.top[:2]]
    feeds += [("金十", f"{x['time']} {x['text']}", RED if x["level"] == 2 else AMBER)
              for x in jf.lst[:1]]
    for tag, txt, c in feeds:
        d.rounded_rectangle([RX, ry + 3, RX + 40, ry + 22], radius=6,
                            fill=(c[0] // 8, c[1] // 8, c[2] // 8))
        d.text((RX + 9, ry + 3), tag, font=f(11, True), fill=c)
        d.text((RX + 48, ry + 3), clip(d, txt, f(13), RW - 56), font=f(13), fill=MUTED)
        ry += 23

    # ================= 最近信号（含未执行原因） =================
    try:
        _rows = journal._conn().execute(
            "SELECT ts, model, side, entry, conf, accepted FROM signals "
            "ORDER BY ts DESC LIMIT 2").fetchall()
    except Exception:
        _rows = []
    if _rows:
        ry += 4
        d.text((RX, ry), "最近信号", font=f(16, True), fill=CYAN)
        ry += 24
        for _ts, _m, _side, _ent, _conf, _acc in _rows:
            _t = datetime.fromtimestamp(_ts / 1000, CST).strftime("%H:%M")
            _txt = (f"{_t}  模型{_m} {side_cn(_side)} @{_ent:,.0f}  "
                    f"置信{_conf:.2f}  {'已执行' if _acc else '未执行'}")
            d.text((RX, ry), clip(d, _txt, f(13), RW - 6), font=f(13),
                   fill=GREEN if _acc else MUTED)
            ry += 20
        d.text((RX, ry - 2), "未执行 = 已有持仓（单仓位模式）或反向力度不足",
               font=f(11), fill=DIM)

    # ================= 底部通栏：持仓与信号 =================
    top = max(max(ends), ry) + 10
    bh = 210
    d.rounded_rectangle([M, top, W - M, top + bh], radius=14, fill=BAND)
    d.rounded_rectangle([M, top, W - M, top + bh], radius=14, outline=AMBER, width=3)

    d.text((M + 22, top + 14), "持仓与信号", font=f(21, True), fill=AMBER)
    _sides = st.open_sides() if hasattr(st, "open_sides") else (["short"] if st.position.is_open else [])
    hint = ("当前无持仓" if not _sides
            else f"持仓中（{len(_sides)} 边，双向模式）· 本框为最核心信息")
    d.text((M + 22 + d.textlength("持仓与信号", font=f(21, True)) + 16, top + 20),
           hint, font=f(13), fill=MUTED)

    if _sides and hasattr(st, "get_pos"):
        _dual = len(_sides) > 1
        _ctv = float(D['ct_val'])
        _lev = float(cfg.get("leverage") or 10)
        _rowh = 54 if _dual else 86
        _fval = f(18, True) if _dual else f(25, True)
        _y = top + 42
        # 右侧止盈/止损独立方框
        _boxw, _boxh = 236, (_rowh - 14) if _dual else 66
        for _sd in ("long", "short"):
            if _sd not in _sides:
                continue
            pos = st.get_pos(_sd)
            _ctv = float(D['ct_val'])
            # 保证金 = 名义 / 杠杆；名义 = 张数 × 合约面值 × 开仓价
            _notional = pos.size * _ctv * pos.entry
            _margin = _notional / _lev
            _qty_btc = pos.size * _ctv
            _pnl_usd = ((price - pos.entry) if pos.side == "long"
                        else (pos.entry - price)) * _ctv * pos.size
            _pnl_pct = _pnl_usd / _margin if _margin else 0.0
            _accent = GREEN if pos.side == "long" else RED
            _pnl_col = GREEN if _pnl_usd > 0 else (RED if _pnl_usd < 0 else FG)

            d.rounded_rectangle([M + 22, _y - 4, M + 30, _y + _rowh - 20],
                                radius=3, fill=_accent)
            cells = [
                ("", side_cn(pos.side), _accent),
                ("保证金", f"{_margin:,.0f}", "#F0C46A"),
                ("实际持仓", f"{_notional:,.0f}", FG),
                ("开仓价", f"{pos.entry:,.1f}", FG),
                ("现价", f"{price:,.1f}", FG),
                ("本仓盈亏", f"{_pnl_usd:+,.0f} U", _pnl_col),
                ("盈利率", f"{_pnl_pct*100:+.2f}%", _pnl_col),
            ]
            _cw_total = W - 2 * M - 44 - _boxw - 14
            cw = (_cw_total - 7 * 8) / len(cells)
            for i, (k, v, col) in enumerate(cells):
                x = M + 36 + i * (cw + 8)
                d.rounded_rectangle([x, _y, x + cw, _y + _rowh - 16],
                                    radius=8, fill=(52, 42, 26))
                if k:
                    _kl = {"保证金": f"保证金·{_lev:g}x"}.get(k, k)
                    d.text((x + 10, _y + 4), _kl, font=f(11), fill=MUTED)
                    d.text((x + 10, _y + 19), clip(d, v, _fval, cw - 16), font=_fval, fill=col)
                else:
                    d.text((x + 10, _y + 11), clip(d, v, _fval, cw - 16), font=_fval, fill=col)

            # 止盈 / 止损 方框（上止盈、下止损）
            _bx = W - M - 22 - _boxw
            d.rounded_rectangle([_bx, _y, _bx + _boxw, _y + _rowh - 16],
                                radius=8, fill=(44, 36, 22), outline=AMBER, width=1)
            _tp = pos.targets[0] if pos.targets else 0
            _half = (_rowh - 16) / 2
            _fs = f(13) if _dual else f(14)
            _fv = f(19, True) if _dual else f(23, True)
            d.text((_bx + 12, _y + 5), "止盈", font=_fs, fill=GREEN)
            d.text((_bx + _boxw - 12 - d.textlength(f"{_tp:,.0f}", font=_fv), _y + 4),
                   f"{_tp:,.0f}", font=_fv, fill=GREEN)
            d.text((_bx + 12, _y + 4 + _half), "止损", font=_fs, fill=RED)
            d.text((_bx + _boxw - 12 - d.textlength(f"{pos.stop:,.0f}", font=_fv), _y + 3 + _half),
                   f"{pos.stop:,.0f}", font=_fv, fill=RED)
            _y += _rowh

        _tot = sum(st.get_pos(s_).size * D['ct_val'] * price for s_ in _sides)
        _tot_margin = sum(st.get_pos(s_).size * D['ct_val'] * st.get_pos(s_).entry
                          for s_ in _sides) / float(cfg.get("leverage") or 10)
        _tot_pnl = sum(
            ((price - st.get_pos(s_).entry) if s_ == "long"
             else (st.get_pos(s_).entry - price)) * D['ct_val'] * st.get_pos(s_).size
            for s_ in _sides)
        line2 = (f"合计 保证金 {_tot_margin:,.0f} U　｜　名义 {_tot:,.0f} U　｜　"
                 f"合计盈亏 {_tot_pnl:+,.0f} U　｜　{_lev:g}x 杠杆")
        d.text((M + 22, _y + 2), clip(d, line2, f(14), W - 2 * M - 44), font=f(14), fill=MUTED)
        yb = _y + 24
    else:
        d.rounded_rectangle([M + 22, top + 50, W - M - 22, top + 120], radius=10, fill=(52, 42, 26))
        d.text((M + 40, top + 72), "当前空仓，等待信号触发", font=f(24, True), fill=MUTED)
        yb = top + 132

    if sig and plan and plan.ok:
        ps = plan.signal
        kind = ("逆势做多" if (ps.side == "long" and ps.counter_trend) else
                "逆势做空" if (ps.side == "short" and ps.counter_trend) else side_cn(ps.side))
        scol = RED if ps.side == "short" else GREEN
        d.rounded_rectangle([M + 22, yb, W - M - 22, yb + 36], radius=8,
                            fill=(scol[0] // 9, scol[1] // 9, scol[2] // 9))
        d.text((M + 36, yb + 7),
               f"本周期新信号：{kind}　入场 {ps.entry:,.1f}　止损 {ps.stop:,.1f}"
               f"（{plan.stop_pct*100:.2f}%）　仓位 ×{ps.size_mult:g}　置信度 {sig.confidence:.2f}",
               font=f(16, True), fill=scol)
    else:
        d.rounded_rectangle([M + 22, yb, W - M - 22, yb + 36], radius=8, fill=(45, 40, 30))
        d.text((M + 36, yb + 7), "本周期无新开仓信号（未触及关键位或条件不足）",
               font=f(16), fill=MUTED)

    # ================= 页脚 =================
    d.line([(M, top + bh + 14), (W - M, top + bh + 14)], fill=LINE, width=1)
    foot = (f"参数 10 倍杠杆 · 止损 {cfg['stop_pct_min']*100:.0f}–{cfg['stop_pct_max']*100:.0f}% · "
            f"单笔保证金 {cfg['margin_pct']*100:.0f}%　｜　刷新：价格60秒 宏观30分 清算5分 "
            f"情绪10分 清算图与推特2小时　｜　仅供研究，不构成投资建议")
    d.text((M, top + bh + 22), clip(d, foot, f(12), W - 2 * M), font=f(12), fill=DIM)

    out_h = min(top + bh + 50, 1030)
    img = img.crop((0, 0, W, out_h))

    now = datetime.now(CST)
    desk = Path(os.path.expanduser("~")) / "Desktop" / "BTC决策报告" / now.strftime("%Y-%m-%d")
    desk.mkdir(parents=True, exist_ok=True)
    out = desk / f"BTC策略{_ver}-{now.strftime('%H%M')}.png"
    img.save(out)
    try:
        (LOG_DIR / f"decision-{now.strftime('%Y%m%d-%H%M')}.png").write_bytes(out.read_bytes())
    except Exception:
        pass
    return str(out)


def main():
    print(render(gather()))


if __name__ == "__main__":
    main()
