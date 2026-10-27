"""验证 OKX 强平流接口是否因为 simulated 头而返回了模拟数据

怀疑：OkxClient(simulated=True) 会带 x-simulated-trading: 1 头，
      导致 /public/liquidation-orders 返回的是【模拟盘数据】而不是真实市场数据。
      这能解释为什么出现 80,120 这个现价根本不存在的价位、以及单笔 41,401 张的异常量。
"""

import json
import urllib.request

URL = ("https://www.okx.com/api/v5/public/liquidation-orders"
       "?instType=SWAP&uly=BTC-USDT&state=filled&limit=100")


def fetch(with_sim_header: bool):
    op = urllib.request.build_opener(
        urllib.request.ProxyHandler({
            "http": "http://127.0.0.1:57777",
            "https": "http://127.0.0.1:57777"}))
    h = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
    if with_sim_header:
        h["x-simulated-trading"] = "1"
    req = urllib.request.Request(URL, headers=h)
    r = op.open(req, timeout=30)
    return json.loads(r.read().decode())


def summarize(d, label):
    data = d.get("data") or []
    det = (data[0].get("details") or []) if data else []
    print("=== %s ===" % label)
    print("  条数:", len(det), " code:", d.get("code"))
    if not det:
        print("  （空）")
        return
    px = sorted(float(x["bkPx"]) for x in det)
    sz = sorted(float(x["sz"]) for x in det)
    print("  价格范围: %.1f ~ %.1f" % (px[0], px[-1]))
    print("  单笔张数: 最小 %.2f  最大 %.2f  合计 %.1f"
          % (sz[0], sz[-1], sum(sz)))
    print("  前 5 条:")
    for x in det[:5]:
        print("    %-6s bkPx=%-12s sz=%-10s posSide=%s"
              % (x.get("side"), x.get("bkPx"), x.get("sz"), x.get("posSide")))


for flag, label in ((True, "带 x-simulated-trading: 1（机器人当前用的）"),
                    (False, "不带 simulated 头（真实市场）")):
    try:
        d = fetch(flag)
        summarize(d, label)
    except Exception as e:
        print("=== %s ===\n  失败: %s" % (label, str(e)[:120]))
    print()
