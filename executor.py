"""执行层：下单、交易所侧止损、分批止盈、时间止损、状态对账。"""
from __future__ import annotations

import json
import math
import time
from dataclasses import asdict

from . import journal
from .journal import BotState, Position
from .okx import OkxClient, OkxError
from .risk import Plan
from .strategy import Signal
from .utils import now_ms, safe_div


def _f(x: float, lot_sz: float) -> str:
    dec = max(0, len(str(lot_sz).split(".")[-1])) if "." in str(lot_sz) else 0
    return f"{x:.{dec}f}"


def _round_lot(x: float, lot_sz: float) -> float:
    return math.floor(x / lot_sz) * lot_sz


def _maker_fill(client: OkxClient, inst_id: str, side: str, sz: str,
                pos_side: str | None, tick: float, cfg: dict, logger,
                reduce_only: bool = False, tag: str = "",
                px_override: float | None = None) -> tuple[bool, float, str]:
    """post_only 限价单（maker 费率）。返回 (是否成交, 成交均价, ordId)。

    定价规则（保证挂单不立即吃单）：
      sell -> best_bid + 1 tick
      buy  -> best_ask - 1 tick
    未成交则每 requote_s 秒贴近 1 tick 重新挂，最多 requote 次。
    """
    if not cfg.get("maker_enabled", True):
        return False, 0.0, ""

    wait_s = float(cfg.get("maker_wait_s", 24))
    requote = int(cfg.get("maker_requote", 2))
    interval = max(3.0, wait_s / (requote + 1))
    pool = cfg.get("pool") if False else None
    _tk = client.ticker(inst_id)
    bid = float(_tk.get("bidPx") or 0)
    ask = float(_tk.get("askPx") or 0)
    if bid <= 0 or ask <= 0:
        return False, 0.0, ""

    # 2026-10-27：挂单不再贴 1 tick（成交率太低，今晚开仓就超时回退市价）。
    # 改为离现价 ~110 点挂，每次 requote 再放宽 60 点。
    # 用户思路：关键位 ±100 点判有效 -> 就在 ±110 挂。
    _off0 = float(cfg.get("maker_offset_usd", 0) or 0)
    _off_step = float(cfg.get("maker_offset_step", 0) or 0)

    def _px(step: int) -> str:
        if px_override is not None:
            base = px_override
        else:
            base = bid if side == "sell" else ask
        off = _off0 + _off_step * step
        if off <= 0:
            off = tick * 2
        adj = base + off if side == "sell" else base - off
        dec = max(0, len(str(tick).split(".")[-1]) if "." in str(tick) else 0)
        return f"{round(adj / tick) * tick:.{dec}f}"

    for step in range(requote + 1):
        cl = f"MK{int(time.time()) % 1000000}{step}"
        try:
            r = client.place_order(inst_id, side, sz, ord_type="post_only",
                                   px=_px(step), reduce_only=reduce_only,
                                   pos_side=pos_side, cl_ord_id=cl)
        except OkxError as e:
            logger.warning("maker 挂单被拒(%s，多为会立即成交) ", e)
            # post_only 被拒通常意味着价格会穿越，贴近 1 tick 再试
            time.sleep(0.6)
            continue
        oid = r.get("ordId", "")
        deadline = time.time() + interval
        while time.time() < deadline:
            time.sleep(0.8)
            try:
                o = client.get_order(inst_id, ord_id=oid)
            except Exception:
                continue
            if o.get("state") == "filled":
                avg = float(o.get("avgPx") or 0)
                logger.info("maker 成交 %s %s张 @%.1f (ordId=%s)",
                            side, o.get("accFillSz", sz), avg, oid)
                return True, avg, oid
            if o.get("state") in ("canceled", "mmp_canceled"):
                break
        client.cancel_order(inst_id, oid)
    return False, 0.0, ""


