"""把 LiquidVision liqmap 写入机器人数据通道 + 全量数据自检

用法：
  python tools_lv_map.py          # 取 liqmap 并写入 data/coinglass.json
  python tools_lv_map.py --check  # 只做数据自检，不写
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(r"D:\DP workspace\btc_system")
sys.path.insert(0, str(ROOT))

from core import lv_api  # noqa: E402

WINDOW = 3.0


def write_snapshot(st: dict):
    """写入 coinglass.json 的 binance_liqmap_1d（机器人直接读这个结构）"""
    p = ROOT / "data" / "coinglass.json"
    try:
        d = json.load(open(p, encoding="utf-8"))
    except Exception:
        d = {}
    d["captured_at"] = int(time.time() * 1000)
    d["binance_liqmap_1d"] = {
        "spot": st["spot"],
        "near": {"pct": WINDOW,
                 "up_px": st["up_px"],
                 "short": int(st["up_short_usd"]),
                 "dn_px": st["dn_px"],
                 "long": int(st["dn_long_usd"])},
        "far": {"pct": 5.0,
                "up_px": st["up_px"],
                "short": int(st["up_short_usd"]),
                "dn_px": st["dn_px"],
                "long": int(st["dn_long_usd"])},
        "points": [{"px": st["up_px"], "short": int(st["up_short_usd"])},
                   {"px": st["dn_px"], "long": int(st["dn_long_usd"])}],
        "method": "LiquidVision /api/v1/liqmap (model v3, all exchanges, 1d)",
        "axis_note": "API JSON 数据，非截图读数，无 VLM 幻觉风险",
        "r": st["r"], "r_window": "%.1f%%" % WINDOW,
        "age_s": st["age_s"], "bins": st["bins"],
        "source": st["source"],
    }
    d.pop("_liqmap_invalidated", None)
    json.dump(d, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    # 追加 market_snapshot
    try:
        from core import journal
        js = journal.load_state()
        pos = None
        for sd in ("short", "long"):
            if sd in js.open_sides():
                q = js.get_pos(sd)
                pos = {"side": q.side, "entry": q.entry, "size": q.size,
                       "sl": q.stop,
                       "tp1": q.targets[0] if q.targets else 0,
                       "upl_pct": round((q.entry - st["spot"]) / q.entry * 100, 2)
                       if sd == "short" else
                       round((st["spot"] - q.entry) / q.entry * 100, 2)}
        rec = {"ts": datetime.now().strftime("%Y-%m-%dT%H:%M"),
               "px": st["spot"], "r": st["r"], "r_window": "%.1f%%" % WINDOW,
               "r_note": "+%.2f%% / -%.2f%%" % (st["up_pct"], st["dn_pct"]),
               "up_px": st["up_px"], "up_short_m": round(st["up_short_usd"] / 1e6, 2),
               "dn_px": st["dn_px"], "dn_long_m": round(st["dn_long_usd"] / 1e6, 2),
               "source": "LiquidVision liqmap", "pos": pos}
        with open(ROOT / "data" / "market_snapshot.jsonl", "a",
                  encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as e:
        print("  snapshot 追加失败:", e)


def data_check() -> int:
    """全量数据自检 —— 用户 2026-10-28 要求，防止再次出现假数据事故"""
    print("=" * 64)
    print(" 全量数据源自检   %s" % datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    print("=" * 64)
    bad = 0

    def ok(name, cond, detail=""):
        nonlocal bad
        mark = "OK  " if cond else "FAIL"
        if not cond:
            bad += 1
        print("  [%s] %-26s %s" % (mark, name, detail))

    # 1. OKX 真实市场（必须不带 simulated 头）
    try:
        import urllib.request
        op = urllib.request.build_opener(urllib.request.ProxyHandler({
            "http": "http://127.0.0.1:57777",
            "https": "http://127.0.0.1:57777"}))
        req = urllib.request.Request(
            "https://www.okx.com/api/v5/market/ticker?instId=BTC-USDT-SWAP",
            headers={"User-Agent": "Mozilla/5.0"})
        d = json.loads(op.open(req, timeout=25).read().decode())
        px = float((d.get("data") or [{}])[0].get("last") or 0)
        ok("OKX 现价", 60000 < px < 200000, "%.1f" % px)
    except Exception as e:
        ok("OKX 现价", False, str(e)[:60])

    # 2. OKX 强平流（真实市场，量级检查）
    try:
        import urllib.request
        op = urllib.request.build_opener(urllib.request.ProxyHandler({
            "http": "http://127.0.0.1:57777",
            "https": "http://127.0.0.1:57777"}))
        req = urllib.request.Request(
            "https://www.okx.com/api/v5/public/liquidation-orders"
            "?instType=SWAP&uly=BTC-USDT&state=filled&limit=100",
            headers={"User-Agent": "Mozilla/5.0"})
        d = json.loads(op.open(req, timeout=25).read().decode())
        det = ((d.get("data") or [{}])[0].get("details") or [])
        tot = sum(float(x["sz"]) for x in det) if det else 0
        mx = max((float(x["sz"]) for x in det), default=0)
        # 真实市场单笔最大不会超过几百张；上万张说明又用错接口了
        ok("OKX 强平流量级", 0 < tot < 5000 and mx < 500,
           "%d条 合计%.0f张 单笔最大%.1f张" % (len(det), tot, mx))
    except Exception as e:
        ok("OKX 强平流量级", False, str(e)[:60])

    # 3. LiquidVision liqmap
    st = None
    try:
        m = lv_api.liqmap("BTCUSDT")
        st = lv_api.window_stats(m, WINDOW)
        ok("LV liqmap", st.get("ok"),
           "R=%.2f age=%.0fs bins=%d" % (st.get("r") or 0,
                                         st.get("age_s") or 0, st.get("bins") or 0))
        ok("LV liqmap 新鲜度", (st.get("age_s") or 999) < 600,
           "%.0f 秒前" % (st.get("age_s") or -1))
        # 注：旧版的「窗口对称」检查来自截图时代（手动 hover 会偏到 +3.26%/-4.48%）。
        # 现在 API 直接按 ±3% 索引取 bin，天然对称，改成检查「实际窗口是否落在 2.7~3.3%」
        _upw, _dnw = st.get("up_pct") or 0, st.get("dn_pct") or 0
        ok("LV 窗口命中", 2.7 <= _upw <= 3.3 and 2.7 <= _dnw <= 3.3,
           "上 %.2f%% / 下 %.2f%%（请求 ±3%%）" % (_upw, _dnw))
        ok("LV R 合理", 0.2 <= (st.get("r") or 0) <= 8.0, "R=%.2f" % (st.get("r") or 0))
    except Exception as e:
        ok("LV liqmap", False, str(e)[:70])

    # 3b. coinglass.json 里的 R 是否新鲜（用户 2026-10-28 指出我引用过 2 小时前的旧 R）
    try:
        p = ROOT / "data" / "coinglass.json"
        d = json.load(open(p, encoding="utf-8"))
        b = d.get("binance_liqmap_1d") or {}
        cap = d.get("captured_at") or 0
        age = (time.time() * 1000 - cap) / 1000 if cap else 99999
        ok("coinglass R 新鲜度", age < 900 and b.get("r") is not None,
           "R=%s age=%.0fs（阈值 900s）" % (b.get("r"), age))
    except Exception as e:
        ok("coinglass R 新鲜度", False, str(e)[:60])

    # 4. LiquidVision 清算汇总
    try:
        s = lv_api.summary()
        r1 = (s.get("rekt") or {}).get("1h") or {}
        r24 = (s.get("rekt") or {}).get("24h") or {}
        ok("LV 清算汇总", 0 < (r24.get("total") or 0) < 5e9,
           "1h $%.1fM / 24h $%.1fM" % ((r1.get("total") or 0) / 1e6,
                                       (r24.get("total") or 0) / 1e6))
    except Exception as e:
        ok("LV 清算汇总", False, str(e)[:70])

    # 5. 金十新闻
    try:
        p = ROOT / "data" / "news_log.jsonl"
        n = 0
        if p.exists():
            lines = p.read_text(encoding="utf-8").splitlines()[-300:]
            n = len([x for x in lines if x.strip()])
        ok("金十 news_log", n > 0, "%d 条" % n)
    except Exception as e:
        ok("金十 news_log", False, str(e)[:60])

    # 6. Odaily 日志
    try:
        p = ROOT / "data" / "odaily_log.jsonl"
        n = 0
        if p.exists():
            lines = p.read_text(encoding="utf-8").splitlines()[-300:]
            n = len([x for x in lines if x.strip()])
        ok("Odaily log", n > 0, "%d 条" % n)
    except Exception as e:
        ok("Odaily log", False, str(e)[:60])

    # 7. 机器人日志新鲜度
    try:
        logs = sorted((ROOT / "logs").glob("btc-*.log"))
        lag = (time.time() - logs[-1].stat().st_mtime) if logs else 9999
        ok("机器人日志", lag < 300, "%.0f 秒前" % lag)
    except Exception as e:
        ok("机器人日志", False, str(e)[:60])

    # 8. 持仓与止损单
    try:
        from core import journal
        from core.okx import OkxClient
        js = journal.load_state()
        sides = js.open_sides()
        c = OkxClient(simulated=True)
        r = c._request("GET", "/api/v5/trade/orders-algo-pending",
                       {"instType": "SWAP", "instId": "BTC-USDT-SWAP",
                        "ordType": "conditional"})
        nsl = len(r.get("data") or [])
        ok("止损单在位", (not sides) or nsl >= len(sides),
           "持仓%d边 止损单%d个" % (len(sides), nsl))
    except Exception as e:
        ok("止损单在位", False, str(e)[:60])

    print("=" * 64)
    print(" 结果: %s" % ("全部通过" if bad == 0 else "%d 项失败" % bad))
    print("=" * 64)
    return bad


def main():
    if "--check" in sys.argv:
        return 0 if data_check() == 0 else 1
    try:
        m = lv_api.liqmap("BTCUSDT")
        st = lv_api.window_stats(m, WINDOW)
    except Exception as e:
        print("LiquidVision liqmap 失败:", e)
        return 1
    if not st.get("ok"):
        print("liqmap 数据不可用:", st)
        return 1
    write_snapshot(st)
    print("=== LiquidVision 清算地图（API，无读图）===")
    print("  现价 %.1f   age %.0f 秒   bins %d"
          % (st["spot"], st["age_s"], st["bins"]))
    print("  +%.2f%% -> %.1f  空头清算 $%.1fM" % (st["up_pct"], st["up_px"],
                                                 st["up_short_usd"] / 1e6))
    print("  -%.2f%% -> %.1f  多头清算 $%.1fM" % (st["dn_pct"], st["dn_px"],
                                                 st["dn_long_usd"] / 1e6))
    print("  R = %.2f" % st["r"])
    print("  已写入 data/coinglass.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
