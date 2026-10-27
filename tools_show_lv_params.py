import json
import os

f = os.path.join(os.environ.get("TEMP", "."), "lvopenapi.json")
d = json.load(open(f, encoding="utf-8"))
paths = d.get("paths") or {}

want = [
    "/api/v1/liqmap/{symbol}",
    "/api/v1/liqheat/{symbol}",
    "/api/v1/heatmap/{exchange}/{symbol}",
    "/api/v1/liquidations",
    "/api/v1/liquidations/summary",
    "/api/v1/liquidations/cascades",
    "/api/v1/oi/regime",
]

for p in want:
    if p not in paths:
        continue
    op = paths[p].get("get") or {}
    print("=== %s   (%s)" % (p, op.get("summary", "")))
    desc = (op.get("description") or "").strip()
    if desc:
        print("  说明:", desc[:220].replace("\n", " "))
    for q in (op.get("parameters") or []):
        sch = q.get("schema") or {}
        print("  参数 %-18s in=%-6s req=%-5s default=%s  %s"
              % (q.get("name"), q.get("in"), q.get("required"),
                 sch.get("default"), (q.get("description") or "")[:70]))
    print()
