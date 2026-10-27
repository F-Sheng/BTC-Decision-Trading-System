"""全面测试 LiquidVision 关键端点（重点：liqmap / liqheat = 清算地图 JSON）"""

import json
import urllib.request

KEY = "Iv_wxwcAjfgNLFNp_RD_FfDRkyENKb9Zgy8hynrDWr3Zy4"
BASE = "https://liquidvision.app"

EPS = [
    ("/api/v1/x402", "x402 支付说明"),
    ("/api/v1/liqmap/BTC", "清算地图 liqmap"),
    ("/api/v1/liqheat/BTC", "清算热力 liqheat"),
    ("/api/v1/liquidations/summary", "清算汇总"),
    ("/api/v1/liquidations/cascades", "清算级联"),
    ("/api/v1/oi/changes", "OI 变化"),
    ("/api/v1/oi/regime", "OI 状态"),
    ("/api/v1/flows", "资金流"),
    ("/api/v1/sparks", "异动"),
]


def call(path, tmo=40):
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


for path, label in EPS:
    code, body = call(path)
    print("=== %s  [HTTP %s]  %s" % (label, code, path))
    if code == 200:
        try:
            d = json.loads(body)
            if isinstance(d, dict):
                print("    顶层键:", list(d.keys())[:14])
                print("    ", json.dumps(d, ensure_ascii=False)[:420])
            elif isinstance(d, list):
                print("    list[%d]" % len(d))
                print("    ", json.dumps(d[:2], ensure_ascii=False)[:420])
        except Exception as e:
            print("    解析失败:", e, "|", body[:200])
    else:
        print("    ", body[:260])
    print()