def _place_entry(client: OkxClient, inst_id: str, side: str, sz: str,
                 pos_side: str, tick: float, ref_px: float, cfg: dict,
                 logger, tag: str) -> tuple[str, float]:
    """优先 maker，失败按配置回退市价。返回 (ordId, 成交价或 0)。"""
    ok, avg, oid = _maker_fill(client, inst_id, side, sz, pos_side,
                               tick, cfg, logger, tag=tag)
    if ok:
        return oid, avg
    if cfg.get("maker_fallback_market", True):
        logger.info("maker 未成交，回退市价（taker 0.05%%）")
        r = client.place_order(inst_id, side, sz, ord_type="market",
                               pos_side=pos_side, cl_ord_id=tag)
        return r.get("ordId", ""), 0.0
    logger.error("maker 未成交且未开启回退，放弃本次开仓")
    return "", -1.0


def open_position(client: OkxClient, sig: Signal, plan: Plan, cfg: dict,
                  inst_id: str, lot_sz: float, logger) -> Position | None:
    """市价开仓 + 挂交易所侧止损。

    注意：risk.finalize 会返回一份 sig 的副本（plan.signal），
    止损价与止盈价都以副本为准，不要用调用方传入的 sig。
    """
    psig = plan.signal or sig
    side = "sell" if psig.side == "short" else "buy"
    pside = psig.side                      # 双向持仓必须带 posSide
    sz = _f(plan.size_contracts, lot_sz)
    tag = f"BSYS{int(time.time())%1000000}"
    try:
        tick = float(client.instrument(inst_id).get("tickSz") or 0.01)
    except Exception:
        tick = 0.01
    try:
        ord_id, filled_px = _place_entry(client, inst_id, side, sz, pside,
                                        tick, psig.entry, cfg, logger, tag)
        if filled_px == -1.0:
            return None
        logger.info("开仓下单: %s %s张 ordId=%s（%s）", side, sz, ord_id,
                    "maker" if filled_px > 0 else "taker")
    except OkxError as e:
        logger.error("开仓失败: %s", e)
        return None

    # 取实际成交均价
    time.sleep(1.2)
    px = psig.entry
    try:
        p = client.position(inst_id, pos_side=pside)
        if p and float(p.get("avgPx") or 0) > 0:
            px = float(p["avgPx"])
            real_sz = abs(float(p["pos"]))
        else:
            real_sz = plan.size_contracts
    except Exception:
        real_sz = plan.size_contracts

    # 交易所侧止损（机器人挂了也不裸奔）
    algo_id = ""
    try:
        a = client.place_algo_stop(inst_id, "buy" if psig.side == "short" else "sell",
                                   _f(real_sz, lot_sz), psig.stop,
                                   pos_side=pside,
                                   cl_ord_id=f"SL{int(time.time())%1000000}")
        algo_id = a.get("algoId", "")
        logger.info("已挂交易所止损 %s @%.1f algoId=%s", psig.side, psig.stop, algo_id)
    except OkxError as e:
        logger.error("止损挂单失败(软件仍会兜底): %s", e)

    return Position(
        side=psig.side, entry=px, size=real_sz, stop=psig.stop,
        risk0=abs(psig.stop - px),
        targets=list(psig.targets), tp_filled=[], opened_at=now_ms(),
        model=psig.model, algo_id=algo_id, cl_ord_id=tag,
        counter_trend=psig.counter_trend,
    )


def reduce_position(client: OkxClient, pos: Position, qty: float,
                    inst_id: str, lot_sz: float, logger, reason: str) -> bool:
    qty = _round_lot(qty, lot_sz)
    if qty <= 0:
        return False
    side = "buy" if pos.side == "short" else "sell"
    try:
        _cfg = cfg if isinstance(cfg, dict) else {}
        _tick = float(client.instrument(inst_id).get("tickSz") or 0.01)
        _ok, _avg, _oid = _maker_fill(client, inst_id, side, _f(qty, lot_sz),
                                      pos.side, _tick, _cfg, logger,
                                      reduce_only=True)
        if not _ok:
            client.place_order(inst_id, side, _f(qty, lot_sz), pos_side=pos.side,
                               reduce_only=True, ord_type="market")
            logger.info("减仓(taker) %s %.2f张 原因=%s", side, qty, reason)
        else:
            logger.info("减仓(maker) %s %.2f张 原因=%s @%.1f", side, qty, reason, _avg)
        pos.size = round(pos.size - qty, 6)
        return True
    except OkxError as e:
        logger.error("减仓失败: %s", e)
        return False


