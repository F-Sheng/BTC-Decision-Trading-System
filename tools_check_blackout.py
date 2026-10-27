import json
import sys

sys.path.insert(0, r"D:\DP workspace\btc_system")
from core import events

cfg = json.load(open(r"D:\DP workspace\btc_system\config.json", encoding="utf-8"))
ev = events.build(cfg, news=[])
print("blackout =", ev.blackout)
print("adjust   =", ev.adjust)
print()
print("notes:")
for n in ev.notes:
    if "FOMC" in n or "静默" in n or "公布" in n:
        print("  -", n)
print()
print("events:")
for e in ev.events:
    print("  %s days=%s impact=%s" % (e["name"], e["days"], e["impact"]))
