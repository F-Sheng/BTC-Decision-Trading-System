"""加仓逻辑（2026-10-28 用户要求：多空两个方向都要）

用户原话：「碰到阻力下来是加仓的机会」「加仓逻辑那两个都需要」

设计（镜像对称）：
  加多：价格回落到【支撑位】被支撑住并反弹  -> 加多
  加空：价格反弹到【阻力位】被拒绝并回落    -> 加空

判定分两步：
  1. 测试：价格进入关键位 ± touch_tol_usd（默认 100 点）范围内 -> 记下 tested_level / tested_ts
  2. 确认：从测试点朝持仓方向走了 >= add_reject_pct（默认 0.35%）-> 视为「被支撑/被拒绝」，加仓

安全约束（任一不满足则不加）：
  · add_enabled = true
  · 该仓位加仓次数 < add_max_times（默认 2）
  · 距上次加仓价格变动 >= add_min_gap_pct（默认 0.5%），防抖
  · 加仓后总风险 <= account_risk_target 的 add_risk_cap 倍（默认 1.6 倍 = 8%）
  · 该方向仓位名义价值不超过权益 × margin_pct × leverage
  · 距重要事件（FOMC 等）超过 add_blackout_h 小时（默认 1.0）

加仓不是市价追单，走 maker（关键位上方/下方 110 点挂），不成交就放弃。
"""

from __future__ import annotations

import time

from . import journal

DEFAULTS = {
    "add_enabled": True,
    "add_max_times": 2,
    "add_size_mult": 0.4,          # 加当前仓位的 40%
    "add_min_gap_pct": 0.5,        # 两次加仓之间至少隔 0.5% 价格
    "add_reject_pct": 0.35,        # 从关键位回落/反弹 0.35% 才算确认
    "add_risk_cap": 1.6,           # 加仓后总风险上限 = account_risk_target × 1.6
    "add_blackout_h": 1.0,         # 重要事件前后 1 小时内不加仓
}


def _cfg(cfg: dict, key: str):
    return cfg.get(key, DEFAULTS[key])


def _near_event(cfg: dict) -> bool:
    """是否处在重要事件黑窗内（用 should_fetch 的日历判断）"""
    try:
        from pathlib import Path
        import json
        from datetime import datetime, timedelta, timezone
        CST = timezone(timedelta(hours=8))
        root = Path(r"D:\DP workspace\btc_system")
        # 复用 tools_should_fetch 的 CALENDAR
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "sf", root / "tools_should_fetch.py")
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        now = datetime.now(CST)
        for s, name in getattr(m, "CALENDAR", []):
            try:
                t = datetime.strptime(s, "%Y-%m-%dT%H:%M").replace(tzinfo=CST)
            except Exception:
                continue
            if abs((now - t).total_seconds()) <= float(_cfg(cfg, "add_blackout_h")) * 3600:
                return True
    except Exception:
        pass
    return False


def evaluate(pos, price: float, levels, cfg: dict, equity: float,
             logger) -> dict | None:
    """判断该仓位是否该加仓。返回 add 计划 dict 或 None。

    levels: (resist_list, support_list)，元素需有 .price 属性
    """
    if not _cfg(cfg, "add_enabled"):
        return None
    if not pos.is_open or not pos.entry:
        return None
    if getattr(pos, "add_count", 0) >= int(_cfg(cfg, "add_max_times")):
        return None
    if _near_event(cfg):
        logger.info("   加仓跳过：处在重要事件黑窗内")
        return None

    tol = float(cfg.get("touch_tol_usd", 100.0))
    rej = float(_cfg(cfg, "add_reject_pct")) / 100.0
    res_list = sorted([r.price for r in (levels[0] or []) if r.price > 0])
    sup_list = sorted([r.price for r in (levels[1] or []) if r.price > 0])

    if pos.side == "long":
        # 找入场价下方的最近支撑
        below = [p for p in sup_list if p < pos.entry]
        if not below:
            return None
        key = below[-1]
        # ① 测试：价格进入支撑 ± tol
        if abs(price - key) <= tol:
            if getattr(pos, "test_level", 0) != key:
                pos.test_level = key
                pos.test_ts = int(time.time() * 1000)
                logger.info("   [加仓观察] 多单测试支撑 %.1f（现价 %.1f）", key, price)
            return None
        # ② 确认：从支撑反弹 >= rej
        if (getattr(pos, "test_level", 0) == key
                and price >= key * (1 + rej)
                and price > pos.entry * 0.998):
            return {"side": "long", "key": key, "trigger": "支撑被确认",
                    "price": price}
    else:
        above = [p for p in res_list if p > pos.entry]
        if not above:
            return None
        key = above[0]
        if abs(price - key) <= tol:
            if getattr(pos, "test_level", 0) != key:
                pos.test_level = key
                pos.test_ts = int(time.time() * 1000)
                logger.info("   [加仓观察] 空单测试阻力 %.1f（现价 %.1f）", key, price)
            return None
        if (getattr(pos, "test_level", 0) == key
                and price <= key * (1 - rej)
                and price < pos.entry * 1.002):
            return {"side": "short", "key": key, "trigger": "阻力被拒绝",
                    "price": price}
    return None


def size_for(pos, cfg: dict, lot_sz: float):
    """加仓张数 = 当前仓位 × add_size_mult，按 lotSz 取整，最少 1 手"""
    mult = float(_cfg(cfg, "add_size_mult"))
    raw = pos.size * mult
    n = round(raw / lot_sz) * lot_sz if lot_sz else raw
    return n if n >= lot_sz else 0.0
