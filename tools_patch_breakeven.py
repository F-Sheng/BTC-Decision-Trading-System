import ast

p = r"D:\DP workspace\btc_system\tools_should_fetch.py"
s = open(p, encoding="utf-8").read()

# ---------- 处理方式 B：保本/锁盈止损不算「大事」 ----------
old = '''def r_position_risk(price: float):
    """持仓距止损/止盈的最近距离百分比"""
    try:
        from core import journal
        st = journal.load_state()
        worst = None
        for sd in st.open_sides():
            p = st.get_pos(sd)
            levels = [p.stop] + list(p.targets or [])
            for lv in levels:
                if not lv:
                    continue
                d = abs(price - lv) / price * 100
                if worst is None or d < worst[0]:
                    worst = (d, sd, lv)
        return worst
    except Exception:
        return None'''

new = '''def r_position_risk(price: float):
    """持仓距「真正有风险的关键位」的最近距离百分比

    处理方式 B（用户 2026-10-27 选定）：
      保本止损 / 锁盈止损不算大事 —— 被它打掉最差也只是不赚，不会亏。
        · 多单：止损 >= 开仓价  -> 保本或已锁盈，跳过
        · 空单：止损 <= 开仓价  -> 保本或已锁盈，跳过
      止盈档位仍然算，因为接近止盈意味着马上要成交。
    """
    try:
        from core import journal
        st = journal.load_state()
        worst = None
        skipped = []
        for sd in st.open_sides():
            p = st.get_pos(sd)
            # 判断该止损是否已在保本或更好位置
            stop_safe = False
            if p.stop and p.entry:
                if sd == "long" and p.stop >= p.entry * 0.9995:
                    stop_safe = True
                elif sd == "short" and p.stop <= p.entry * 1.0005:
                    stop_safe = True
            for lv in ([p.stop] + list(p.targets or [])):
                if not lv:
                    continue
                is_stop = (lv == p.stop)
                if is_stop and stop_safe:
                    skipped.append("%s 止损 %.1f 已保本/锁盈" % (sd, lv))
                    continue
                d = abs(price - lv) / price * 100
                if worst is None or d < worst[0]:
                    worst = (d, sd, lv)
        if skipped and worst:
            worst = worst + ("跳过: " + "; ".join(skipped),)
        return worst
    except Exception:
        return None'''

assert old in s, "r_position_risk 未匹配"
s = s.replace(old, new, 1)

open(p, "w", encoding="utf-8").write(s)
ast.parse(s)
print("tools_should_fetch.py: 已实现处理方式 B（保本/锁盈止损不算大事）")
