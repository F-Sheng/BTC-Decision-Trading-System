import ast
import json
import re

# ============ 1) 金十默认窗口 2h -> 4h ============
p = "tools_jin10.py"
s = open(p, encoding="utf-8").read()
orig = s
s = re.sub(r'(add_argument\(\s*["\']--hours["\'][^)]*?default\s*=\s*)2', r"\g<1>4", s, count=1)
if s == orig:
    s = re.sub(r"default=2(\s*,\s*help=)", r"default=4\1", s, count=1)
open(p, "w", encoding="utf-8").write(s)
ast.parse(s)
found = re.search(r"add_argument\(\s*[\"']--hours[\"'][^)]*\)", s, re.S)
print("tools_jin10.py:", (found.group(0).replace("\n", " ") if found else "未定位"))

# ============ 2) local_fetch：最对称配对 + 升级给 Agent ============
q = "local_fetch.py"
t = open(q, encoding="utf-8").read()

ACTION1 = ('"请 Agent 用 read_image 看 crop 图，确认 tooltip 价格与累计值，然后执行 "'
           ' "python tools_write_manual_map.py --spot <> --up-px <> --up-short-m <> '
           '--dn-px <> --dn-long-m <>"')
ACTION2 = ('"请 Agent 用 read_image 核对 crop 图，然后执行 '
           'tools_write_manual_map.py 人工写数"')

old_find = '''        # 2) 上方 ~3%：从画布 55% 处向右找，逐步加距离
        def find_side(sign: int):
            """sign=+1 上侧, -1 下侧"""
            for frac in (0.62, 0.72, 0.82, 0.92, 0.50, 0.42):
                x = int(cl + cw * frac) if sign > 0 else int(cl + cw * (1 - frac))
                x = max(int(cl + 10), min(int(cl + cw - 10), x))
                kind = "upper" if sign > 0 else "lower"
                r, why = _read_side(pg, base, snapdir, kind, x, mid_y, kind)
                if not r:
                    continue
                px = r["tooltip_price"]
                pct = (px - spot) / spot * 100 if sign > 0 else (spot - px) / spot * 100
                if 2.0 <= pct <= 5.0:
                    return r, pct, why
                # 百分比不合适就换更近/更远的档
            return None, None, "no acceptable position"

        up, up_pct, up_why = find_side(+1)
        dn, dn_pct, dn_why = find_side(-1)'''

new_find = '''        # 2) 上下各取多个候选，最后选「最对称的一对」
        def candidates(sign: int):
            outl = []
            for frac in (0.62, 0.72, 0.55, 0.82, 0.68, 0.78, 0.92, 0.50):
                x = int(cl + cw * frac) if sign > 0 else int(cl + cw * (1 - frac))
                x = max(int(cl + 10), min(int(cl + cw - 10), x))
                kind = "upper" if sign > 0 else "lower"
                r, why = _read_side(pg, base, snapdir, kind, x, mid_y, kind)
                if not r:
                    continue
                px = r["tooltip_price"]
                pct = (px - spot) / spot * 100 if sign > 0 else (spot - px) / spot * 100
                if 2.0 <= pct <= 5.0:
                    outl.append((r, pct, why))
            return outl

        up_c = candidates(+1)
        dn_c = candidates(-1)

        up = dn = None
        up_pct = dn_pct = None
        up_why = dn_why = "no acceptable position"
        best = 99.0
        for ur, up_, uw in up_c:
            for dr, dp_, dw in dn_c:
                gap = abs(up_ - dp_)
                if gap < best:
                    best, up, up_pct, up_why = gap, ur, up_, uw
                    dn, dn_pct, dn_why = dr, dp_, dw
        if up is not None:
            up_why = "%s | gap=%.2fpt" % (up_why, best)
            dn_why = "%s | gap=%.2fpt" % (dn_why, best)'''

assert old_find in t, "find_side 未匹配"
t = t.replace(old_find, new_find, 1)

old_fail = '''        if not up or not dn:
            out["why"] = "read failed: up=%s(%s) dn=%s(%s)" % (
                bool(up), up_why, bool(dn), dn_why)
            return out'''

new_fail = '''        if not up or not dn:
            why = "read failed: up=%s(%s) dn=%s(%s)" % (
                bool(up), up_why, bool(dn), dn_why)
            out["why"] = why
            try:
                esc = {
                    "ts": datetime.now().strftime("%Y-%m-%dT%H:%M"),
                    "spot": spot, "why": why,
                    "crops": sorted(str(x) for x in snapdir.glob("crop_*.jpg")),
                    "hovers": sorted(str(x) for x in snapdir.glob("hover_*.jpg")),
                    "base": str(base),
                    "action": ACTION_PLACEHOLDER,
                }
                (ROOT / "data" / "map_escalation.json").write_text(
                    json.dumps(esc, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception:
                pass
            return out'''

assert old_fail in t, "失败分支未匹配"
t = t.replace(old_fail, new_fail, 1)
t = t.replace("ACTION_PLACEHOLDER", ACTION1, 1)

old_val = '''        if _err:
            out["ok"] = False
            out["why"] = "validation failed: " + _err
        return out'''
new_val = '''        if _err:
            out["ok"] = False
            out["why"] = "validation failed: " + _err
            try:
                esc = {
                    "ts": datetime.now().strftime("%Y-%m-%dT%H:%M"),
                    "spot": spot, "why": out["why"],
                    "raw": {"upper": out.get("upper"), "lower": out.get("lower")},
                    "crops": sorted(str(x) for x in snapdir.glob("crop_*.jpg")),
                    "action": ACTION2_PLACEHOLDER,
                }
                (ROOT / "data" / "map_escalation.json").write_text(
                    json.dumps(esc, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception:
                pass
        else:
            f = ROOT / "data" / "map_escalation.json"
            if f.exists():
                try:
                    f.unlink()
                except Exception:
                    pass
        return out'''
assert old_val in t, "校验分支未匹配"
t = t.replace(old_val, new_val, 1)
t = t.replace("ACTION2_PLACEHOLDER", ACTION2, 1)

open(q, "w", encoding="utf-8").write(t)
ast.parse(t)
print("local_fetch.py: 最对称配对 + map_escalation.json OK")
