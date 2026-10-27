"""切换 V4 版本 + 重置权益基准（用户 2026-10-28 要求）

V4 相对 V3 的重大变化：
  数据层
    · 清算地图：截图+VLM → LiquidVision 官方 API（/api/v1/liqmap，400 bins，实时）
    · 强平流：修复模拟盘接口 bug（虚高 1746 倍）→ 直连真实市场
    · 新闻：金十 important 官方爆标记 + Odaily REST API isImportant
    · X：5 个硬编码账号 → 「正在关注」时间线 + 25 账号清单 + 本地模型摘要
    · 宏观：接入 Odaily Skill（总市值/市占率/恐惧贪婪/涨跌榜）
    · 数据自检：12 项（含强平流量级、R 新鲜度）
  调度层
    · 固定 15 分钟 → 2 分钟心跳 + 三级分层（L0/L1/L2）
    · 新闻触发去重（同一★★不重复触发）
  交易层
    · 移动止损：1.0R 零缓冲 → 0.5R + max(0.4R, pnl_r*40%) 缓冲
    · 逆势单：新增 4 条独立退出条件
    · reverse_signal：逆势信号不再能推翻顺向仓位
    · 止盈：必须放在阻力/支撑内侧 100 点
    · 加仓：双向（支撑确认加多 / 阻力拒绝加空）
    · maker：关键位 ±110 点挂单 + 逐步收窄 + 最终市价兜底
"""

import json
import sys
import time
from datetime import datetime

sys.path.insert(0, r"D:\DP workspace\btc_system")

cp = r"D:\DP workspace\btc_system\config.json"
sp = r"D:\DP workspace\btc_system\state\bot_state.json"

c = json.load(open(cp, encoding="utf-8"))

# 取当前 OKX 权益作为 V4 起点
try:
    from core.okx import OkxClient
    ok = OkxClient(simulated=True)
    b = ok.balance()
    eq = float(b.get("totalEq") or 0)
except Exception as e:
    print("取权益失败:", str(e)[:80])
    eq = 0.0

if eq <= 0:
    print("无法取到权益，放弃重置")
    sys.exit(1)

old_ver = c.get("strategy_version")
old_eq = c.get("run_start_equity")

now_ms = int(time.time() * 1000)
c["strategy_version"] = "V4"
c["run_start_equity"] = round(eq, 2)
c["run_started_at"] = now_ms
c["v3_archive"] = {
    "start_equity": old_eq,
    "started_at": c.get("v3_started_at") or 1793034476691,
    "final_equity": round(eq, 2),
    "pnl": round(eq - (old_eq or 0), 2),
    "pnl_pct": round((eq - (old_eq or 0)) / (old_eq or 1) * 100, 2) if old_eq else None,
    "note": "V3 于 2026-10-28 02:55 结束，切换 V4",
}
json.dump(c, open(cp, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

# 同步 state
try:
    s = json.load(open(sp, encoding="utf-8"))
    s["start_equity"] = c["run_start_equity"]
    s["started_at"] = now_ms
    s["equity"] = round(eq, 2)
    s["cycle_count"] = 0
    s["loss_streak"] = 0
    json.dump(s, open(sp, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    state_ok = True
except Exception as e:
    print("state 更新失败:", str(e)[:80])
    state_ok = False

print("=== V3 -> V4 切换完成 ===")
print("  版本: %s -> V4" % old_ver)
print("  V3 起始权益: %.2f" % (old_eq or 0))
print("  V3 结束权益: %.2f" % eq)
print("  V3 区间盈亏: %+.2f U (%+.2f%%)"
      % (eq - (old_eq or 0), (eq - (old_eq or 0)) / (old_eq or 1) * 100 if old_eq else 0))
print()
print("  V4 新起点: %.2f" % c["run_start_equity"])
print("  V4 起始时间: %s" % datetime.fromtimestamp(now_ms / 1000).strftime("%Y-%m-%d %H:%M:%S"))
print("  state 同步: %s" % ("OK" if state_ok else "失败"))
print()
print("  V3 数据已归档到 config.v3_archive")
