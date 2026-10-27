"""探测 OKX 真实强平流接口

返回的是真实爆仓成交，不是估算：
  {"details": [{"bkPx": 价格, "posSide": "short/long", "side": "buy/sell",
                "sz": 张数, "ts": 毫秒时间戳, "bkLoss": 穿仓损失}, ...]}
"""

import sys
import time

sys.path.insert(0, r"D:\DP workspace\btc_system")
from core.okx import OkxClient

c = OkxClient(simulated=True)

print("=== 1. 基础查询（uly=BTC-USDT）===")
r = c._request("GET", "/api/v5/public/liquidation-orders",
               {"instType": "SWAP", "uly": "BTC-USDT",
                "state": "filled", "limit": "100"})
data = r.get("data") or []
print("  外层条数:", len(data))
if data:
    d0 = data[0]
    det = d0.get("details") or []
    print("  第一条 instId:", d0.get("instId"), " 明细数:", len(det))
    print("  明细时间跨度:")
    if det:
        ts = [int(x["ts"]) for x in det]
        span = (max(ts) - min(ts)) / 1000
        print("    最新: %.0f 秒前   最早: %.0f 秒前   跨度 %.0f 秒"
              % ((time.time() * 1000 - max(ts)) / 1000,
                 (time.time() * 1000 - min(ts)) / 1000, span))
        # 统计
        buy_sz = sum(float(x["sz"]) for x in det if x["side"] == "buy")
        sell_sz = sum(float(x["sz"]) for x in det if x["side"] == "sell")
        print("    买(空头爆仓) %.1f 张 / 卖(多头爆仓) %.1f 张" % (buy_sz, sell_sz))
        print("    样本前 3 条:")
        for x in det[:3]:
            print("      ", x)

print()
print("=== 2. 所有 SWAP 品种（看是否分 instId）===")
print("  返回的 instId 列表:", [x.get("instId") for x in data[:8]])

print()
print("=== 3. 连续两次调用看是否增量 ===")
r2 = c._request("GET", "/api/v5/public/liquidation-orders",
                {"instType": "SWAP", "uly": "BTC-USDT",
                 "state": "filled", "limit": "100"})
d2 = (r2.get("data") or [{}])[0].get("details") or []
ts1 = [int(x["ts"]) for x in (data[0].get("details") or [])]
ts2 = [int(x["ts"]) for x in d2]
print("  第一次最新 ts:", max(ts1) if ts1 else None)
print("  第二次最新 ts:", max(ts2) if ts2 else None)
print("  条数:", len(ts1), "->", len(ts2))
