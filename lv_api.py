"""LiquidVision API 客户端 —— 彻底替代清算地图读图

用户 2026-10-28 提供 key 与文档后发现的官方 API：
  https://liquidvision.app/api/v1/openapi.json  （46 个端点）

核心端点（全部免费，200 OK）：
  GET /api/v1/liqmap/{symbol}?exchange=all&range=1d&model=v3
      -> 清算地图！400 个价格档位 + 每档 cum_long / cum_short（美元）
      -> 这就是 CoinGlass 那张图的 JSON 版，**不需要截图、不需要 VLM**
  GET /api/v1/liquidations/summary        -> 1h/4h/12h/24h 多空清算汇总
  GET /api/v1/liquidations?symbol=BTC     -> 原始清算事件（跨交易所）
  GET /api/v1/oi/regime                   -> 持仓状态（new_longs/short_covering/...）
  GET /api/v1/flows                       -> 资金流

需要 x402 微支付的端点（0.005 USDC/次，Arbitrum USDC）：
  /api/v1/sparks、/api/v1/heatmap/...

用法：
  python -m core.lv_api liqmap BTCUSDT
  python -m core.lv_api summary
"""

from __future__ import annotations

import json
import urllib.request

KEY = "Iv_wxwcAjfgNLFNp_RD_FfDRkyENKb9Zgy8hynrDWr3Zy4"
BASE = "https://liquidvision.app"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/147.0"


def get(path: str, timeout: float = 45.0) -> dict | list:
    """GET 请求（走系统代理）。认证用 Authorization: Bearer <key>，
    注意 X-API-Key 会返回 invalid API key。"""
    for attempt in range(2):
        try:
            op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            req = urllib.request.Request(
                BASE + path,
                headers={"Authorization": "Bearer " + KEY,
                         "Accept": "application/json",
                         "User-Agent": UA})
            return json.loads(op.open(req, timeout=timeout).read().decode())
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")
            if e.code in (401, 402):
                raise RuntimeError("HTTP %d: %s" % (e.code, body[:160]))
            if attempt == 1:
                raise RuntimeError("HTTP %d: %s" % (e.code, body[:160]))
        except Exception as e:
            if attempt == 1:
                raise RuntimeError(str(e)[:160])
    raise RuntimeError("unreachable")


def liqmap(symbol: str = "BTCUSDT", exchange: str = "all",
           rng: str = "1d", model: str = "v3") -> dict:
    """取清算地图原始数据（400 档 bins + cum_long + cum_short）"""
    return get("/api/v1/liqmap/%s?exchange=%s&range=%s&model=%s"
               % (symbol, exchange, rng, model))


def window_stats(m: dict, window_pct: float = 3.0) -> dict:
    """按 ±window_pct 窗口，从 liqmap 算出上下累计清算量（美元）

    这是替代 CoinGlass 读图的核心计算：
      · 上方 window_pct 处 bins 索引 -> cum_short[idx] = 上方累计空头清算
      · 下方 window_pct 处 bins 索引 -> cum_long[idx]  = 下方累计多头清算
      · R = 上方空头 / 下方多头
    """
    bins = m.get("bins") or []
    cum_long = m.get("cum_long") or []
    cum_short = m.get("cum_short") or []
    spot = float(m.get("current_price") or 0)
    if not bins or not cum_long or not cum_short or not spot:
        return {"ok": False, "why": "liqmap 数据不完整"}

    up_target = spot * (1 + window_pct / 100.0)
    dn_target = spot * (1 - window_pct / 100.0)

    def find_idx(target: float) -> int:
        best, bi = 1e18, 0
        for i, b in enumerate(bins):
            dd = abs(b - target)
            if dd < best:
                best, bi = dd, i
        return bi

    iu = find_idx(up_target)
    idn = find_idx(dn_target)

    up_short = float(cum_short[iu] or 0)
    dn_long = float(cum_long[idn] or 0)
    r = (up_short / dn_long) if dn_long > 0 else None

    return {
        "ok": bool(up_short > 0 and dn_long > 0),
        "spot": round(spot, 1),
        "up_px": round(bins[iu], 1), "up_short_usd": round(up_short),
        "dn_px": round(bins[idn], 1), "dn_long_usd": round(dn_long),
        "up_pct": round((bins[iu] - spot) / spot * 100, 2),
        "dn_pct": round((spot - bins[idn]) / spot * 100, 2),
        "r": round(r, 3) if r else None,
        "window_pct": window_pct,
        "exchange": m.get("exchange"), "model": m.get("model"),
        "age_s": m.get("age_s"), "ts": m.get("ts"),
        "bins": len(bins),
        "source": "LiquidVision /api/v1/liqmap",
    }


def summary(minutes: int = 1440) -> dict:
    return get("/api/v1/liquidations/summary?minutes=%d" % minutes)


def oi_regime(minutes: int = 240) -> dict:
    return get("/api/v1/oi/regime?minutes=%d" % minutes)


def top_bins(m: dict, spot: float, window_pct: float = 5.0, n: int = 8) -> list:
    """返回 ±window_pct 内清算最密集的 n 个价位（用 |Δcum| 作为该档新增量）"""
    bins = m.get("bins") or []
    cum_long = m.get("cum_long") or []
    cum_short = m.get("cum_short") or []
    if not bins:
        return []
    up = spot * (1 + window_pct / 100.0)
    dn = spot * (1 - window_pct / 100.0)
    out = []
    for i in range(1, len(bins)):
        if not (dn <= bins[i] <= up):
            continue
        dl = (cum_long[i] or 0) - (cum_long[i - 1] or 0)
        ds = (cum_short[i] or 0) - (cum_short[i - 1] or 0)
        out.append({"px": round(bins[i], 1),
                    "long_add": round(dl), "short_add": round(ds),
                    "total": round(abs(dl) + abs(ds))})
    out.sort(key=lambda x: -x["total"])
    return out[:n]


if __name__ == "__main__":
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else "liqmap"
    if cmd == "liqmap":
        sym = sys.argv[2] if len(sys.argv) > 2 else "BTCUSDT"
        m = liqmap(sym)
        print("age_s=%s bins=%d spot=%s"
              % (m.get("age_s"), len(m.get("bins") or []), m.get("current_price")))
        for pct in (1.0, 2.0, 3.0, 5.0):
            print("  ±%.0f%%:" % pct, json.dumps(window_stats(m, pct), ensure_ascii=False))
        print("  密集价位:", json.dumps(
            top_bins(m, float(m.get("current_price") or 0)), ensure_ascii=False))
    elif cmd == "summary":
        print(json.dumps(summary(), ensure_ascii=False)[:800])
    elif cmd == "regime":
        print(json.dumps(oi_regime(), ensure_ascii=False)[:800])
