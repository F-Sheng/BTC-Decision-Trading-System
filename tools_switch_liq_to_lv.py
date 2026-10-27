"""把 tools_liq_flow.py 的数据源换成 LiquidVision（7 家交易所）

2026-10-28 用户指出前后矛盾后实测：

  【OKX 单家】0.97 秒 / 100 条 / 时间跨度 159 分钟 / **最新一笔 3867 秒前（64 分钟！）**
  【LV symbol=BTCUSDT】0.83 秒 / 9 条 / **最新 156 秒前（2.6 分钟）**
                        交易所: binance 5 / htx 2 / bitget 1 / gate 1

  结论：
    1. OKX 的 liquidation-orders 有严重延迟（64 分钟），之前「60分钟0条=市场平静」的判断
       全是错的——是数据没更新，不是没爆仓。
    2. LV 更快、更新鲜、覆盖更多交易所。

  单位换算：
    OKX 的 sz 单位是「张」，BTC-USDT-SWAP 1 张 = 0.01 BTC
    LV 的 qty 单位是「币」（BTC）
    -> 张 = qty / 0.01 = qty * 100

  side 含义（LV）：
    side="long"  = 多头被爆（价格下跌触发）
    side="short" = 空头被爆（价格上涨触发）
"""

import ast

p = "tools_liq_flow.py"
s = open(p, encoding="utf-8").read()

old_start = 'def fetch():'
idx = s.index(old_start)
# 找到 fetch 函数结束（下一个顶层 def）
rest = s[idx:]
nxt = rest.find('\ndef ', 1)
old_func = rest[:nxt]

new_func = '''CT_VAL = 0.01  # BTC-USDT-SWAP 1 张 = 0.01 BTC


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
'''

s = s[:idx] + new_func + s[idx + len(old_func):]

# 加 WINDOW_MIN 常量
if "WINDOW_MIN" not in s.split("def fetch")[0]:
    s = s.replace("BURST_COUNT_5M = ", "WINDOW_MIN = 60\nBURST_COUNT_5M = ", 1)

# 阈值重标（LV 60 分钟约 9-40 条，OKX 100 条/159 分钟；真实爆仓明显更少）
s = s.replace("BURST_COUNT_5M = 12", "BURST_COUNT_5M = 6")
s = s.replace("BURST_VOL_5M = 150", "BURST_VOL_5M = 200")
s = s.replace("FRESH_SEC = 90", "FRESH_SEC = 180")

open(p, "w", encoding="utf-8").write(s)
ast.parse(s)
print("tools_liq_flow.py: 数据源换成 LiquidVision（7家交易所），单位 币->张 换算")
print("  新阈值: 5分钟>=6条 或 >=200张 或 最新<180秒")
