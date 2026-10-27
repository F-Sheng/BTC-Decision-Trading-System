import json

cp = r"D:\DP workspace\btc_system\config.json"
sp = r"D:\DP workspace\btc_system\state\bot_state.json"

c = json.load(open(cp, encoding="utf-8"))
s = json.load(open(sp, encoding="utf-8"))

old_se = s.get("start_equity")
s["start_equity"] = c.get("run_start_equity")
s["started_at"] = c.get("run_started_at")
json.dump(s, open(sp, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

print("  start_equity: %s -> %s" % (old_se, s["start_equity"]))
print("  started_at  已同步为 config 的 V3 起点")
print()
print("=== 核对 V3 期已实现盈亏（排除 V3 起点之前的交易）===")

import sys
sys.path.insert(0, r"D:\DP workspace\btc_system")
from core import journal
import datetime
from datetime import timezone, timedelta
CST = timezone(timedelta(hours=8))

c2 = journal._conn()
t0 = c.get("run_started_at")
rows = c2.execute(
    "select close_ts,side,entry,exit,size,pnl_usd,reason from trades "
    "where close_ts>=? order by close_ts", (t0,)).fetchall()
total = 0.0
for r in rows:
    t = datetime.datetime.fromtimestamp(r[0] / 1000, CST).strftime("%m-%d %H:%M")
    total += r[5] or 0
    print("  %s 平%-5s %.1f张 %,.1f->%,.1f  %+.2fU  %s"
          % (t, r[1], r[4], r[2], r[3], r[5] or 0, r[6]))
print("  ────────────────────────────────")
print("  V3 期已实现合计: %+.2f U" % total)
print()
rows_all = c2.execute(
    "select close_ts,pnl_usd from trades where close_ts<? order by close_ts",
    (t0,)).fetchall()
if rows_all:
    print("  （V3 起点之前的历史交易 %d 笔，合计 %+.2f U，不计入 V3）"
          % (len(rows_all), sum(r[1] or 0 for r in rows_all)))
