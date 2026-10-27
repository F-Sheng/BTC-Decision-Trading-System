"""CoinGlass vs LiquidVision liqmap 一致性对比

CoinGlass 实测（2026-10-28 02:16，Binance BTC/USDT，1 天）：
  现价          80,250
  +1.36% (81338) -> 累计空头清算 $397.71M

现在用 LiquidVision 在同一价位算累计空头清算，看量级是否一致。
同时对比 ±3% 窗口的量级差异。
"""

import json
import sys

sys.path.insert(0, r"D:\DP workspace\btc_system")
from core import lv_api

CG = {
    "spot": 80250.0,
    "px_1p36": 81338.0,
    "short_at_1p36": 397.71e6,      # CoinGlass 实测
    "liq24h": 137.07e6,             # CoinGlass 顶部 24h Liquidation
}

print("=" * 66)
print(" CoinGlass vs LiquidVision liqmap 对比")
print("=" * 66)
print("CoinGlass 实测（Binance 单家，1天）：")
print("  现价 %.0f   +1.36%%(%.0f) 累计空头 $%.2fM"
      % (CG["spot"], CG["px_1p36"], CG["short_at_1p36"] / 1e6))
print("  24h 清算总计 $%.2fM" % (CG["liq24h"] / 1e6))
print()

# LV: all exchanges
for exch in ("all", "binance"):
    try:
        m = lv_api.liqmap("BTCUSDT", exchange=exch)
    except Exception as e:
        print("LV(%s) 获取失败: %s" % (exch, str(e)[:80]))
        continue
    spot = float(m.get("current_price") or 0)
    bins = m.get("bins") or []
    cs = m.get("cum_short") or []
    cl = m.get("cum_long") or []
    if not bins or not spot:
        print("LV(%s) 数据为空" % exch)
        continue

    def at_pct(pct):
        tgt = spot * (1 + pct / 100.0)
        bi, bd = 0, 1e18
        for i, b in enumerate(bins):
            d = abs(b - tgt)
            if d < bd:
                bd, bi = d, i
        return bins[bi], float(cs[bi] or 0), float(cl[bi] or 0)

    px1, sh1, lo1 = at_pct(1.356)
    px3u, sh3u, _ = at_pct(3.0)
    px3d, _, lo3d = at_pct(-3.0)

    print("LV exchange=%s （model=%s, age=%ss, bins=%d）:"
          % (exch, m.get("model"), m.get("age_s"), len(bins)))
    print("  LV 现价 %.0f" % spot)
    print("  +1.356%%(%.0f) 累计空头 $%.2fM   ← 对比 CG 的 $%.2fM   差异 %+.1f%%"
          % (px1, sh1 / 1e6, CG["short_at_1p36"] / 1e6,
             (sh1 / CG["short_at_1p36"] - 1) * 100 if CG["short_at_1p36"] else 0))
    print("  +3.0%%(%.0f) 累计空头 $%.2fM" % (px3u, sh3u / 1e6))
    print("  -3.0%%(%.0f) 累计多头 $%.2fM" % (px3d, lo3d / 1e6))
    print("  R(±3%%) = %.2f" % (sh3u / lo3d if lo3d else 0))
    print()

# LV 24h 清算汇总 vs CG 24h
try:
    s = lv_api.summary()
    r24 = (s.get("rekt") or {}).get("24h") or {}
    print("LV /liquidations/summary 24h 清算总计 $%.2fM   ← 对比 CG 的 $%.2fM  差异 %+.1f%%"
          % ((r24.get("total") or 0) / 1e6, CG["liq24h"] / 1e6,
             ((r24.get("total") or 0) / CG["liq24h"] - 1) * 100))
    print("  （LV 覆盖 7+ 家交易所，CG 顶部数字是全市场）")
except Exception as e:
    print("LV summary 失败:", str(e)[:80])
