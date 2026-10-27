"""核实「80,120 强平」数据是否真实存在

用户质疑：「挤压方向不对吧，你在哪里的数据？从来没有 80,120 这个数据啊」

我之前的说法来自 OKX /api/v5/public/liquidation-orders 的 bkPx 字段。
现在用 LiquidVision 跨交易所数据交叉验证。
"""

import json
import sys
import urllib.request
from collections import Counter

KEY = "Iv_wxwcAjfgNLFNp_RD_FfDRkyENKb9Zgy8hynrDWr3Zy4"
BASE = "https://liquidvision.app"


def call(path, tmo=50):
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req = urllib.request.Request(
        BASE + path,
        headers={"Authorization": "Bearer " + KEY,
                 "Accept": "application/json", "User-Agent": "Mozilla/5.0"})
    try:
        r = op.open(req, timeout=tmo)
        return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        return 0, str(e)[:100]


print("=== LiquidVision 最近 60 分钟 BTC 清算 ===")
code, body = call("/api/v1/liquidations?minutes=60")
if code != 200:
    print("  获取失败", code, body[:120])
    sys.exit(1)
rows = json.loads(body)
btc = [r for r in rows if r.get("symbol", "").startswith("BTC")]
print("  总条数 %d，其中 BTC %d 条" % (len(rows), len(btc)))

if btc:
    px = [r["price"] for r in btc]
    print("  BTC 清算价格范围: %.1f ~ %.1f" % (min(px), max(px)))
    sides = Counter(r["side"] for r in btc)
    print("  方向:", dict(sides))
    print("  最近 8 条:")
    for r in sorted(btc, key=lambda x: -x["ts"])[:8]:
        import datetime
        t = datetime.datetime.fromtimestamp(r["ts"] / 1000).strftime("%H:%M:%S")
        print("    %s %-8s %-5s @ %.1f  qty=%.3f"
              % (t, r["exchange"], r["side"], r["price"], r["qty"]))
    # 是否出现 80,120 附近
    near = [r for r in btc if 80_050 <= r["price"] <= 80_200]
    print("  80,050~80,200 区间的清算: %d 条" % len(near))
    for r in near[:5]:
        import datetime
        t = datetime.datetime.fromtimestamp(r["ts"] / 1000).strftime("%H:%M:%S")
        print("    %s %-8s %-5s @ %.1f qty=%.3f"
              % (t, r["exchange"], r["side"], r["price"], r["qty"]))
else:
    print("  无 BTC 清算记录")

print()
print("=== 对照：OKX liquidation-orders 的 80,120 ===")
sys.path.insert(0, r"D:\DP workspace\btc_system")
from core.okx import OkxClient
c = OkxClient(simulated=True)
r = c._request("GET", "/api/v5/public/liquidation-orders",
               {"instType": "SWAP", "uly": "BTC-USDT",
                "state": "filled", "limit": "100"})
det = ((r.get("data") or [{}])[0].get("details") or [])
print("  OKX 返回 %d 条" % len(det))
for x in det[:12]:
    import datetime
    t = datetime.datetime.fromtimestamp(int(x["ts"]) / 1000).strftime("%H:%M:%S")
    print("    %s %-5s bkPx=%-10s sz=%s posSide=%s"
          % (t, x.get("side"), x.get("bkPx"), x.get("sz"), x.get("posSide")))
