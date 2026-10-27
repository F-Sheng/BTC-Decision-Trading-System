"""对比 OKX 单家 vs LiquidVision 7 家 的强平流：速度、覆盖、一致性

用户 2026-10-28 指出：我说过 LV 强平流更齐全，却一直用 OKX，前后矛盾。
本脚本实测两个源，决定最终方案。
"""

import json
import sys
import time
import urllib.request

sys.path.insert(0, r"D:\DP workspace\btc_system")
from core import lv_api

PROXY = {"http": "http://127.0.0.1:57777", "https": "http://127.0.0.1:57777"}


def okx_liq():
    t0 = time.time()
    url = ("https://www.okx.com/api/v5/public/liquidation-orders"
           "?instType=SWAP&uly=BTC-USDT&state=filled&limit=100")
    op = urllib.request.build_opener(urllib.request.ProxyHandler(PROXY))
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    d = json.loads(op.open(req, timeout=30).read().decode())
    det = ((d.get("data") or [{}])[0].get("details") or [])
    return {
        "ok": True, "elapsed": round(time.time() - t0, 2), "n": len(det),
        "rows": [{"ts": int(x["ts"]), "exchange": "okx", "side": x.get("side"),
                  "px": float(x["bkPx"]), "sz": float(x["sz"]),
                  "posSide": x.get("posSide")} for x in det],
    }


def lv_liq(symbol=None, minutes=60):
    t0 = time.time()
    q = "/api/v1/liquidations?minutes=%d&limit=500" % minutes
    if symbol:
        q += "&symbol=" + symbol
    try:
        d = lv_api.get(q, timeout=60)
        rows = d if isinstance(d, list) else []
        return {"ok": True, "elapsed": round(time.time() - t0, 2), "n": len(rows),
                "rows": rows}
    except Exception as e:
        return {"ok": False, "elapsed": round(time.time() - t0, 2),
                "why": str(e)[:90], "rows": []}


print("=" * 70)
print(" 强平流数据源对比")
print("=" * 70)

# 1) OKX
try:
    o = okx_liq()
    print("【OKX 单家】")
    print("  耗时 %.2f 秒  条数 %d" % (o["elapsed"], o["n"]))
    if o["rows"]:
        ts = sorted(r["ts"] for r in o["rows"])
        print("  时间跨度 %.1f 分钟" % ((ts[-1] - ts[0]) / 60000))
        print("  最新一笔 %.0f 秒前" % ((time.time() * 1000 - ts[-1]) / 1000))
        buy = sum(r["sz"] for r in o["rows"] if r["side"] == "buy")
        sell = sum(r["sz"] for r in o["rows"] if r["side"] == "sell")
        print("  buy(空头被爆) %.1f 张 / sell(多头被爆) %.1f 张" % (buy, sell))
except Exception as e:
    print("【OKX 单家】失败:", str(e)[:100])
    o = {"rows": [], "n": 0}

print()

# 2) LV 全市场
for label, kw in (("LV 不筛币种(全市场)", {}),
                  ("LV symbol=BTC", {"symbol": "BTC"}),
                  ("LV symbol=BTCUSDT", {"symbol": "BTCUSDT"})):
    r = lv_liq(**kw)
    print("【%s】" % label)
    if not r["ok"]:
        print("  失败:", r.get("why"))
        print()
        continue
    print("  耗时 %.2f 秒  条数 %d" % (r["elapsed"], r["n"]))
    if r["rows"]:
        btc = [x for x in r["rows"]
               if str(x.get("symbol", "")).upper().startswith("BTC")]
        print("    BTC 相关 %d 条" % len(btc))
        ex = {}
        for x in btc:
            ex[x.get("exchange")] = ex.get(x.get("exchange"), 0) + 1
        print("    交易所分布:", ex)
        if btc:
            px = [x["price"] for x in btc]
            print("    价格范围 %.1f ~ %.1f" % (min(px), max(px)))
            ts = sorted(x["ts"] for x in btc)
            print("    最新 %.0f 秒前" % ((time.time() * 1000 - ts[-1]) / 1000))
            qty = sum(float(x.get("qty") or 0) for x in btc)
            print("    合计数量 %.3f（注意单位可能与 OKX 张不同）" % qty)
    print()
