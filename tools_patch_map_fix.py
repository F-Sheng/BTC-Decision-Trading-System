import ast

p = "local_fetch.py"
s = open(p, encoding="utf-8").read()

# ---------- 改 pick：上下各用独立序列、试更多档、记录每次失败原因 ----------
old = '''        def pick(sign: int):
            best = (None, None, "no acceptable position")
            for frac in _FRACS[:4]:
                r, pct, why = _try(sign, frac)
                if r is None:
                    continue
                if 2.6 <= pct <= 3.6:
                    return r, pct, why
                if 2.0 <= pct <= 5.0 and best[0] is None:
                    best = (r, pct, why)
            return best'''

new = '''        # 上下各用独立的位置序列（原来下侧复用 1-frac，四档都挤在左中区域，
        # 经常覆盖不到 -3% 的位置，导致 "no acceptable position"）
        _FRACS_UP = (0.68, 0.64, 0.72, 0.60, 0.76, 0.56)
        _FRACS_DN = (0.46, 0.42, 0.50, 0.38, 0.54, 0.34)   # 直接就是画布 x 比例

        def pick(sign: int):
            best = (None, None, "no acceptable position")
            tries = []
            frs = _FRACS_UP if sign > 0 else _FRACS_DN
            for frac in frs:
                r, pct, why = _try(sign, frac)
                if r is None:
                    tries.append("x=%.2f %s" % (frac, why))
                    continue
                tries.append("x=%.2f -> %.2f%%" % (frac, pct))
                if 2.6 <= pct <= 3.6:
                    return r, pct, why + " | " + "; ".join(tries)
                if 2.0 <= pct <= 5.0 and best[0] is None:
                    best = (r, pct, why)
            if best[0] is not None:
                return best
            return None, None, "no acceptable position | " + "; ".join(tries)'''

assert old in s, "pick 未匹配"
s = s.replace(old, new, 1)

# _try 里 x 直接用 frac（下侧已经是画布比例）
old_try = '''        def _try(sign: int, frac: float):
            x = int(cl + cw * frac) if sign > 0 else int(cl + cw * (1 - frac))'''
new_try = '''        def _try(sign: int, frac: float):
            # 上侧 frac 直接用；下侧 _FRACS_DN 本身就是画布 x 比例，不再 1-frac
            x = int(cl + cw * frac)'''
assert old_try in s, "_try 未匹配"
s = s.replace(old_try, new_try, 1)

open(p, "w", encoding="utf-8").write(s)
ast.parse(s)
print("local_fetch.py: 上下独立位置序列 + 每次尝试记录原因")
