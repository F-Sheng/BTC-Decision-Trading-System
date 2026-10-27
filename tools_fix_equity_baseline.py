"""修正 state 里的 start_equity 残留（必须在机器人停止时执行）

问题：
  state.bot_state.json 里 start_equity = 87,606.12（V2 时代旧值）
  config.json 里 run_start_equity    = 84,478.20（V3 正确起点，10-27 01:07:56）

  之前我在机器人运行时改文件，机器人下一轮用内存里的旧值覆盖回去了。
  必须先停机器人再改。

同时：state.equity 是 None，需要补上当前权益。
"""

import json
import sys
import time

sys.path.insert(0, r"D:\DP workspace\btc_system")

cp = r"D:\DP workspace\btc_system\config.json"
sp = r"D:\DP workspace\btc_system\state\bot_state.json"

c = json.load(open(cp, encoding="utf-8"))
s = json.load(open(sp, encoding="utf-8"))

# 1) 拿 OKX 实时权益
try:
    from core.okx import OkxClient
    ok = OkxClient(simulated=True)
    b = ok.balance()
    equity = float(b.get("totalEq") or 0)
except Exception as e:
    equity = 0.0
    print("  取权益失败:", str(e)[:80])

old_se = s.get("start_equity")
old_eq = s.get("equity")

s["start_equity"] = c.get("run_start_equity")
s["started_at"] = c.get("run_started_at")
if equity > 0:
    s["equity"] = equity

json.dump(s, open(sp, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

print("=== state 修正 ===")
print("  start_equity: %s -> %s" % (old_se, s["start_equity"]))
print("  started_at  : -> %s" % s["started_at"])
print("  equity      : %s -> %s" % (old_eq, s.get("equity")))
print()
print("=== 权益口径统一（三个来源）===")
base = float(c.get("run_start_equity") or 0)
print("  V3 起始权益（config）  : %,.2f" % base if False else "  V3 起始权益（config）  : %.2f" % base)
print("  OKX 实时 totalEq       : %.2f" % equity)
if base > 0 and equity > 0:
    print("  V3 区间盈亏            : %+.2f U (%+.2f%%)"
          % (equity - base, (equity - base) / base * 100))
print()
print("  说明：perf.py 优先读 config，所以之前的统计是对的；")
print("       state.start_equity 只是残留，会造成混淆，现已同步。")