def close_position(client: OkxClient, st: BotState, inst_id: str, lot_sz: float,
                   logger, reason: str, exit_px: float | None = None,
                   ct_val: float = 0.01, pos: Position | None = None,
                   cfg: dict | None = None) -> None:
    pos = pos if pos is not None else st.position
    if not pos.is_open:
        return
    # 先撤交易所止损，避免重复平仓
    if pos.algo_id:
        try:
            client.cancel_algo(pos.algo_id, inst_id)
        except OkxError as e:
            logger.warning("撤销止损失败: %s", e)
    side = "buy" if pos.side == "short" else "sell"
    _fast = reason in ("stop", "exchange_stop", "liquidate", "risk",
                      "soft_stop", "policy_flatten", "trail_stop")
    _filled_px = 0.0
    _done = False
    if not _fast:
        try:
            _tick = float(client.instrument(inst_id).get("tickSz") or 0.01)
            _ok, _avg, _oid = _maker_fill(client, inst_id, side,
                                          _f(pos.size, lot_sz), pos.side, _tick,
                                          cfg if isinstance(cfg, dict) else {},
                                          logger, reduce_only=True)
            if _ok:
                _filled_px = _avg
                _done = True
                logger.info("平仓(maker) 成交 @%.1f 原因=%s", _avg, reason)
        except Exception as e:
            logger.warning("maker 平仓异常，改用市价: %s", e)
    if not _done:
        try:
            client.place_order(inst_id, side, _f(pos.size, lot_sz),
                               reduce_only=True, ord_type="market", pos_side=pos.side)
            if not _fast:
                logger.info("平仓回退市价(taker) 原因=%s", reason)
        except OkxError as e:
            logger.error("平仓失败: %s", e)
            return

    px = (exit_px or _filled_px or pos.entry)
    pnl_pct = safe_div((px - pos.entry) if pos.side == "long" else (pos.entry - px), pos.entry)
    r_mult = safe_div(pnl_pct, safe_div(abs(pos.stop - pos.entry), pos.entry))
    # 名义价值 = 张数 × 每张标的数量 × 价格（修复：原先硬编码 ×10，导致高估 1000 倍）
    notional = pos.size * ct_val * pos.entry
    journal.log_trade({
        "open_ts": pos.opened_at, "close_ts": now_ms(), "inst_id": inst_id,
        "side": pos.side, "model": pos.model, "entry": pos.entry, "exit": px,
        "size": pos.size, "stop": pos.stop, "r_multiple": r_mult,
        "pnl_pct": pnl_pct, "pnl_usd": pnl_pct * notional,
        "reason": reason, "notes": "", "counter_trend": pos.counter_trend,
    })
    if pnl_pct > 0:
        st.wins += 1
        st.win_streak += 1
        st.loss_streak = 0
    else:
        st.loss_streak += 1
        st.win_streak = 0
    st.trades_total += 1
    st.set_pos(pos.side, Position())
    logger.info("平仓完成 %s 盈亏%.2f%% R=%.2f 原因=%s", pos.side, pnl_pct * 100, r_mult, reason)


def reconcile(client: OkxClient, st: BotState, inst_id: str, price: float,
              logger, ct_val: float = 0.01) -> None:
    """把本地状态与交易所实际持仓对齐。

    修复：止损成交后按**止损价**而不是当前价结算，
    否则插针后价格回抽会把实际的大亏记成小赚。
    """
    for side_ in ("long", "short"):
        _reconcile_one(client, st, inst_id, price, logger, ct_val, side_)


