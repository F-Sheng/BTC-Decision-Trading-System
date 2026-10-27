"""把当前持仓的止盈调到「关键位内侧 100 点」

用户 2026-10-28 要求：
  多单止盈必须放在阻力位下方（留 100 点），防止价格没突破阻力就直接回头，
  导致止盈永远打不到。空单同理，要放在支撑位上方。
  系统算出的最近压力位 = 80,857 -> 多单 TP1 应该 = 80,757（不是 80,822.6）
"""

import json
import sys

sys.path.insert(0, r"D:\DP workspace\btc_system")

from core import journal
from core.okx import OkxClient

SAFETY = 100.0

c = OkxClient(simulated=True)
st = journal.load_state()

# 1) 先看有没有挂着的止盈单
try:
    r = c._request("GET", "/api/v5/trade/orders-pending",
                   {"instType": "SWAP", "instId": "BTC-USDT-SWAP"})
    pend = r.get("data") or []
    print("待成交订单 %d 个:" % len(pend))
    for o in pend:
        print("  %s %s sz=%s px=%s side=%s posSide=%s"
              % (o.get("ordId"), o.get("ordType"), o.get("sz"),
                 o.get("px"), o.get("side"), o.get("posSide")))
except Exception as e:
    print("查待成交单失败:", e)
    pend = []

# 2) 算新的止盈
RES_LONG = 80857.0    # 系统最近压力位
SUP_SHORT = None      # 空单需要最近支撑位，稍后用机器人日志

new_tp_long = round(RES_LONG - SAFETY, 1)
print()
print("多单：最近压力 %.1f - %.0f = 新 TP1 %.1f" % (RES_LONG, SAFETY, new_tp_long))

changed = False
for side in st.open_sides():
    pos = st.get_pos(side)
    if not pos.targets:
        continue
    old = list(pos.targets)
    if side == "long":
        pos.targets[0] = min(pos.targets[0], new_tp_long)
        # 后续档位也不许高于压力位
        for i in range(1, len(pos.targets)):
            if pos.targets[i] and pos.targets[i] > new_tp_long:
                pos.targets[i] = round(pos.targets[i] - SAFETY, 1)
    else:
        # 空单：支撑位上方留 100 点（等日志给出支撑位再处理，先不动）
        pass
    if list(pos.targets) != old:
        changed = True
        print("%s 止盈 %s -> %s" % (side, [format(x, ',.1f') for x in old],
                                    [format(x, ',.1f') for x in pos.targets]))

# 3) 保存
if changed:
    journal.save_state(st)
    print("\n已保存 state")
else:
    print("\n无变化")

# 4) 复核
print()
for side in st.open_sides():
    pos = st.get_pos(side)
    print("  %s: 开仓 %.1f 止损 %.1f 止盈 %s 已成交档 %s"
          % (side, pos.entry, pos.stop,
             [format(x, ',.1f') for x in (pos.targets or [])], pos.tp_filled))
