"""探测 LiquidVision API 各端点权限（用户 2026-10-28 提供的 key）"""

import json
import urllib.request

KEY = "Iv_wxwcAjfgNLFNp_RD_FfDRkyENKb9Zgy8hynrDWr3Zy4"
BASE = "https://liquidvision.app"

EPS = [
    ("/api/v1/coins", "币种排名"),
    ("/api/v1/funding", "跨交易所资金费率"),
    ("/api/v1/oi", "未平仓合约"),
    ("/api/v1/oi/history/BTCUSDT", "OI 历史"),
    ("/api/v1/longshort", "多空比"),
    ("/api/v1/liquidations?minutes=60", "清算"),
    ("/api/v1/liquidations/summary", "清算汇总"),
    ("/api/v1/heatmap/binance/BTCUSDT", "热力图网格"),
]


def call(path):
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req = urllib.request.Request(
        BASE + path,
        headers={"Authorization": "Bearer " + KEY,
                 "Accept": "application/json",
                 "User-Agent": "Mozilla/5.0"})
    try:
        r = op.open(req, timeout=20)
        body = r.read().decode("utf-8", "replace")
        return r.status, body
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        return 0, str(e)[:120]


for path, label in EPS:
    code, body = call(path)
    print("=== %s  [HTTP %s]  %s" % (label, code, path))
    if code == 200:
        try:
            d = json.loads(body)
            if isinstance(d, dict):
                print("    顶层键:", list(d.keys())[:12])
                for k in ("coins", "data", "result", "heatmap", "grid", "buckets"):
                    v = d.get(k)
                    if isinstance(v, list):
                        print("    %s: list[%d]" % (k, len(v)))
                        if v:
                            print("      样本:", json.dumps(v[0], ensure_ascii=False)[:200])
                            break
                    elif isinstance(v, dict):
                        print("    %s: dict keys=%s" % (k, list(v.keys())[:10]))
            elif isinstance(d, list):
                print("    list[%d] 样本:" % len(d),
                      json.dumps(d[0], ensure_ascii=False)[:200] if d else "")
        except Exception as e:
            print("    解析失败:", e, "| 原始:", body[:150])
    else:
        print("    ", body[:220])
    print()
