import ast
import json

# ============================================================
# ① 修 tools_liq_flow.py：不能用带 simulated 头的 OKX 接口
#    实测对比（同一时刻）：
#      带 x-simulated-trading:1  -> 价格 80,120~80,837，单笔最大 10,000 张，合计 300,292 张
#      不带（真实市场）          -> 价格 80,366~80,789，单笔最大 91.93 张，合计 540 张
#    模拟盘头返回的是 demo 环境的垃圾数据，量级虚高 556 倍，且出现 80,120 这个
#    现价根本不存在的价位（LiquidVision 跨交易所数据也确认 80,050~80,200 区间 0 条）。
# ============================================================
p = "tools_liq_flow.py"
s = open(p, encoding="utf-8").read()

old = '''def fetch():
    from core.okx import OkxClient
    c = OkxClient(simulated=True)
    r = c._request("GET", "/api/v5/public/liquidation-orders",
                   {"instType": "SWAP", "uly": "BTC-USDT",
                    "state": "filled", "limit": "100"})
    data = r.get("data") or []
    if not data:
        return []
    return data[0].get("details") or []'''

new = '''def fetch():
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
    return data[0].get("details") or []'''

assert old in s, "fetch 未匹配"
s = s.replace(old, new, 1)

# 阈值按真实数据重标（真实合计 540 张 / 100 条，不是 30 万张）
s = s.replace("BURST_COUNT_5M = 20", "BURST_COUNT_5M = 12")
s = s.replace("BURST_VOL_5M = 50000", "BURST_VOL_5M = 150")
s = s.replace("FRESH_SEC = 60", "FRESH_SEC = 90")

open(p, "w", encoding="utf-8").write(s)
ast.parse(s)
print("tools_liq_flow.py: 改为直连真实市场接口，阈值按真实量级重标")
print("  新阈值: 5分钟>=12条 或 >=150张 或 最新<90秒")

# ============================================================
# ② 同步修 core/liq_density.py
# ============================================================
q = "core/liq_density.py"
t = open(q, encoding="utf-8").read()
old2 = '''    try:
        c = OkxClient(simulated=True)
        r = c._request("GET", "/api/v5/public/liquidation-orders",
                       {"instType": "SWAP", "uly": "BTC-USDT",
                        "state": "filled", "limit": "100"})
        data = r.get("data") or []
        details = (data[0].get("details") or []) if data else []
    except Exception as e:
        return {"ok": False, "why": str(e)[:80]}'''
new2 = '''    try:
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
        return {"ok": False, "why": str(e)[:80]}'''
assert old2 in t, "liq_density fetch 未匹配"
t = t.replace(old2, new2, 1)
open(q, "w", encoding="utf-8").write(t)
ast.parse(t)
print("core/liq_density.py: 同步改为真实市场接口")

# ============================================================
# ③ 移动止盈止损改成「留缓冲」版（用户 2026-10-28 要求）
# ============================================================
r = "core/executor.py"
u = open(r, encoding="utf-8").read()
old3 = '''            # 锁定 R：峰值 − trail_lock_r，但绝不让止损越过保本
            lock_r = max(0.0, pnl_r - float(cfg.get("trail_lock_r", 0.5)))'''
new3 = '''            # 锁定 R：2026-10-28 用户要求改成「留缓冲」版本。
            # 旧逻辑 lock_r = pnl_r - 0.5，在 pnl_r=0.5 时锁 0R -> 止损=开仓价，
            # 没有任何回撤空间，实测被 0.15% 的正常波动打掉，错失后续 0.75% 行情。
            # 新逻辑：缓冲 = max(buffer_min_r, pnl_r * buffer_ratio)，最少 0.4R。
            _buf_min = float(cfg.get("trail_buffer_min_r", 0.4))
            _buf_ratio = float(cfg.get("trail_buffer_ratio", 0.4))
            buffer_r = max(_buf_min, pnl_r * _buf_ratio)
            lock_r = max(0.0, pnl_r - buffer_r)'''
assert old3 in u, "lock_r 未匹配"
u = u.replace(old3, new3, 1)
open(r, "w", encoding="utf-8").write(u)
ast.parse(u)
print("core/executor.py: 移动止损改为留缓冲（buffer = max(0.4R, pnl_r*40%)）")

# 配置
c = json.load(open("config.json", encoding="utf-8"))
c["trail_buffer_min_r"] = 0.4
c["trail_buffer_ratio"] = 0.4
json.dump(c, open("config.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print("config.json: trail_buffer_min_r=0.4, trail_buffer_ratio=0.4")
