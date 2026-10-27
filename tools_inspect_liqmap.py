import json
import urllib.request

KEY = "Iv_wxwcAjfgNLFNp_RD_FfDRkyENKb9Zgy8hynrDWr3Zy4"


def call(path, tmo=60):
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req = urllib.request.Request(
        "https://liquidvision.app" + path,
        headers={"Authorization": "Bearer " + KEY,
                 "Accept": "application/json", "User-Agent": "Mozilla/5.0"})
    r = op.open(req, timeout=tmo)
    return json.loads(r.read().decode())


d = call("/api/v1/liqmap/BTCUSDT?exchange=all&range=1d&model=v3")
print("顶层键:", list(d.keys()))
print("symbol:", d.get("symbol"), " exchange:", d.get("exchange"))
print("range:", d.get("range"), " model:", d.get("model"))
print("current_price:", d.get("current_price"))
print("ts:", d.get("ts"), " age_s:", d.get("age_s"))
print()
for k in ("cum_long", "cum_short", "by_leverage", "by_exchange"):
    v = d.get(k)
    print("%s: %s" % (k, json.dumps(v, ensure_ascii=False)[:400] if not isinstance(v, list)
                      else "list[%d] 前3=%s" % (len(v), json.dumps(v[:3], ensure_ascii=False)[:250])))
print()
bins = d.get("bins") or []
print("bins: list[%d]" % len(bins))
if bins:
    print("  类型:", type(bins[0]).__name__)
    print("  前 5 个:", json.dumps(bins[:5], ensure_ascii=False)[:300])
    print("  后 5 个:", json.dumps(bins[-5:], ensure_ascii=False)[:300])
    if isinstance(bins[0], list) and len(bins[0]) > 1:
        px = [b[0] for b in bins if isinstance(b, list)]
        print("  价格范围: %.1f ~ %.1f" % (min(px), max(px)))
