import ast
import json
import re

# ============================================================
# 1) 修 FOMC 日期：FOMC 决议是美国东部 14:00，北京时间是次日 02:00
#    原表 "2026-10-28" 是美东会议日，但按北京日期算是 2026-10-29
#    导致 10-28 全天被误判为「事件静默期」，禁止开仓 25 小时
# ============================================================
p = "core/events.py"
s = open(p, encoding="utf-8").read()

old = '''FOMC_2026 = [
    "2026-01-28", "2026-03-18", "2026-04-29", "2026-06-17",
    "2026-07-29", "2026-09-16", "2026-10-28", "2026-12-09",
]'''

new = '''# 注意：FOMC 决议公布时间是美国东部 14:00，换算北京时间是【次日 02:00】。
# 这里必须填「北京日期」，即美东会议日 +1 天。
# 2026-10-28 修正：原来填的是美东日期，导致 10-28 全天被误判为事件静默期。
FOMC_2026 = [
    "2026-01-29", "2026-03-19", "2026-04-30", "2026-06-18",
    "2026-07-30", "2026-09-17", "2026-10-29", "2026-12-10",
]
# 决议公布的具体时间（北京时间 HH:MM），用于 30 分钟级静默窗
FOMC_DECISION_TIME = "02:00"'''

assert old in s, "FOMC_2026 表未匹配"
s = s.replace(old, new, 1)

# 2) 改进 blackout：不再整天静默，只在决议公布前后 30 分钟
old_bk = '''    blackout = False
    for e in evs:
        if e["days"] == 0 and e["impact"] == "high":
            blackout = True
            notes.append(f"今日 {e['name']}：公布后 30 分钟内禁止下单")'''

new_bk = '''    blackout = False
    for e in evs:
        if e["impact"] != "high":
            continue
        # 2026-10-28 改进：原来是「当天整天静默」，太粗（曾误封 25 小时）。
        # 现在只在高影响事件【公布时刻前后 30 分钟】静默。
        if e["days"] != 0:
            continue
        try:
            from datetime import datetime as _dt, timedelta as _td
            _t = getattr(e, "time", None) or cfg.get("fomc_decision_time", "02:00")
            _now = _dt.now(CST)
            _hh, _mm = [int(x) for x in str(_t).split(":")[:2]]
            _ev = _now.replace(hour=_hh, minute=_mm, second=0, microsecond=0)
            if abs((_now - _ev).total_seconds()) <= 30 * 60:
                blackout = True
                notes.append("%s 公布时刻前后 30 分钟：禁止下单" % e["name"])
            else:
                _h = round((_ev - _now).total_seconds() / 3600, 1)
                notes.append("%s 今日 %s 公布（还有 %.1f 小时），非静默窗" % (
                    e["name"], _t, _h))
        except Exception:
            blackout = True
            notes.append("%s 今日公布：禁止下单（时间解析失败，保守处理）" % e["name"])'''

assert old_bk in s, "blackout 逻辑未匹配"
s = s.replace(old_bk, new_bk, 1)

open(p, "w", encoding="utf-8").write(s)
ast.parse(s)
print("core/events.py: FOMC 日期改为北京日期(+1天)，blackout 改为公布前后30分钟")

# ============================================================
# 3) 同步修正 tools_should_fetch.py 的 CALENDAR
# ============================================================
q = "tools_should_fetch.py"
t = open(q, encoding="utf-8").read()
t = t.replace('("2026-10-28T02:00", "FOMC 利率决议")',
              '("2026-10-29T02:00", "FOMC 利率决议")')
t = t.replace('("2026-10-28T02:30", "FOMC 新闻发布会")',
              '("2026-10-29T02:30", "FOMC 新闻发布会")')
open(q, "w", encoding="utf-8").write(t)
ast.parse(t)
print("tools_should_fetch.py: CALENDAR 改为 2026-10-29T02:00/02:30")
for ln in t.splitlines():
    if "2026-1" in ln and "FOMC" in ln:
        print("   ", ln.strip())
