"""运行成绩单：检验策略 V2 的实际表现。

用法：
    python perf.py           # 打印
    python perf.py --save    # 同时写入 logs/perf-YYYYMMDD.md
"""
from __future__ import annotations

import os
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import journal
from core.utils import CST, LOG_DIR, ROOT, read_json


def main():
    cfg = read_json(ROOT / "config.json", {}) or {}
    st = journal.load_state()
    con = journal._conn()

    t0 = (cfg.get("run_started_at") or cfg.get("v1_started_at")
          or getattr(st, "started_at", 0) or 0)
    se = (cfg.get("run_start_equity") or cfg.get("v1_start_equity")
          or getattr(st, "start_equity", 0) or 0)
    now = datetime.now(CST)
    run_h = (now.timestamp() * 1000 - t0) / 3.6e6 if t0 else 0

    # 权益曲线
    rows = con.execute("SELECT ts, equity FROM equity WHERE ts>=? ORDER BY ts",
                       (t0,)).fetchall()
    if not rows:
        # V2 刚起步，还没有新的权益记录：用最新一条作为当前值
        rows = con.execute(
            "SELECT ts, equity FROM equity ORDER BY ts DESC LIMIT 1").fetchall()
    eq_now = rows[-1][1] if rows else 0
    eq_max = max([r[1] for r in rows] + [eq_now])
    eq_min = min([r[1] for r in rows] + [eq_now])
    dd = ((eq_now - eq_max) / eq_max * 100) if eq_max else 0
    pnl = eq_now - se if se else 0
    pnl_pct = (pnl / se * 100) if se else 0

    # 信号
    sig_n = con.execute("SELECT COUNT(*) FROM signals WHERE ts>=?", (t0,)).fetchone()[0]
    by_model = con.execute(
        "SELECT model, side, COUNT(*) FROM signals WHERE ts>=? "
        "GROUP BY model, side", (t0,)).fetchall()
    acc = con.execute("SELECT COUNT(*) FROM signals WHERE accepted=1 AND ts>=?", (t0,)).fetchone()[0]

    # 交易
    tr = con.execute("SELECT COUNT(*) FROM trades WHERE close_ts>=?", (t0,)).fetchone()[0]
    wins = con.execute("SELECT COUNT(*) FROM trades WHERE pnl_usd>0 AND close_ts>=?", (t0,)).fetchone()[0]
    avg_r = con.execute("SELECT AVG(r_multiple) FROM trades WHERE close_ts>=?", (t0,)).fetchone()[0] or 0
    sum_pnl = con.execute("SELECT SUM(pnl_usd) FROM trades WHERE close_ts>=?", (t0,)).fetchone()[0] or 0
    best = con.execute("SELECT MAX(r_multiple) FROM trades WHERE close_ts>=?", (t0,)).fetchone()[0]
    worst = con.execute("SELECT MIN(r_multiple) FROM trades WHERE close_ts>=?", (t0,)).fetchone()[0]
    recent = con.execute(
        "SELECT close_ts, side, model, entry, exit, pnl_usd, r_multiple, reason "
        "FROM trades WHERE close_ts>=? ORDER BY close_ts DESC LIMIT 8", (t0,)).fetchall()

    # 健康
    logf = LOG_DIR / f"btc-{now.strftime('%Y%m%d')}.log"
    lag = (time.time() - logf.stat().st_mtime) if logf.exists() else -1

    L = []
    A = L.append
    A("=" * 62)
    A("  BTC 自动交易策略 V2 · 运行成绩单")
    A("=" * 62)
    A(f"  统计时间     {now.strftime('%Y-%m-%d %H:%M:%S')} 北京时间")
    A(f"  运行时长     {run_h:.1f} 小时（约 {run_h/24:.1f} 天）")
    A(f"  运行周期     {st.cycle_count} 次")
    A(f"  日志延迟     {lag:.0f} 秒 {'✅正常' if 0 <= lag < 180 else '⚠️需检查'}")
    A("")
    A("  ── 资金 ─────────────────────────────")
    A(f"  起始权益     {se:,.2f} USDT")
    A(f"  当前权益     {eq_now:,.2f} USDT")
    A(f"  区间盈亏     {pnl:+,.2f} USDT（{pnl_pct:+.2f}%）")
    A(f"  峰值 / 谷值  {eq_max:,.2f} / {eq_min:,.2f}")
    A(f"  当前回撤     {dd:+.2f}%")
    A(f"  已平仓盈亏   {sum_pnl:+,.2f} USDT")
    A("")
    A("  ── 交易 ─────────────────────────────")
    if tr == 0:
        A("  尚无已平仓交易（当前持仓为接管仓，平仓后才会入账）")
    else:
        A(f"  平仓笔数     {tr}    胜率 {wins/tr*100:.1f}%")
        A(f"  平均 R       {avg_r:+.2f}    最好 {best:+.2f}R   最差 {worst:+.2f}R")
    A("")
    A("  ── 信号 ─────────────────────────────")
    A(f"  累计生成     {sig_n} 条    被采纳 {acc} 条")
    for m, s, c in by_model:
        A(f"    模型 {m} {s:<5} {c} 次")
    A("")
    if recent:
        A("  ── 最近平仓 ─────────────────────────")
        for ts, side, model, ent, ex, p, r, why in recent:
            A(f"    {datetime.fromtimestamp(ts/1000, CST).strftime('%m-%d %H:%M')} "
              f"{'空' if side=='short' else '多'} {model} "
              f"{ent:,.0f}→{ex:,.0f}  {p:+.2f}U  {r:+.2f}R  {why}")
        A("")
    A("  ── 当前持仓 ─────────────────────────")
    pos = st.position
    if pos.is_open:
        A(f"    {'做空' if pos.side=='short' else '做多'} {pos.size:.2f} 张  开仓 {pos.entry:,.1f}")
        A(f"    止损 {pos.stop:,.1f}   止盈 {'/'.join(f'{t:,.0f}' for t in pos.targets)}")
        A(f"    模型 {pos.model}   已持 {(now.timestamp()*1000-pos.opened_at)/3.6e6:.1f} 小时")
    else:
        A("    空仓")
    A("=" * 62)

    txt = "\n".join(L)
    print(txt)
    if "--save" in sys.argv:
        p = LOG_DIR / f"perf-{now.strftime('%Y%m%d-%H%M')}.md"
        p.write_text("```\n" + txt + "\n```\n", encoding="utf-8")
        print(f"\n已保存 {p}")


if __name__ == "__main__":
    main()
