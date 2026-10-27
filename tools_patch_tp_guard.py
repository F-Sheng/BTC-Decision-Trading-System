import ast

p = "core/risk.py"
s = open(p, encoding="utf-8").read()

# ---- 在 targets.build 之后加「止盈必须留在阻力/支撑内侧」的约束 ----
old = '''            if tg:
                sig.targets = tg
                for n in tg_notes:
                    p.notes.append("目标：" + n)'''

new = '''            if tg:
                # 2026-10-28 用户要求：止盈必须放在阻力位「下方」/支撑位「上方」，
                # 否则价格没突破关键位就直接回头，止盈永远打不到。
                # 多单：TP <= 最近压力 - tp_safety_usd
                # 空单：TP >= 最近支撑 + tp_safety_usd
                try:
                    safety = float(cfg.get("tp_safety_usd", 100.0))
                    res_list, sup_list = [], []
                    for x in (dfs_source or {}).values():
                        pass  # 占位：下面用 levels.build 统一算
                    from . import levels as _lv2
                    _res, _sup = _lv2.build(dfs_source, price or sig.entry)
                    if sig.side == "long":
                        _above = sorted([r.price for r in _res if r.price > sig.entry])
                        if _above:
                            _cap = _above[0] - safety
                            _new = []
                            for t in tg:
                                if t > _cap:
                                    p.notes.append(
                                        "止盈 %.1f 高于最近压力 %.1f，压到 %.1f（留 %.0f 点）"
                                        % (t, _above[0], _cap, safety))
                                _new.append(min(t, _cap))
                            # 去重 + 保序
                            _seen, _out = set(), []
                            for t in _new:
                                k = round(t, 0)
                                if k in _seen:
                                    continue
                                _seen.add(k)
                                _out.append(t)
                            tg = _out
                    else:
                        _below = sorted([r.price for r in _sup if r.price < sig.entry],
                                        reverse=True)
                        if _below:
                            _floor = _below[0] + safety
                            _new = []
                            for t in tg:
                                if t < _floor:
                                    p.notes.append(
                                        "止盈 %.1f 低于最近支撑 %.1f，抬到 %.1f（留 %.0f 点）"
                                        % (t, _below[0], _floor, safety))
                                _new.append(max(t, _floor))
                            _seen, _out = set(), []
                            for t in _new:
                                k = round(t, 0)
                                if k in _seen:
                                    continue
                                _seen.add(k)
                                _out.append(t)
                            tg = _out
                except Exception as e:
                    p.notes.append("止盈关键位约束失败(%s)，保持原值" % e)

                sig.targets = tg
                for n in tg_notes:
                    p.notes.append("目标：" + n)'''

assert old in s, "targets.build 赋值块未匹配"
s = s.replace(old, new, 1)
open(p, "w", encoding="utf-8").write(s)
ast.parse(s)
print("core/risk.py: 已加「止盈必须留在阻力/支撑内侧」约束")
