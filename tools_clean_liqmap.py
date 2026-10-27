"""清理 coinglass.json 中被污染的清算地图数据

背景：21:57 那轮 local_fetch 的下方读数错误（-413.07%），写进去的 R=1.31 是脏数据，
      但 fresh=True 导致机器人 L2 仍在使用它。
现在 local_fetch 已加校验会拒绝这类数据，这里清理掉历史脏值，让机器人退回自算 proxy。
"""

import json
import sys

sys.path.insert(0, r"D:\DP workspace\btc_system")
from pathlib import Path

root = Path(r"D:\DP workspace\btc_system")
p = root / "data" / "coinglass.json"
d = json.load(open(p, encoding="utf-8"))

blk = d.get("binance_liqmap_1d") or {}
near = blk.get("near") or {}
far = blk.get("far") or {}
print("现状:")
print("  near:", {k: near.get(k) for k in ("pct", "up_px", "dn_px", "short", "long")})
print("  far :", {k: far.get(k) for k in ("pct", "up_px", "dn_px", "short", "long")})

spot = blk.get("spot") or 0
up_px = near.get("up_px") or 0
dn_px = near.get("dn_px") or 0
short = near.get("short") or 0
long_ = near.get("long") or 0

problems = []
if not spot:
    problems.append("no spot")
if not up_px or not dn_px:
    problems.append("no px")
else:
    upp = (up_px - spot) / spot * 100
    dnp = (spot - dn_px) / spot * 100
    print("  推算上侧 %+.2f%%  下侧 %+.2f%%" % (upp, dnp))
    if not (1.5 <= upp <= 6.0):
        problems.append("upper pct %.2f" % upp)
    if not (1.5 <= dnp <= 6.0):
        problems.append("lower pct %.2f" % dnp)
if not short or not long_:
    problems.append("no cumulative")
elif not (0.2 <= short / long_ <= 5.0):
    problems.append("R implausible %.2f" % (short / long_))

if problems:
    print("\n判定为脏数据:", "; ".join(problems))
    d.pop("binance_liqmap_1d", None)
    d["_liqmap_invalidated"] = {
        "reason": "; ".join(problems),
        "note": "local_fetch 校验未通过，已移除；机器人将退回自算 proxy",
    }
    json.dump(d, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("已移除 binance_liqmap_1d，机器人会用自算 L2[PROXY]")
else:
    print("\n数据看起来正常，保留")
