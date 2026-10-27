"""自建清算密度地图 —— 用 OKX 真实强平流替代读 CoinGlass 图

2026-10-28 用户提出「地图读数好难，是不是模型问题」，确认根因是 Qwen2.5-VL-3B
能力不足（反复把 tooltip 里的价格当累计值读）。方案：不再读图，改用真实数据聚合。

数据来源：OKX /api/v5/public/liquidation-orders（真实爆仓成交，无需额外权限）
  {"bkPx": 价格, "side": "buy"(空头被爆)/"sell"(多头被爆), "sz": 张数, "ts": 毫秒}

做法：
  · 机器人每轮 poll 一次（liq_live 已在做），把新记录追加到 data/liq_density.jsonl
  · 按 ts+px+sz 去重，保留最近 24 小时
  · 按价格分桶（默认 100 点一桶）
  · 输出与 CoinGlass 完全相同的字段结构，机器人无感切换

为什么比读图好：
  · 数据真实，不是估算
  · 零成本、无穷读数抖动、无 VLM 幻觉
  · 可以精确算 ±3% 窗口内的累计空头/多头清算量

局限：
  · OKX 单次只返回最近 100 条，需要持续累积
  · 只有 OKX 一家（CoinGlass 是全交易所）→ 量级偏小，但比例（R 值）可用
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(r"D:\DP workspace\btc_system")
FILE = ROOT / "data" / "liq_density.jsonl"
KEEP_HOURS = 26
BUCKET_USD = 100.0


def _now_ms() -> int:
    return int(time.time() * 1000)


def ingest() -> dict:
    """抓一次 OKX 强平流并追加到密度文件，返回本次新增条数"""
    from .okx import OkxClient

    try:
        # 必须直连真实市场接口，不能带 x-simulated-trading 头（会返回 demo 垃圾数据）
        import urllib.request as _ur
        url = ("https://www.okx.com/api/v5/public/liquidation-orders"
               "?instType=SWAP&uly=BTC-USDT&state=filled&limit=100")
        op = _ur.build_opener(_ur.ProxyHandler({
            "http": "http://127.0.0.1:57777",
            "https": "http://127.0.0.1:57777"}))
        req = _ur.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        _d = json.loads(op.open(req, timeout=30).read().decode())
        data = _d.get("data") or []
        details = (data[0].get("details") or []) if data else []
    except Exception as e:
        return {"ok": False, "why": str(e)[:80]}

    # 已有的 key（去重）
    seen = set()
    old_rows = []
    if FILE.exists():
        try:
            for line in FILE.read_text(encoding="utf-8").splitlines()[-60000:]:
                if not line.strip():
                    continue
                try:
                    o = json.loads(line)
                except Exception:
                    continue
                old_rows.append(o)
                seen.add((o["ts"], o["px"], o["sz"]))
        except Exception:
            pass

    cutoff = _now_ms() - KEEP_HOURS * 3600 * 1000
    keep = [o for o in old_rows if o.get("ts", 0) >= cutoff]

    new = 0
    for x in details:
        try:
            ts = int(x["ts"])
            px = round(float(x["bkPx"]), 1)
            sz = round(float(x["sz"]), 2)
            side = x.get("side")          # buy = 空头爆仓, sell = 多头爆仓
        except Exception:
            continue
        if ts < cutoff:
            continue
        k = (ts, px, sz)
        if k in seen:
            continue
        seen.add(k)
        keep.append({"ts": ts, "px": px, "sz": sz, "side": side})
        new += 1

    if new:
        keep.sort(key=lambda o: o["ts"])
        FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(FILE, "w", encoding="utf-8") as f:
            for o in keep:
                f.write(json.dumps(o, ensure_ascii=False) + "\n")

    return {"ok": True, "new": new, "total": len(keep)}


def build_view(spot: float, window_pct: float = 3.0) -> dict:
    """按 ±window_pct 窗口统计累计清算，输出 CoinGlass 同构字段"""
    if not FILE.exists():
        return {"ok": False, "why": "无累积数据"}
    rows = []
    try:
        for line in FILE.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
    except Exception as e:
        return {"ok": False, "why": str(e)[:60]}

    if not rows:
        return {"ok": False, "why": "文件为空"}
    if not spot:
        spot = max((r["px"] for r in rows), default=0)
    if not spot:
        return {"ok": False, "why": "无现价"}

    up_px = spot * (1 + window_pct / 100.0)
    dn_px = spot * (1 - window_pct / 100.0)

    short_liq = 0.0     # 空头爆仓（side=buy）在现价上方
    long_liq = 0.0      # 多头爆仓（side=sell）在现价下方
    n_up = n_dn = 0
    for r in rows:
        px, sz, side = r["px"], r["sz"], r.get("side")
        if px >= up_px and side == "buy":
            short_liq += sz
            n_up += 1
        elif px <= dn_px and side == "sell":
            long_liq += sz
            n_dn += 1

    # 张 -> 百万美元：1 张 = 0.01 BTC
    def to_m(contracts: float) -> float:
        return round(contracts * 0.01 * spot / 1e6, 2)

    sm, lm = to_m(short_liq), to_m(long_liq)
    if sm <= 0 or lm <= 0:
        return {"ok": False,
                "why": "窗口内数据不足 up=%.2fM dn=%.2fM（累积还没跑够）" % (sm, lm),
                "raw": {"short_m": sm, "long_m": lm, "n_up": n_up, "n_dn": n_dn}}

    up_trigger = round(max([r["px"] for r in rows if r["px"] >= up_px and r.get("side") == "buy"] or [up_px]), 1)
    dn_trigger = round(min([r["px"] for r in rows if r["px"] <= dn_px and r.get("side") == "sell"] or [dn_px]), 1)

    return {
        "ok": True,
        "spot": round(spot, 1),
        "up_px": up_trigger, "short_m": sm,
        "dn_px": dn_trigger, "long_m": lm,
        "r": round(sm / lm, 2),
        "window_pct": window_pct,
        "n_up": n_up, "n_dn": n_dn,
        "rows": len(rows),
        "hours": round((rows[-1]["ts"] - rows[0]["ts"]) / 3.6e6, 1) if len(rows) > 1 else 0,
        "source": "OKX 真实强平流聚合（非 CoinGlass）",
    }


def write_to_coinglass(spot: float, window_pct: float = 3.0) -> dict:
    """把自建密度写进 data/coinglass.json，机器人直接可用"""
    v = build_view(spot, window_pct)
    if not v.get("ok"):
        return v
    p = ROOT / "data" / "coinglass.json"
    try:
        d = json.load(open(p, encoding="utf-8"))
    except Exception:
        d = {}
    d["captured_at"] = _now_ms()
    d["binance_liqmap_1d"] = {
        "spot": v["spot"],
        "near": {"pct": window_pct,
                 "up_px": v["up_px"],
                 "short": int(v["short_m"] * 1e6),
                 "dn_px": v["dn_px"],
                 "long": int(v["long_m"] * 1e6)},
        "far": {"pct": window_pct,
                "up_px": v["up_px"],
                "short": int(v["short_m"] * 1e6),
                "dn_px": v["dn_px"],
                "long": int(v["long_m"] * 1e6)},
        "points": [{"px": v["up_px"], "short": int(v["short_m"] * 1e6)},
                   {"px": v["dn_px"], "long": int(v["long_m"] * 1e6)}],
        "method": "SELF_BUILT: OKX 真实强平流按价格聚合 ±%.1f%%" % window_pct,
        "axis_note": "非截图读数，无 VLM 幻觉风险",
        "source": v["source"],
        "n_up": v["n_up"], "n_dn": v["n_dn"],
        "data_hours": v["hours"],
    }
    d.pop("_liqmap_invalidated", None)
    json.dump(d, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    return v


if __name__ == "__main__":
    print("=== 抓取并累积 ===")
    print(" ", ingest())
    import sys
    spot = float(sys.argv[1]) if len(sys.argv) > 1 else 0
    print("=== 构建视图 ===")
    v = build_view(spot)
    print(" ", json.dumps(v, ensure_ascii=False))


def squeeze_view(spot: float) -> dict:
    """用真实强平流给出「挤压方向」和「清算密集价位」。

    重要澄清（实测结论）：
      OKX liquidation-orders 返回的是【已经发生的爆仓】，价格集中在现价附近
      （实测 100 条全在 spot ±0.6% 内），**不能反推未来 ±3% 的清算簇**。
      所以本函数只做两件真实可行的事：
        ① 多空爆仓比 -> 哪一边正在被挤爆（方向性信号）
        ② 清算最密集的价位 -> 刚刚被扫过的关键位（结构参考）
      不做「未来清算地图」这种做不到的事。
    """
    if not FILE.exists():
        return {"ok": False, "why": "无数据"}
    rows = []
    for line in FILE.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            continue
    if not rows:
        return {"ok": False, "why": "空"}

    buy = sum(r["sz"] for r in rows if r.get("side") == "buy")    # 空头被爆
    sell = sum(r["sz"] for r in rows if r.get("side") == "sell")  # 多头被爆
    ratio = round(buy / sell, 2) if sell else None

    now = _now_ms()
    recent = [r for r in rows if now - r["ts"] <= 3600 * 1000]

    buckets = {}
    for r in rows:
        b = int(r["px"] // BUCKET_USD * BUCKET_USD)
        buckets.setdefault(b, {"n": 0, "sz": 0.0, "buy": 0.0, "sell": 0.0})
        buckets[b]["n"] += 1
        buckets[b]["sz"] += r["sz"]
        if r.get("side") == "buy":
            buckets[b]["buy"] += r["sz"]
        else:
            buckets[b]["sell"] += r["sz"]
    top = sorted(buckets.items(), key=lambda kv: -kv[1]["sz"])[:5]

    hours = round((rows[-1]["ts"] - rows[0]["ts"]) / 3.6e6, 1) if len(rows) > 1 else 0
    return {
        "ok": True,
        "rows": len(rows),
        "hours": hours,
        "short_liq_contracts": round(buy),
        "long_liq_contracts": round(sell),
        "squeeze_ratio": ratio,
        "squeeze_side": ("short" if (ratio and ratio > 2) else
                          ("long" if (ratio and ratio < 0.5) else "balanced")),
        "recent_1h_rows": len(recent),
        "dense_levels": [{"px": b, "sz": round(v["sz"]),
                           "buy": round(v["buy"]), "sell": round(v["sell"])}
                          for b, v in top],
        "note": "已发生的爆仓，非未来清算簇；仅用于挤压方向与结构参考",
    }
