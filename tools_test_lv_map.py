import json
import urllib.request

KEY = "Iv_wxwcAjfgNLFNp_RD_FfDRkyENKb9Zgy8hynrDWr3Zy4"
BASE = "https://liquidvision.app"


def call(path, tmo=60):
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
        return 0, str(e)[:120]


tests = [
    "/api/v1/liqmap/BTC?exchange=all&range=1d&model=v3",
    "/api/v1/liqmap/BTCUSDT?exchange=binance&range=1d",
    "/api/v1/liqmap/btc?exchange=all&range=1d",
    "/api/v1/liqheat/BTC?exchange=all&range=1d",
    "/api/v1/liquidations?symbol=BTC&minutes=120&min_usd=10000&limit=200",
]

for u in tests:
    code, body = call(u)
    print("=== HTTP %s   %s" % (code, u))
    if code == 200:
        try:
            d = json.loads(body)
            if isinstance(d, dict):
                print("    键:", list(d.keys())[:12])
                # liqmap 的关键字段
                for k in ("bins", "cols", "levels", "price", "spot", "ts", "error"):
                    if k in d:
                        v = d[k]
                        if isinstance(v, list):
                            print("    %s: list[%d]" % (k, len(v)))
                            if v:
                                print("      前2:", json.dumps(v[:2], ensure_ascii=False)[:300])
                        else:
                            print("    %s = %s" % (k, json.dumps(v, ensure_ascii=False)[:200]))
            elif isinstance(d, list):
                print("    list[%d] 前2:" % len(d), json.dumps(d[:2], ensure_ascii=False)[:300])
        except Exception as e:
            print("    解析失败:", e, "|", body[:200])
    else:
        print("    ", body[:250])
    print()
