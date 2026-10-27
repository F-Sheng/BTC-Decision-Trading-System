"""判断「本轮是否需要取数」—— 事件驱动自适应采样

设计（用户 2026-10-27 批准）：
  主任务每 10 分钟 tick，但只在以下任一条件命中时才真正跑 local_fetch：

  1. 金十最近 1 小时有权重 >= 9 的新闻        -> 有事发生
  2. 价格距上次快照变动 > 1.2%               -> 波动异常
  3. 持仓距止损或止盈 < 0.6%                 -> 快打到了
  4. 距上次成功取数 > 55 分钟                -> 保底，防漏
  5. 存在 ALERT / 数据源故障                 -> 系统异常
  6. AgentView 已过期（或 30 分钟内将过期）  -> 该重新表态
  7. 重要日历事件前后 2 小时                  -> 已知风险时点

  读数方式：
  · 命中 1/2/3/7（真·大事） -> 由 Agent 亲自 read_image 读裁剪小图
  · 只命中 4/5/6（到期/异常）-> 用本地 VLM（现有 local_fetch）

输出：JSON 到 stdout，同时写 data/fetch_decision.json
退出码：0 = 需要取数，10 = 可跳过
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(r"D:\DP workspace\btc_system")
sys.path.insert(0, str(ROOT))

CST = timezone(timedelta(hours=8))

NEWS_HIGH_SCORE = 9          # 金十权重阈值
NEWS_WINDOW_MIN = 60         # 新闻回看窗口（分钟）
PRICE_MOVE_PCT = 1.2         # 价格变动阈值（%）
NEAR_LEVEL_PCT = 0.6         # 距止损/止盈阈值（%）
MIN_INTERVAL_MIN = 55        # 保底取数间隔（分钟）
AGENTVIEW_WARN_MIN = 30      # AgentView 剩余不足该值时也强制刷新

# 重要日历事件（本地时间，格式 YYYY-MM-DDTHH:MM）
CALENDAR = [
    # FOMC 决议 + 新闻发布会
    ("2026-10-28T02:00", "FOMC 利率决议"),
    ("2026-10-28T02:30", "FOMC 新闻发布会"),
    # 用户可继续补充：非农、CPI 等
]
CALENDAR_WINDOW_H = 2


def _now():
    return datetime.now(CST)


def _scan_news_file(path, label, window_min=NEWS_WINDOW_MIN, thresh=NEWS_HIGH_SCORE):
    """扫描某个 news 日志文件，返回最近 window_min 分钟内权重>=thresh 的最新一条"""
    if not path.exists():
        return None, label + ' 文件不存在'
    import json as _json
    from datetime import datetime as _dt, timedelta as _td
    cutoff = (_now() - _td(minutes=window_min)).timestamp()
    best = None
    try:
        lines = path.read_text(encoding='utf-8').splitlines()[-400:]
    except Exception as e:
        return None, label + ' 读取失败: %s' % e
    for line in lines:
        if not line.strip():
            continue
        try:
            o = _json.loads(line)
        except Exception:
            continue
        ts = o.get('ts') or o.get('time') or o.get('t') or 0
        if isinstance(ts, str):
            _ok = False
            for _fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M',
                         '%Y-%m-%dT%H:%M:%S', '%Y-%m-%dT%H:%M'):
                try:
                    ts = _dt.strptime(ts[:19], _fmt).replace(tzinfo=CST).timestamp()
                    _ok = True
                    break
                except Exception:
                    continue
            if not _ok:
                continue
        elif ts > 1e11:
            ts = ts / 1000
        if ts < cutoff:
            continue
        sc = int(o.get('score') or o.get('weight') or 0)
        if sc >= thresh:
            if best is None or sc > best[0] or (sc == best[0] and ts > best[2]):
                best = (sc, (o.get('txt') or o.get('text') or o.get('title') or '')[:60], ts)
    if best:
        t = _dt.fromtimestamp(best[2], CST).strftime('%H:%M')
        return True, '%s %s 权重%d: %s' % (label, t, best[0], best[1])
    return False, '%s 最近%d分钟无权重>=%d' % (label, window_min, thresh)


def r_news():
    """金十最近 NEWS_WINDOW_MIN 分钟是否有高权重新闻"""
    p = ROOT / "data" / "news_log.jsonl"
    if not p.exists():
        return None, "news_log 不存在"
    cutoff = (_now() - timedelta(minutes=NEWS_WINDOW_MIN)).timestamp()
    best = None
    try:
        for line in p.read_text(encoding="utf-8").splitlines()[-400:]:
            if not line.strip():
                continue
            try:
                o = json.loads(line)
            except Exception:
                continue
            ts = o.get("ts") or o.get("time") or o.get("t") or 0
            # 可能是毫秒、秒、或字符串
            if isinstance(ts, str):
                # news_log 里存的是 "YYYY-MM-DD HH:MM"（16 字符），
                # 原来只按 "%Y-%m-%d %H:%M:%S" 解析会全部失败 -> 规则1永远不触发
                _ok = False
                for _fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M",
                             "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M"):
                    try:
                        ts = datetime.strptime(ts[:19], _fmt).replace(
                            tzinfo=CST).timestamp()
                        _ok = True
                        break
                    except Exception:
                        continue
                if not _ok:
                    continue
            elif ts > 1e11:
                ts = ts / 1000
            if ts < cutoff:
                continue
            sc = int(o.get("score") or o.get("weight") or 0)
            if sc >= NEWS_HIGH_SCORE:
                # 同分时取最新那条（原来只比分数，导致一直报最早的一条）
                if best is None or sc > best[0] or (sc == best[0] and ts > best[2]):
                    best = (sc, (o.get("txt") or o.get("text") or o.get("title") or "")[:60], ts)
    except Exception as e:
        return None, "news_log 读取失败: %s" % e
    if best:
        t = datetime.fromtimestamp(best[2], CST).strftime("%H:%M")
        return True, "金十 %s 权重%d: %s" % (t, best[0], best[1])
    return False, "最近 %d 分钟无权重>=%d 的新闻" % (NEWS_WINDOW_MIN, NEWS_HIGH_SCORE)


def r_odaily():
    """Odaily 星球日报快讯（专注币圈，补充金十的宏观视角）"""
    return _scan_news_file(ROOT / "data" / "odaily_log.jsonl", "Odaily")


def r_last_snapshot():
    """读最后一次快照：时间、价格、持仓"""
    p = ROOT / "data" / "market_snapshot.jsonl"
    if not p.exists():
        return None
    lines = p.read_text(encoding="utf-8").strip().splitlines()
    for ln in reversed(lines[-50:]):
        try:
            o = json.loads(ln)
            return o
        except Exception:
            continue
    return None


def r_price():
    try:
        from core.okx import OkxClient
        c = OkxClient(simulated=True)
        t = c.ticker("BTC-USDT-SWAP")
        return float(t.get("last") or 0)
    except Exception:
        return 0.0


def r_position_risk(price: float):
    """持仓距「真正有风险的关键位」的最近距离百分比

    处理方式 B（用户 2026-10-27 选定）：
      保本止损 / 锁盈止损不算大事 —— 被它打掉最差也只是不赚，不会亏。
        · 多单：止损 >= 开仓价  -> 保本或已锁盈，跳过
        · 空单：止损 <= 开仓价  -> 保本或已锁盈，跳过
      止盈档位仍然算，因为接近止盈意味着马上要成交。
    """
    try:
        from core import journal
        st = journal.load_state()
        worst = None
        skipped = []
        for sd in st.open_sides():
            p = st.get_pos(sd)
            # 判断该止损是否已在保本或更好位置
            stop_safe = False
            if p.stop and p.entry:
                if sd == "long" and p.stop >= p.entry * 0.9995:
                    stop_safe = True
                elif sd == "short" and p.stop <= p.entry * 1.0005:
                    stop_safe = True
            for lv in ([p.stop] + list(p.targets or [])):
                if not lv:
                    continue
                is_stop = (lv == p.stop)
                if is_stop and stop_safe:
                    skipped.append("%s 止损 %.1f 已保本/锁盈" % (sd, lv))
                    continue
                d = abs(price - lv) / price * 100
                if worst is None or d < worst[0]:
                    worst = (d, sd, lv)
        if skipped and worst:
            worst = worst + ("跳过: " + "; ".join(skipped),)
        return worst
    except Exception:
        return None


def r_agentview():
    try:
        from core import agent_view
        v = agent_view.load()
        if not v.ok:
            return False, "AgentView 不存在"
        left = 90 - v.age_min
        if left < AGENTVIEW_WARN_MIN:
            return True, "AgentView 仅剩 %.0f 分钟" % left
        return False, "AgentView 剩 %.0f 分钟" % left
    except Exception as e:
        return None, "AgentView 读取失败: %s" % e


def r_calendar():
    now = _now()
    for s, name in CALENDAR:
        try:
            t = datetime.strptime(s, "%Y-%m-%dT%H:%M").replace(tzinfo=CST)
        except Exception:
            continue
        if abs((now - t).total_seconds()) <= CALENDAR_WINDOW_H * 3600:
            return True, "%s 距 %+.1f 小时" % (name, (now - t).total_seconds() / 3600)
    return False, "不在重要日历窗口内"



LIQ_WATCH = ROOT / "data" / "liq_watch.json"
LIQ_CHANGE_PCT = 30.0     # 上方/下方清算总量相对上次变动超过该值 -> 触发
OI_CHANGE_PCT = 5.0       # OI 变动超过该值 -> 触发


def r_liquidation_shift():
    """从机器人日志读 L2[PROXY]，检测清算总量 / OI 突变。

    为什么这样做（用户 2026-10-27 提问）：
      平静期不截 CoinGlass 图，那币圈发生清算大事怎么发现？
      答案：机器人每 60 秒算一次自己的 L2[PROXY]（含上3%/下3%清算总量、OI、
      funding、多空比），已经写在日志里。读日志零成本，不需要浏览器。
    """
    import re

    logs = sorted((ROOT / "logs").glob("btc-*.log"))
    if not logs:
        return False, "无日志"
    latest = logs[-1]
    try:
        tail = latest.read_text(encoding="utf-8", errors="ignore").splitlines()[-400:]
    except Exception as e:
        return None, "日志读取失败: %s" % e

    pat = re.compile(
        r"L2\[PROXY\]\s+R=([\d.]+)\s+上3%=([\d.]+)M\s+下3%=([\d.]+)M.*?OI=([\d,]+)BTC")
    cur = None
    for line in reversed(tail):
        m = pat.search(line)
        if m:
            cur = {"r": float(m.group(1)),
                   "up_m": float(m.group(2)),
                   "dn_m": float(m.group(3)),
                   "oi": float(m.group(4).replace(",", ""))}
            break
    if not cur:
        return None, "日志里没找到 L2[PROXY]"

    prev = None
    if LIQ_WATCH.exists():
        try:
            prev = json.loads(LIQ_WATCH.read_text(encoding="utf-8"))
        except Exception:
            prev = None
    try:
        LIQ_WATCH.write_text(json.dumps(cur, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass

    if not prev or not prev.get("up_m"):
        return False, "首次记录清算基准 R=%.2f 上%.0fM 下%.0fM" % (
            cur["r"], cur["up_m"], cur["dn_m"])

    du = abs(cur["up_m"] - prev["up_m"]) / prev["up_m"] * 100
    dd = abs(cur["dn_m"] - prev["dn_m"]) / prev["dn_m"] * 100
    do = abs(cur["oi"] - prev["oi"]) / prev["oi"] * 100 if prev.get("oi") else 0
    msg = ("清算变化 上%.0f%% 下%.0f%% OI%.1f%%（R %.2f→%.2f）"
           % (du, dd, do, prev["r"], cur["r"]))
    if du > LIQ_CHANGE_PCT or dd > LIQ_CHANGE_PCT:
        return True, "清算突变 " + msg
    if do > OI_CHANGE_PCT:
        return True, "OI 突变 " + msg
    return False, msg



def r_liquidation_burst():
    """规则 9：OKX 真实强平流（2026-10-27 新增）

    用户提问：「清算地图是自己算的，不读图能拿到最新数据吗？
              是不是可以直接读 OKX 的强平流，暴涨就切模式？」
    答：能。OKX /api/v5/public/liquidation-orders 无需额外权限，返回真实爆仓成交。
        原 L2[PROXY] 是基于 OI + 杠杆假设的估算，不如这个准。
    """
    try:
        import subprocess
        py = sys.executable
        out = subprocess.run(
            [py, str(ROOT / "tools_liq_flow.py"), "--json"],
            capture_output=True, text=True, timeout=25,
            encoding="utf-8", errors="ignore")
        d = json.loads(out.stdout or "{}")
    except Exception as e:
        return None, "强平流获取失败: %s" % str(e)[:60]

    if not d.get("ok"):
        return None, "强平流: " + str(d.get("why"))

    w60 = d.get("w60") or {}
    ratio = d.get("short_long_ratio_60m")
    msg = ("60分钟 空爆%.0f张/多爆%.0f张 比%s"
           % (w60.get("short_liq", 0), w60.get("long_liq", 0), ratio))
    if d.get("burst"):
        return True, "强平潮！" + "; ".join(d.get("why") or []) + " | " + msg
    return False, msg


def main():
    reasons = []       # 触发原因
    checks = {}        # 全部检查明细
    big_event = False  # 是否需要 Agent 亲读

    # 1. 新闻
    ok, msg = r_news()
    checks["news"] = msg
    if ok:
        reasons.append(msg)
        big_event = True

    # 1b. Odaily 币圈快讯
    ok2, msg2 = r_odaily()
    checks["odaily"] = msg2
    if ok2:
        reasons.append(msg2)
        big_event = True

    # 2. 价格变动
    snap = r_last_snapshot()
    price = r_price()
    checks["price_now"] = price
    if snap and snap.get("px") and price:
        move = abs(price - snap["px"]) / snap["px"] * 100
        checks["price_move_pct"] = round(move, 2)
        checks["last_ts"] = snap.get("ts")
        if move > PRICE_MOVE_PCT:
            reasons.append("价格变动 %.2f%%（>%.1f%%）" % (move, PRICE_MOVE_PCT))
            big_event = True

    # 3. 距止损/止盈
    if price:
        w = r_position_risk(price)
        if w:
            checks["nearest_level"] = {"dist_pct": round(w[0], 2),
                                       "side": w[1], "level": w[2]}
            if w[0] < NEAR_LEVEL_PCT:
                reasons.append("%s 距关键位 %s 仅 %.2f%%（<%.1f%%）"
                               % (w[1], format(w[2], ",.1f"), w[0], NEAR_LEVEL_PCT))
                big_event = True

    # 4. 距上次取数（保底）
    if snap and snap.get("ts"):
        try:
            t = datetime.strptime(snap["ts"], "%Y-%m-%dT%H:%M").replace(tzinfo=CST)
            mins = (_now() - t).total_seconds() / 60
            checks["since_last_fetch_min"] = round(mins, 1)
            if mins > MIN_INTERVAL_MIN:
                reasons.append("距上次取数 %.0f 分钟（>%d 保底）" % (mins, MIN_INTERVAL_MIN))
        except Exception:
            reasons.append("无法解析上次快照时间，强制取数")
    else:
        reasons.append("无历史快照，强制取数")

    # 5. ALERT
    alert = ROOT / "state" / "ALERT.txt"
    if alert.exists():
        try:
            txt = alert.read_text(encoding="utf-8").strip()[:120]
        except Exception:
            txt = "ALERT 存在"
        reasons.append("系统告警: " + txt)

    # 6. AgentView 快过期
    ok, msg = r_agentview()
    checks["agentview"] = msg
    if ok:
        reasons.append(msg)

    # 7. 日历
    ok, msg = r_calendar()
    checks["calendar"] = msg
    if ok:
        reasons.append(msg)
        big_event = True

    # 8. 清算 / OI 突变（从机器人日志读，零成本）
    ok, msg = r_liquidation_shift()
    checks["liquidation"] = msg
    if ok:
        reasons.append(msg)
        big_event = True

    # 9. OKX 真实强平流（暴涨 -> 大事）
    ok, msg = r_liquidation_burst()
    checks["liq_flow"] = msg
    if ok:
        reasons.append(msg)
        big_event = True

    need = bool(reasons)
    out = {
        "ts": _now().strftime("%Y-%m-%dT%H:%M"),
        "need_fetch": need,
        "read_mode": "agent" if (need and big_event) else ("vlm" if need else "skip"),
        "reasons": reasons,
        "checks": checks,
    }
    (ROOT / "data" / "fetch_decision.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if need else 10


if __name__ == "__main__":
    sys.exit(main())
