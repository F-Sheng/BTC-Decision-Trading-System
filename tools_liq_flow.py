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
WINDOW_MIN = 60
BURST_COUNT_5M = 6          # 最近 5 分钟强平条目数
BURST_VOL_5M = 200         # 最近 5 分钟爆仓张数（张，1 张 = 0.01 BTC）
FRESH_SEC = 30               # 最新强平在 60 秒内 = 正在发生


CT_VAL = 0.01  # BTC-USDT-SWAP 1 张 = 0.01 BTC


def fetch():
    """抓真实强平流（LiquidVision，覆盖 7 家交易所）

    2026-10-28 换源原因：OKX /public/liquidation-orders 数据滞后 64 分钟，
    导致「60分钟 0 条」被误判成「市场平静」。LV 实测最新 156 秒前。

    返回统一成与旧代码兼容的结构：
      ts(ms) / side(buy|sell) / bkPx / sz(张) / posSide
      · LV side="long"（多头被爆）-> 转成 side="sell"（卖单把多头打爆）
      · LV side="short"（空头被爆）-> 转成 side="buy"
    """
    from core import lv_api

    rows = lv_api.get("/api/v1/liquidations?symbol=BTCUSDT&minutes=%d&limit=500"
                      % WINDOW_MIN, timeout=45)
    out = []
    for r in (rows or []):
        try:
            qty = float(r.get("qty") or 0)
            side_lv = (r.get("side") or "").lower()
            out.append({
                "ts": str(r.get("ts")),
                "side": "sell" if side_lv == "long" else "buy",
                "bkPx": str(r.get("price")),
                "sz": str(round(qty / CT_VAL, 4)),   # 币 -> 张
                "posSide": "long" if side_lv == "long" else "short",
                "exchange": r.get("exchange"),
            })
        except Exception:
            continue
    return out

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
        print("=== 真实强平流（LiquidVision · 7家交易所）===")
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
