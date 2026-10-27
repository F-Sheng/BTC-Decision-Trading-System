import datetime
import json
import sys
from collections import Counter

f = sys.argv[1] if len(sys.argv) > 1 else r"C:\Users\81559\AppData\Local\Temp\lvliq.json"
try:
    rows = json.load(open(f, encoding="utf-8"))
except Exception as e:
    print("读取失败:", e)
    sys.exit(1)

btc = [r for r in rows if str(r.get("symbol", "")).startswith("BTC")]
print("  总 %d 条, BTC %d 条" % (len(rows), len(btc)))
if btc:
    px = [r["price"] for r in btc]
    print("  BTC 清算价格范围 %.1f ~ %.1f" % (min(px), max(px)))
    print("  方向:", dict(Counter(r["side"] for r in btc)))
    print("  交易所:", dict(Counter(r["exchange"] for r in btc)))
    print("  最近 10 条:")
    for r in sorted(btc, key=lambda x: -x["ts"])[:10]:
        t = datetime.datetime.fromtimestamp(r["ts"] / 1000).strftime("%H:%M:%S")
        print("    %s %-8s %-5s @ %.1f  qty=%.3f"
              % (t, r["exchange"], r["side"], r["price"], r["qty"]))
    near = [r for r in btc if 80050 <= r["price"] <= 80200]
    print("  80,050~80,200 区间: %d 条" % len(near))
    for r in near[:6]:
        t = datetime.datetime.fromtimestamp(r["ts"] / 1000).strftime("%H:%M:%S")
        print("    %s %-8s %-5s @ %.1f  qty=%.3f"
              % (t, r["exchange"], r["side"], r["price"], r["qty"]))
else:
    print("  无 BTC 清算记录（可能 symbol 格式是 BTCUSDT）")
    syms = list({r.get("symbol") for r in rows})[:10]
    print("  样本 symbol:", syms)
