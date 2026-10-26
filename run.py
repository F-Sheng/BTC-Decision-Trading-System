"""主循环：五层过滤 -> 信号 -> 风控 -> 执行 -> 管理。

用法：
    python run.py --once          # 跑一个周期（推荐首次）
    python run.py --dry-run       # 只出信号不下单
    python run.py                 # 常驻
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import (events, executor, journal, liquidation, macro, market, risk,
                  scoring, shadow, strategy)
from core.okx import OkxClient, OkxError
from core.utils import (CST, DATA_DIR, ROOT, append_jsonl, fmt_ts, guard,
                        now_cst, now_ms, read_json, setup_logging, write_json)

LOG = setup_logging("btc")
CFG = read_json(ROOT / "config.json", {}) or {}
SECRETS = read_json(ROOT / "secrets.json", {}) or {}
if SECRETS.get("deepseek_key"):
    CFG["deepseek_key"] = SECRETS["deepseek_key"]

BANNER = r"""
 ______   ______  ______     __     __
|  _  \ /  __  \|  ____|    \ \   / /
| |_) )| |  | || |____      \ \_/ /
|  __/ | |__| ||  ____|      \   /
|_|     \_____/|__|            |_|
 BTC 五层过滤系统 v2.0-B   10x / 3-5% SL / 20% margin