def _reconcile_one(client, st, inst_id, price, logger, ct_val, side_):
    pos = st.get_pos(side_)
    if not pos.is_open:
        return
    try:
        p = client.position(inst_id, pos_side=side_)
    except Exception:
        return
    real = abs(float(p["pos"])) if p else 0.0

    if pos.is_open and real <= 0:
        # 交易所已无持仓（多半是 algo 止损成交）
        fill_px = pos.stop if pos.stop else price
        logger.warning("检测到持仓已被交易所平掉，按止损价 %.1f 结算", fill_px)
        pnl_pct = safe_div((pos.entry - fill_px) if pos.side == "short" else (fill_px - pos.entry),
                           pos.entry)
        notional = pos.size * ct_val * pos.entry
        journal.log_trade({
            "open_ts": pos.opened_at, "close_ts": now_ms(), "inst_id": inst_id,
            "side": pos.side, "model": pos.model, "entry": pos.entry, "exit": fill_px,
            "size": pos.size, "stop": pos.stop,
            "r_multiple": safe_div(pnl_pct, safe_div(abs(pos.stop - pos.entry), pos.entry)),
            "pnl_pct": pnl_pct, "pnl_usd": pnl_pct * notional,
            "reason": "exchange_stop", "notes": "由交易所侧条件单平仓",
            "counter_trend": pos.counter_trend,
        })
        st.loss_streak += 1
        st.win_streak = 0
        st.trades_total += 1
        st.set_pos(side_, Position())
    elif pos.is_open and real > 0:
        # 部分成交/手动加减仓：同步张数，并按新张数重挂止损
        if abs(real - pos.size) / max(pos.size, 1e-9) > 0.02:
            logger.info("同步持仓张数 %.2f -> %.2f，重挂止损", pos.size, real)
            pos.size = real
            if pos.algo_id:
                try:
                    client.cancel_algo(pos.algo_id, inst_id)
                except Exception:
                    pass
            try:
                lot_sz = 0.01
                a = client.place_algo_stop(
                    inst_id, "buy" if pos.side == "short" else "sell",
                    _f(pos.size, lot_sz), pos.stop, pos_side=pos.side,
                    cl_ord_id=f"RS{int(time.time()) % 1000000}")
                pos.algo_id = a.get("algoId", "")
                logger.info("止损已按 %.2f 张重挂 algoId=%s", pos.size, pos.algo_id)
            except Exception as e:
                logger.warning("重挂止损失败: %s", e)


