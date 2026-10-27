"""OKX 真实强平流监控 —— 替代估算，直接看爆仓成交

背景（用户 2026-10-27 提问）：
  「清算地图是自己算的，在什么基础上算的？不读图能拿到最新数据吗？
    是不是可以直接读 OKX 的强平流，强平流暴涨就切模式？」

  确认：原 L2[PROXY] 是估算（基于 OI + LEVERAGES 假设），不是真实爆仓。
  OKX 有真实强平接口 /api/v5/public/liquidation-orders（无需额外权限）：
    {"details": [{"bkPx": 价格, "posSide": "short/long", "side": "buy/sell",
                  "sz": 张数, "ts": 毫秒}]}
  只返回最近 100 条，正常行情覆盖约 2.5 小时。
  **强平潮来时 100 条会被快速填满 -> 最新 ts 很近 + 5 分钟内条目暴涨**，
  这个特征本身就是极可靠的触发信号。

用法：
  python tools_liq_flow.py            # 打印当前强平流状态
  python tools_liq_flow.py --json     # 输出 JSON（供 should_fetch 调用）
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(r"D:\DP workspace\btc_system")
sys.path.insert(0, str(ROOT))

STATE = ROOT / "data" / "liq_flow.json"

# 触发阈值
BURST_COUNT_5M = 12          # 最近 5 分钟强平条目数
BURST_VOL_5M = 150         # 最近 5 分钟爆仓张数（张，1 张 = 0.01 BTC）
FRESH_SEC = 90               # 最新强平在 60 秒内 = 正在发生


def fetch():
    """抓真实市场强平流（必须不带 x-simulated-trading 头）

    2026-10-28 用户质疑「从来没有 80,120 这个数据」后查出根因：
      OkxClient(simulated=True) 会带 x-simulated-trading: 1，
      而 /public/liquidation-orders 在模拟盘返回的是 demo 垃圾数据：
        带 simulated 头 -> 单笔最大 10,000 张、合计 300,292 张、价格含 80,120
        不带          -> 单笔最大 91.93 张、合计 540 张、价格 80,366~80,789
      虚高约 556 倍。LiquidVision 跨交易所数据也证实 80,050~80,200 区间没有清算。
    """
    import json as _json
    import urllib.request as _ur
    url = ("https://www.okx.com/api/v5/public/liquidation-orders"
           "?instType=SWAP&uly=BTC-USDT&state=filled&limit=100")
    op = _ur.build_opener(_ur.ProxyHandler({
        "http": "http://127.0.0.1:57777",
        "https": "http://127.0.0.1:57777"}))
    req = _ur.Request(url, headers={"User-Agent": "Mozilla/5.0",
                                    "Accept": "application/json"})
    d = _json.loads(op.open(req, timeout=30).read().decode())
    data = d.get("data") or []
    if not data:
        return []
    return data[0].get("details") or []


def analyze(details: list) -> dict:
    now_ms = time.time() * 1000
    if not details:
        return {"ok": False, "why": "no details"}

    rows = []
    for x in details:
        try:
            rows.append({
                "ts": int(x["ts"]),
                "px": float(x["bkPx"]),
                "sz": float(x["sz"]),
                "side": x.get("side"),        # buy = 空头爆仓, sell = 多头爆仓
                "pos": x.get("posSide"),
            })
        except Exception:
            continue
    if not rows:
        return {"ok": False, "why": "cannot parse"}

    rows.sort(key=lambda r: -r["ts"])
    newest = rows[0]["ts"]
    age_s = (now_ms - newest) / 1000

    def window(minutes):
        cut = now_ms - minutes * 60 * 1000
        return [r for r in rows if r["ts"] >= cut]

    w5 = window(5)
    w15 = window(15)
    w60 = window(60)

    def agg(ws):
        buy = sum(r["sz"] for r in ws if r["side"] == "buy")    # 空头爆仓
        sell = sum(r["sz"] for r in ws if r["side"] == "sell")  # 多头爆仓
        return {"n": len(ws), "short_liq": round(buy, 1),
                "long_liq": round(sell, 1),
                "total": round(buy + sell, 1)}

    a5, a15, a60 = agg(w5), agg(w15), agg(w60)

    burst = False
    why = []
    if a5["n"] >= BURST_COUNT_5M:
        burst = True
        why.append("5分钟内 %d 条强平（阈值 %d）" % (a5["n"], BURST_COUNT_5M))
    if a5["total"] >= BURST_VOL_5M:
        burst = True
        why.append("5分钟内爆仓 %.0f 张（阈值 %d）" % (a5["total"], BURST_VOL_5M))
    if age_s <= FRESH_SEC:
        burst = True
        why.append("最新强平仅 %.0f 秒前（阈值 %ds）" % (age_s, FRESH_SEC))

    return {
        "ok": True,
        "age_sec": round(age_s, 1),
        "newest_px": rows[0]["px"],
        "span_hours": round((rows[0]["ts"] - rows[-1]["ts"]) / 3.6e6, 2),
        "w5": a5, "w15": a15, "w60": a60,
        "burst": burst,
        "why": why,
        # 累计多空爆仓比（越大说明空头被打得越多 = 上涨动能强）
        "short_long_ratio_60m": round(
            a60["short_liq"] / a60["long_liq"], 2) if a60["long_liq"] else None,
    }


def main():
    as_json = "--json" in sys.argv
    try:
        details = fetch()
        r = analyze(details)
    except Exception as e:
        r = {"ok": False, "why": str(e)}

    if r.get("ok"):
        try:
            STATE.write_text(json.dumps(
                {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), **r},
                ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    if as_json:
        print(json.dumps(r, ensure_ascii=False, indent=2))
    else:
        if not r.get("ok"):
            print("强平流获取失败:", r.get("why"))
            return 1
        print("=== OKX 真实强平流 ===")
        print("  最新强平: %.0f 秒前 @ %.1f" % (r["age_sec"], r["newest_px"]))
        print("  100条覆盖: %.2f 小时" % r["span_hours"])
        for k, label in (("w5", "5分钟"), ("w15", "15分钟"), ("w60", "60分钟")):
            a = r[k]
            print("  %-6s %2d 条 | 空头爆仓 %8.0f 张 | 多头爆仓 %8.0f 张 | 合计 %8.0f 张"
                  % (label, a["n"], a["short_liq"], a["long_liq"], a["total"]))
        print("  60分钟 空/多爆仓比: %s" % r["short_long_ratio_60m"])
        print("  强平潮: %s %s" % ("是 ⚠" if r["burst"] else "否",
                                 "; ".join(r["why"])))
    return 0 if r.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