"""


# ---------------- 启动自检 ----------------
def boot() -> tuple[OkxClient, dict]:
    cfg = dict(CFG)
    if not cfg.get("simulated", True):
        LOG.error("配置为实盘模式，已拒绝启动。请确认后手动改 simulated=false 并移除本保护。")
        sys.exit(2)

    client = OkxClient(simulated=True)
    t_server = client.server_time()
    drift = abs(t_server - now_ms())
    LOG.info("服务器时间偏移 %d ms", drift)
    if drift > 3000:
        LOG.warning("本机与 OKX 时间偏差 >3s，签名可能失败")

    ins = client.instrument(cfg["inst_id"])
    cfg["ct_val"] = float(ins["ctVal"])
    cfg["lot_sz"] = float(ins["lotSz"])
    cfg["min_sz"] = float(ins["minSz"])
    cfg["tick_sz"] = float(ins["tickSz"])
    LOG.info("合约 %s ctVal=%s lotSz=%s", cfg["inst_id"], cfg["ct_val"], cfg["lot_sz"])

    try:
        client.set_leverage(cfg["inst_id"], cfg["leverage"], "cross")
        LOG.info("杠杆已设为 %dx", cfg["leverage"])
    except OkxError as e:
        LOG.warning("设置杠杆失败（可能已有持仓）：%s", e)

    return client, cfg


def equity_of(client: OkxClient) -> tuple[float, float]:
    try:
        b = client.balance()
        return float(b.get("totalEq") or 0), float(b.get("upl") or 0)
    except Exception:
        return 0.0, 0.0


def used_margin_pct(client: OkxClient, equity: float) -> float:
    try:
        b = client.balance()
        imr = float(b.get("imr") or 0)
        return imr / equity if equity else 0.0
    except Exception:
        return 0.0


# ---------------- 接管已有持仓 ----------------
def startup_positions(client: OkxClient, st: journal.BotState, cfg: dict) -> None:
    """处理启动前已存在的手工持仓。"""
    policy = cfg.get("position_policy", "observe")
    inst = cfg["inst_id"]
    try:
        poss = [p for p in client.positions() if abs(float(p.get("pos") or 0)) > 0]
    except Exception as e:
        LOG.warning("读取持仓失败: %s", e)
        return

    other = [p for p in poss if p["instId"] != inst]
    for p in other:
        LOG.info("发现其他合约持仓 %s pos=%s avgPx=%s（本系统不管理）",
                 p["instId"], p.get("pos"), p.get("avgPx"))

    mine = [p for p in poss if p["instId"] == inst]
    if not mine:
        LOG.info("启动时无 %s 持仓", inst)
        return
    if len(mine) > 1:
        LOG.info("双向模式：发现 %d 个方向持仓，逐个处理", len(mine))

    for _mi in mine:
        _adopt_one(client, st, cfg, inst, policy, _mi, journal)
    journal.save_state(st)
    return


def _adopt_one(client, st, cfg, inst, policy, mine, journal):
    raw = float(mine["pos"])
    avg = float(mine["avgPx"])
    # 双向模式下 pos 永远是正数，方向必须看 posSide
    _ps = mine.get("posSide")
    if _ps in ("long", "short"):
        side = _ps
    else:
        side = "long" if raw > 0 else "short"
    size = abs(raw)
    LOG.info("发现 %s 持仓: %s %.2f张 @%.1f 杠杆%s", inst, side, size, avg, mine.get("lever"))

    if st.get_pos(side).is_open:
        LOG.info("本地状态已有 %s 持仓，跳过接管", side)
        return

    if policy == "flatten":
        LOG.warning("策略=flatten，平掉该持仓")
        _tmp = journal.Position(side=side, entry=avg, size=size, model="ADOPT")
        executor.close_position(client, st, inst, cfg["lot_sz"], LOG, "policy_flatten",
                                avg, cfg["ct_val"], pos=_tmp)
        return

    if policy == "observe":
        LOG.warning("策略=observe，不接管，也不下单，直到该持仓清零")
        st.halted_until = 0
        return

    # adopt
    stop_base = cfg["stop_pct_max"]
    stop = avg * (1 + stop_base) if side == "short" else avg * (1 - stop_base)
    # 先清掉该合约上遗留的条件单，避免重复止损
    try:
        for a in client.algo_pending(inst):
            client.cancel_algo(a["algoId"], inst)
            LOG.info("撤销遗留条件单 algoId=%s sz=%s", a["algoId"], a.get("sz"))
    except Exception as e:  # noqa: BLE001
        LOG.warning("清理遗留条件单失败: %s", e)
    _apos = journal.Position(
        side=side, entry=avg, size=size, stop=round(stop, 1),
        targets=[], tp_filled=[], opened_at=now_ms(), model="ADOPT",
        counter_trend=False,
    )
    # 计算止盈
    tp = cfg["tp_pcts"]
    _apos.targets = [
        round(avg * (1 - tp[0]), 1) if side == "short" else round(avg * (1 + tp[0]), 1),
        round(avg * (1 - tp[1]), 1) if side == "short" else round(avg * (1 + tp[1]), 1),
        round(avg * (1 - tp[2]), 1) if side == "short" else round(avg * (1 + tp[2]), 1),
    ]
    LOG.info("已接管持仓，止损 %.1f，止盈 %s", stop, _apos.targets)
    try:
        a = client.place_algo_stop(inst, "buy" if side == "short" else "sell",
                                   executor._f(size, cfg["lot_sz"]),
                                   _apos.stop, pos_side=side,
                                   cl_ord_id=f"AD{int(time.time())%1000000}")
        _apos.algo_id = a.get("algoId", "")
        LOG.info("已为接管持仓挂上交易所止损 algoId=%s", _apos.algo_id)
    except OkxError as e:
        LOG.error("接管持仓挂止损失败: %s", e)
    journal.save_state(st)


# ---------------- 单周期 ----------------
@guard(LOG, "cycle")
def cycle(client: OkxClient, cfg: dict, st: journal.BotState, cache: dict,
          dry_run: bool = False) -> None:
    st.cycle_count += 1
    inst = cfg["inst_id"]
    lot = cfg["lot_sz"]

    tick = client.ticker(inst)
    price = float(tick["last"])
    equity, upl = equity_of(client)
    if equity <= 0:
        LOG.error("权益读取为 0，跳过本周期")
        return
    # ---- 当日回撤熔断 ----
    today = now_cst().strftime("%Y%m%d")
    if st.day_key != today:
        st.day_key = today
        st.day_start_equity = equity
        st.day_halted = False
        LOG.info("新交易日 %s 起始权益 %.0f", today, equity)
    cap = cfg.get("daily_loss_cap_pct", 0.05)
    if st.day_start_equity > 0 and not st.day_halted:
        dd = (equity - st.day_start_equity) / st.day_start_equity
        if dd <= -cap:
            st.day_halted = True
            LOG.warning("当日回撤 %.2f%% 触及 -%.0f%% 上限，今日停止开新仓",
                        dd * 100, cap * 100)
    st.equity_peak = max(st.equity_peak, equity)
    journal.log_equity(equity, upl)

    # ---- 持仓对账与托管 ----
    executor.reconcile(client, st, inst, price, LOG, cfg["ct_val"])

    # ---- 影子账户推进（无论哪种模式都在积累前向样本）----
    if "shadow" not in cache:
        cache["shadow"] = shadow.ShadowBook()
    cache["shadow"].update(price, cfg, LOG, equity)
    mode = cfg.get("execution_mode", "shadow")

    # 双向持仓：逐边管理
    for _side in st.open_sides():
        _p = st.get_pos(_side)
        reason = executor.manage(client, st, price, cfg, inst, lot, LOG,
                                 cfg["ct_val"], pos=_p)
        if reason in ("time_stop_full", "time_stop_half", "TP1_full", "TP2_full", "TP3_full"):
            if reason == "time_stop_half":
                executor.reduce_position(client, _p, _p.size * 0.5,
                                         inst, lot, LOG, "时间止损减半")
            else:
                executor.close_position(client, st, inst, lot, LOG, reason, price,
                                        cfg["ct_val"], pos=_p)
            journal.save_state(st)

    # ---- 定时刷新数据层 ----
    now = time.time()
    # 真实强平流：每个周期都尝试抓（模块内部自带 45 秒节流）
    try:
        from core import liq_live
        ll = liq_live.poll()
        cache["liq_live"] = ll
    except Exception as e:  # noqa: BLE001
        LOG.warning("真实强平流抓取失败: %s", e)

    if now - cache.get("liq_t", 0) > cfg["liq_refresh_sec"] or "liq" not in cache:
        candles_1h = client.candles_bulk(inst, bar="1H", total=720)
        oi = client.open_interest(inst)
        fr = client.funding_rate(inst)
        ls = client.long_short_ratio("BTC")
        tv = client.taker_volume("BTC")
        taker_buy = 0.5
        if tv:
            try:
                sell, buy = float(tv[0][1]), float(tv[0][2])
                taker_buy = buy / (buy + sell) if (buy + sell) else 0.5
            except Exception:
                pass
        cache["liq"] = liquidation.build_proxy(
            candles_1h, price,
            oi_btc=float(oi["oiCcy"]), funding=float(fr["fundingRate"]),
            ls_ratio=float(ls[0][1]) if ls else 0.0,
            taker_buy_ratio=taker_buy,
        )
        # 金十快讯 + 市场情绪
        try:
            from core import jin10 as jin10mod
            from core import sentiment as sentimod
            jf = jin10mod.fetch()
            cache["jin10"] = jf
            LOG.info("金十 %d条(高%d) 情绪%+d", len(jf.lst), jf.high, jf.score)
            cache["senti"] = sentimod.load(jin10_score=jf.score)
            LOG.info("市场情绪 %s", cache["senti"].summary())
        except Exception as e:  # noqa: BLE001
            LOG.warning("情绪源失败: %s", e)

        # 持仓结构层：散户/大户背离 + 费率 + OI 变化
        try:
            from core import positioning as posmod
            p = posmod.fetch(funding=float(fr["fundingRate"]))
            try:
                p.price_change_pct = (price / float(tick["open24h"]) - 1) * 100
            except Exception:
                pass
            cache["liq"].pos = p
            cache["liq"].senti = cache.get("senti")
            LOG.info("持仓结构 " + p.summary())
            for r in p.reasons:
                LOG.info("   · %s", r)
        except Exception as e:  # noqa: BLE001
            LOG.warning("持仓结构获取失败: %s", e)
        cache["liq_t"] = now
        LOG.info(cache["liq"].summary())

    if now - cache.get("macro_t", 0) > cfg["macro_refresh_sec"] or "macro" not in cache:
        idx = client.index_price("BTC-USDT")
        cache["macro"] = macro.compute(client, okx_index=idx, heads=cache.get("heads") or [], cfg=cfg)
        cache["macro_t"] = now
        LOG.info(cache["macro"].summary())

    if now - cache.get("event_t", 0) > cfg["news_refresh_sec"] or "event" not in cache:
        heads = events.fetch_headlines()
        cache["heads"] = heads
        cache["event"] = events.compute(cfg, heads)
        LOG.info("   新闻 %d 条，冲击 %d 条", len(heads), len(cache["event"].shock))
        cache["event_t"] = now
        LOG.info(cache["event"].summary())

    # ---- L3 ----
    if now - cache.get("l3_t", 0) > 120 or "l3" not in cache:
        dfs = market.load_all(client, inst)
        cache["l3"] = {k: market.build_view(k, v) for k, v in dfs.items()}   # 含日线
        cache["l3_t"] = now
        cache["dfs"] = dfs

    l3 = cache["l3"]
    sc = scoring.build(cache["macro"], cache["liq"], cache["event"], l3, cfg)

    _parts = []
    for _sd in st.open_sides():
        _pp = st.get_pos(_sd)
        _mv = (price - _pp.entry) / _pp.entry * (1 if _sd == "long" else -1) * 100
        _parts.append(f"{'多' if _sd=='long' else '空'} {_pp.size:.2f}张@{_pp.entry:,.0f}({_mv:+.2f}%)")
    pos_txt = " + ".join(_parts) if _parts else "空仓"
    pos = st.position

    LOG.info("── cycle %d %s 价%.0f 权益%.0f 持仓:%s", st.cycle_count,
             now_cst().strftime("%H:%M"), price, equity, pos_txt)
    LOG.info("   %s", sc.brief())

    # ---- 信号 ----
    bar_ms = 15 * 60 * 1000
    if "dfs" in cache and cache["dfs"].get("15m") is not None and len(cache["dfs"]["15m"]):
        last_bar = int(cache["dfs"]["15m"]["ts"].iloc[-1])   # dfs 已剔除未收盘K线
    else:
        last_bar = int(time.time() // 900 * 900 * 1000)

    new_bar = last_bar != cache.get("last_bar")
    if new_bar:
        cache["last_bar"] = last_bar
        sig = strategy.detect(sc, l3, cache["liq"], cfg, dbg=LOG.info)
        if sig:
            LOG.info("   信号 %s", sig.describe())
            journal.log_signal(now_ms(), sig.model, sig.side, sig.entry, sig.stop,
                               sig.confidence, False, "detected")

            # 双向持仓：本方向已持仓则不重复开；反方向持仓则视强度决定是否平掉
            _opp_side = "short" if sig.side == "long" else "long"
            if st.get_pos(_opp_side).is_open:
                _op = st.get_pos(_opp_side)
                if sig.confidence >= cfg["min_confidence"] + 0.17 and sig.size_mult >= 0.25:
                    LOG.warning("   反向信号 conf=%.2f 足够强，平掉 %s 持仓给新方向让路",
                                sig.confidence, _opp_side)
                    executor.close_position(client, st, inst, lot, LOG,
                                            "reverse_signal", price, cfg["ct_val"], pos=_op)
                    journal.save_state(st)
                else:
                    LOG.info("   相反方向仍持仓（%s），本信号不推翻它，可并行开仓", _opp_side)

            if st.get_pos(sig.side).is_open:
                LOG.info("   本方向已有持仓（%s），不重复开仓", sig.side)
            if not st.get_pos(sig.side).is_open:
                if st.loss_streak >= cfg["loss_streak_stop"]:
                    LOG.warning("   连亏%d次，今日停止交易", st.loss_streak)
                elif st.day_halted:
                    LOG.warning("   当日回撤熔断中，不开新仓")
                elif sig.confidence < cfg["min_confidence"]:
                    LOG.info("   置信度 %.2f < %.2f，放弃", sig.confidence, cfg["min_confidence"])
                elif st.halted_until > now_ms():
                    LOG.info("   处于停牌期，跳过")
                else:
                    plan = risk.finalize(
                        sig, equity, cfg, cache["liq"], cfg["ct_val"], lot, price,
                        dfs_source=cache.get("dfs"),
                        open_positions=st.n_open(),
                        used_margin_pct=used_margin_pct(client, equity),
                        loss_streak=st.loss_streak,
                    )
                    LOG.info("   %s", plan.describe())
                    if plan.ok:
                        if mode == "shadow" or dry_run:
                            # 影子模式：只记账，不下单
                            cache["shadow"].add(plan.signal if plan else sig, plan, sc, LOG)
                            journal.log_signal(now_ms(), sig.model, sig.side,
                                               sig.entry, sig.stop, sig.confidence,
                                               False, "shadow")
                        else:
                            newp = executor.open_position(client, sig, plan, cfg, inst, lot, LOG)
                            if newp:
                                st.set_pos(newp.side, newp)
                                journal.log_signal(now_ms(), sig.model, sig.side,
                                                   sig.entry, sig.stop, sig.confidence,
                                                   True, "executed")
        else:
            LOG.info("   无信号（价格未触及关键位或形态不满足）")
    else:
        LOG.info("   等待下一根 15m 收盘（当前 bar %s）", fmt_ts(last_bar))

    # ---- 停牌检查 ----
    # 停牌到期 -> 连亏清零，否则会永久停摆
    if st.halted_until and now_ms() >= st.halted_until:
        LOG.info("停牌期结束，连亏计数清零（原 %d 次）", st.loss_streak)
        st.halted_until = 0
        st.loss_streak = 0

    if st.loss_streak >= cfg["loss_streak_stop"] and st.halted_until == 0:
        st.halted_until = now_ms() + 24 * 3600 * 1000
        LOG.warning("连续亏损 %d 次，停牌 24 小时", st.loss_streak)

    journal.save_state(st)
    LOG.info("   %s", cache["shadow"].summary_line())
    append_jsonl(journal.CYCLE_LOG, {
        "ts": now_ms(), "price": price, "equity": equity,
        "l1": sc.l1_total, "l1_allow": sc.l1_allow, "l2": sc.l2_score,
        "l2_r": round(sc.l2_r, 2), "l3": sc.l3_bias, "l4": sc.l4_adjust,
        "dir": sc.direction, "veto": sc.veto, "pos": pos_txt,
    })


# ---------------- 入口 ----------------
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="跑一个周期后退出")
    ap.add_argument("--dry-run", action="store_true", help="只出信号，不下单")
    ap.add_argument("--cycles", type=int, default=0, help="跑 N 个周期后退出")
    args = ap.parse_args()

    print(BANNER)
    LOG.info("启动 %s  simulated=%s  dry_run=%s",
             CFG.get("inst_id"), CFG.get("simulated"), args.dry_run)

    client, cfg = boot()
    st = journal.load_state()
    if not getattr(st, "started_at", 0):
        st.started_at = now_ms()
    if not getattr(st, "start_equity", 0):
        try:
            st.start_equity = float(client.balance().get("totalEq") or 0)
        except Exception:
            st.start_equity = 0.0
    journal.save_state(st)
    if cfg.get("execution_mode", "shadow") == "shadow":
        LOG.warning("运行在【影子模式】：只记账不下单，不接管账户持仓，零资金风险")
        cfg["position_policy"] = "observe"
    startup_positions(client, st, cfg)

    cache: dict = {}
    n = 0
    while True:
        n += 1
        try:
            cycle(client, cfg, st, cache, dry_run=args.dry_run)
        except Exception as e:  # noqa: BLE001
            LOG.error("周期异常: %s\n%s", e, traceback.format_exc(limit=4))

        if args.once or (args.cycles and n >= args.cycles):
            LOG.info("结束（已跑 %d 个周期）", n)
            break
        time.sleep(cfg["loop_interval_sec"])


if __name__ == "__main__":
    main()