def manage(client: OkxClient, st: BotState, price: float, cfg: dict,
           inst_id: str, lot_sz: float, logger, ct_val: float = 0.01,
           pos: Position | None = None) -> str | None:
    """分批止盈 + 移动止损 + 时间止损 + 软件兜底止损。返回触发的原因。"""
    pos = pos if pos is not None else st.position
    if not pos.is_open:
        return None

    pnl = safe_div((price - pos.entry) if pos.side == "long" else (pos.entry - price), pos.entry)
    pos.peak_pnl_pct = max(pos.peak_pnl_pct, pnl)

    # --- 软件兜底止损：交易所条件单可能挂失败/被手工撤掉，这里必须自己判断 ---
    if pos.stop:
        hit = (pos.side == "long" and price <= pos.stop) or \
              (pos.side == "short" and price >= pos.stop)
        if hit:
            logger.warning("软件止损触发：价格 %.1f 触及止损 %.1f", price, pos.stop)
            close_position(client, st, inst_id, lot_sz, logger, "soft_stop", price, ct_val, pos=pos)
            return "soft_stop"

    # --- 分批止盈：唯一事实来源是风控层写进 Position.targets 的价格 ---
    if pos.targets:
        tps = [abs(t - pos.entry) / pos.entry for t in pos.targets]
    else:
        tps = list(cfg["tp_pcts"])
    weights = cfg["tp_weights"]

    # --- 连续移动止损（真正的"移动止损"）---
    # 盈利超过 1R 后，每多赚 0.5R 就把止损推进到"峰值 − 0.5R"，
    # 只朝有利方向移动，永不回退。
    risk_abs = pos.risk0 or (abs(pos.stop - pos.entry) if pos.stop else 0.0)
    if not pos.risk0 and risk_abs > 0:
        pos.risk0 = risk_abs
    if risk_abs > 0 and pos.entry > 0:
        pnl_r = pnl * pos.entry / risk_abs          # 当前盈亏折成 R

        # 2026-10-27 修复：原门槛 1R = 止损距离（本系统 1.56%），
        # 导致价格朝有利方向走 1.0%（浮盈已达保证金 +10%）仍不启动保护。
        # 现在满足任一即启动：
        #   ① 盈利 >= trail_start_r（默认 0.5R，约 0.78% 价格）
        #   ② 保证金收益率 >= trail_start_margin（默认 8%，约 0.8% 价格）
        _lev = float(cfg.get("leverage", 10) or 10)
        _ctv = float(cfg.get("ct_val", 0.01) or 0.01)
        _margin = pos.size * _ctv * pos.entry / _lev if _lev else 0.0
        _mret = (pnl / _margin) if _margin > 0 else 0.0
        _is_counter = bool(getattr(pos, "counter_trend", False))
        _start_r = float(cfg.get("counter_trend_trail_r" if _is_counter
                                 else "trail_start_r",
                                 0.3 if _is_counter else 0.5))
        _start_m = float(cfg.get("trail_start_margin", 0.08))
        _active = (pnl_r >= _start_r) or (_mret >= _start_m)

        if _active:
            # 锁定 R：峰值 − trail_lock_r，但绝不让止损越过保本
            lock_r = max(0.0, pnl_r - float(cfg.get("trail_lock_r", 0.5)))
            sign = -1 if pos.side == "short" else 1
            cand = pos.entry * (1 + sign * (risk_abs / pos.entry) * lock_r)
            better = (cand < pos.stop) if pos.side == "short" else (cand > pos.stop)
            if better and abs(cand - pos.stop) / price > 0.0005:
                old = pos.stop
                pos.stop = round(cand, 1)
                if pos.algo_id:
                    try:
                        client.cancel_algo(pos.algo_id, inst_id)
                    except Exception:
                        pass
                try:
                    a = client.place_algo_stop(
                        inst_id, "buy" if pos.side == "short" else "sell",
                        _f(pos.size, lot_sz), pos.stop, pos_side=pos.side,
                        cl_ord_id=f"TR{int(time.time()) % 1000000}")
                    pos.algo_id = a.get("algoId", "")
                    logger.info("移动止损 %.1f -> %.1f（盈利%.2fR / 保证金%+.1f%%，锁定%.2fR）",
                                old, pos.stop, pnl_r, _mret * 100, lock_r)
                except Exception as e:
                    logger.warning("移动止损挂单失败: %s", e)

    for i, tp in enumerate(tps):
        if i in pos.tp_filled:
            continue
        if pnl >= tp:
            last = (i == len(tps) - 1)
            if last:
                qty = pos.size                      # 最后一档清空，不留残余
            else:
                qty = pos.size * (weights[i] / max(1e-9, sum(weights[i:])))
                qty = min(qty, pos.size * 0.95)
            if reduce_position(client, pos, qty, inst_id, lot_sz, logger, f"TP{i+1}"):
                pos.tp_filled.append(i)
                # 浮动止损：TP1 后移保本，TP2 后移到 TP1，TP3 后移到 TP2
                if i >= 0 and (i == 0 or i < len(pos.targets)):
                    if pos.algo_id:
                        try:
                            client.cancel_algo(pos.algo_id, inst_id)
                        except Exception:
                            pass
                    if i == 0:
                        pos.stop = pos.entry
                    else:
                        pos.stop = pos.targets[i - 1]
                    try:
                        a = client.place_algo_stop(
                            inst_id, "buy" if pos.side == "short" else "sell",
                            _f(pos.size, lot_sz), pos.stop, pos_side=pos.side,
                            cl_ord_id=f"BE{int(time.time())%1000000}")
                        pos.algo_id = a.get("algoId", "")
                        logger.info("止损上移至 %.1f（第%d档后浮动）", pos.stop, i + 1)
                    except Exception as e:
                        logger.warning("保本止损挂单失败: %s", e)
                if last or pos.size <= 0.01:
                    st.set_pos(pos.side, journal.Position())
                    return f"TP{i+1}_full"
                return f"TP{i+1}"

    # --- 时间止损 ---
    if pos.opened_at:
        hours = (now_ms() - pos.opened_at) / 3.6e6
        half_h = cfg["time_stop_hours_half"]
        full_h = cfg["time_stop_hours_full"]
        if hours >= full_h and pnl < tps[0]:
            return "time_stop_full"
        if hours >= half_h and pnl < tps[0] * 0.5 and 0 not in pos.tp_filled:
            return "time_stop_half"
    return None
