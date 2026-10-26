"""每日复盘：今天发生了什么、为什么没做、数据健不健康、参数该不该调。

用法：
    python review.py            # 复盘今天
    python review.py --days 3   # 复盘最近 3 天
"""
from __future__ import annotations

import os
import sys
import time
from collections import Counter
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import coinglass_file, jin10, journal, liq_live, sentiment, x_sentiment
from core.utils import CST, LOG_DIR, ROOT, read_json


def main():
    days = 1
    if "--days" in sys.argv:
        try:
            days = int(sys.argv[sys.argv.index("--days") + 1])
        except Exception:
            pass

    now = datetime.now(CST)
    since = int((now - timedelta(days=days)).timestamp() * 1000)
    con = journal._conn()
    L = []
    A = L.append

    A("=" * 68)
    A(f"  每日复盘 · {now.strftime('%Y-%m-%d %H:%M')} · 回看 {days} 天")
    A("=" * 68)

    # ---------- 1. 资金 ----------
    rows = con.execute("SELECT ts, equity FROM equity ORDER BY ts").fetchall()
    rows = [r for r in rows if r[0] >= since]
    cfg = read_json(ROOT / "config.json", {}) or {}
    se = cfg.get("v1_start_equity") or 0
    if rows:
        e0, e1 = rows[0][1], rows[-1][1]
        A(f"\n【资金】{e0:,.0f} → {e1:,.0f}  ({e1-e0:+,.0f} USDT / {(e1/e0-1)*100:+.2f}%)")
        A(f"        起始基准 {se:,.0f}  累计 {e1-se:+,.0f} USDT ({(e1/se-1)*100:+.2f}%)")

    # ---------- 2. 信号漏斗 ----------
    sig = con.execute(
        "SELECT ts, model, side, conf, accepted, reason FROM signals WHERE ts>=? ORDER BY ts",
        (since,)).fetchall()
    A(f"\n【信号漏斗】共生成 {len(sig)} 条")
    if sig:
        by_model = Counter((m, s) for _, m, s, _, _, _ in sig)
        for (m, s), c in by_model.most_common():
            A(f"        模型{m} {'做多' if s=='long' else '做空'}  {c:>3} 次")
        acc = sum(1 for r in sig if r[4])
        A(f"        被采纳 {acc} 条，未采纳 {len(sig)-acc} 条")
        conf = [r[3] for r in sig]
        A(f"        置信度 min {min(conf):.2f} / 均值 {sum(conf)/len(conf):.2f} / max {max(conf):.2f}")

        # 未执行原因（从日志里扒）
        logf = LOG_DIR / f"btc-{now.strftime('%Y%m%d')}.log"
        why = Counter()
        if logf.exists():
            for line in logf.read_text(encoding="utf-8", errors="ignore").splitlines():
                if "已有持仓" in line and "不执行" in line:
                    why["已有持仓（单仓位模式）"] += 1
                elif "仓位系数" in line and "<" in line and "忽略" in line:
                    why["反向信号力度不足"] += 1
                elif "置信度" in line and "放弃" in line:
                    why["置信度不足"] += 1
                elif "处于停牌期" in line:
                    why["停牌中"] += 1
        if why:
            A("        未执行原因：")
            for k, v in why.most_common():
                A(f"          · {k}: {v} 次")

    # ---------- 3. 成交 ----------
    tr = con.execute(
        "SELECT close_ts, side, model, entry, exit, pnl_usd, r_multiple, reason "
        "FROM trades WHERE close_ts>=? ORDER BY close_ts", (since,)).fetchall()
    A(f"\n【成交】平仓 {len(tr)} 笔")
    if tr:
        wins = sum(1 for r in tr if (r[5] or 0) > 0)
        rs = [r[6] or 0 for r in tr]
        A(f"        胜率 {wins/len(tr)*100:.0f}%   平均R {sum(rs)/len(rs):+.2f}   "
          f"合计 {sum(r[5] or 0 for r in tr):+.2f} USDT")
        for r in tr[-6:]:
            t = datetime.fromtimestamp(r[0] / 1000, CST).strftime("%m-%d %H:%M")
            A(f"        {t} {'空' if r[1]=='short' else '多'} {r[2]} "
              f"{r[3]:,.0f}→{r[4]:,.0f}  {r[5]:+.1f}U  {r[6]:+.2f}R  {r[7]}")
    else:
        A("        今天还没有平仓（持仓未到止盈/止损）")

    # ---------- 4. 数据健康 ----------
    A("\n【数据健康】")
    cg = coinglass_file.load()
    A(f"        CoinGlass    {'✅ 新鲜' if cg.fresh else '⚠️ 过期'}  {cg.age_min:.0f} 分钟前"
      + (f"  R={cg.r_ratio:.2f}" if cg.r_ratio else ""))
    ll = liq_live.load()
    A(f"        真实强平流   {'✅' if ll.ok else '⚠️'}  {ll.n} 笔 / {ll.window_h:.1f} 小时"
      + (f"  挤压比 {ll.squeeze_ratio:.2f}" if ll.squeeze_ratio else ""))
    xv = x_sentiment.load(cfg)
    A(f"        推特情绪     {'✅' if xv.fresh else '⚠️ 过期'}  专业{xv.pro_n}条/散户{xv.retail_n}条")
    jf = jin10.fetch()
    A(f"        金十快讯     {'✅' if jf.lst else '⚠️ 无'}  {len(jf.lst)} 条")
    sn = sentiment.load(jin10_score=jf.score, cfg=cfg)
    A(f"        情绪合成     恐贪{sn.fng_value} 社区{(sn.gecko_up_pct or 0):.0f}% "
      f"交易员{sn.st_bull}/{sn.st_bear}  → {sn.score:+d}")

    # ---------- 5. 参数体检 ----------
    A("\n【参数体检】")
    flags = []
    if sig and sum(1 for r in sig if r[4]) == 0:
        flags.append(f"今天 {len(sig)} 条信号全部未执行 —— 检查是否单仓位卡死，或 NEAR_TOL 过紧")
    if not sig:
        flags.append("今天零信号 —— 关键位过滤可能过严（min_pct 0.3% / NEAR_TOL 0.5%）")
    if sig:
        acc = sum(1 for r in sig if r[4])
        if len(sig) >= 20 and acc <= 1:
            flags.append(f"信号 {len(sig)} 条仅采纳 {acc} 条 —— 仓位折扣或多重门控过严")
    if not cg.fresh:
        flags.append("CoinGlass 已过期 —— 清算层降级为真实强平流或中性")
    if ll.ok and ll.n < 30:
        flags.append("真实强平流样本不足 —— 需要持续运行累积")
    if not flags:
        flags.append("当前无异常，参数维持")
    for f_ in flags:
        A(f"        · {f_}")

    A("\n【建议动作】")
    A("        1) 若有平仓样本 ≥10 笔，用 sweep 重新标定 min_confidence / NEAR_TOL")
    A("        2) 每周核对一次各层权重（L1 九项 / L2 燃料与持仓 / 情绪合成）")
    A("        3) 检查数据源新鲜度曲线，CoinGlass 过期率超过 30% 就提高刷新频率")
    A("=" * 68)

    txt = "\n".join(L)
    print(txt)
    out = LOG_DIR / f"review-{now.strftime('%Y%m%d-%H%M')}.md"
    out.write_text("```\n" + txt + "\n```\n", encoding="utf-8")
    print(f"\n已保存 {out}")


if __name__ == "__main__":
    main()
