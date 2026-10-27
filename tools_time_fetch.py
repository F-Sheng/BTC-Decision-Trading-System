"""拆解 local_fetch 各阶段耗时，定位慢在哪"""
import json
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(r"D:\DP workspace\btc_system")
sys.path.insert(0, str(ROOT))
sys.path.insert(0, r"E:\btc-ai")

import local_fetch as LF

t = {}

t0 = time.time()
m = LF.fetch_map()
t["map"] = time.time() - t0

t0 = time.time()
lsr = LF.parse_lsr(LF.fetch_lsr())
t["lsr"] = time.time() - t0

t0 = time.time()
pro = LF.fetch_x(LF.X_PRO, "pro")
t["x_pro"] = time.time() - t0

t0 = time.time()
ret = LF.fetch_x(LF.X_RETAIL, "retail")
t["x_retail"] = time.time() - t0

print("=== 各阶段耗时 ===")
for k, v in t.items():
    print("  %-10s %6.1f 秒" % (k, v))
print("  %-10s %6.1f 秒" % ("合计", sum(t.values())))
print()
print("map ok =", m.get("ok"), " why =", m.get("why"))
if m.get("ok"):
    print("  spot=%s up=+%s%%(%sM) dn=-%s%%(%sM)" % (
        m["spot"], m["upper"]["pct"], m["upper"]["short_m"],
        m["lower"]["pct"], m["lower"]["long_m"]))
print("X pro=%d retail=%d" % (len(pro), len(ret)))
