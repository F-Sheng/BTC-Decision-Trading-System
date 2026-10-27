import ast

p = "local_fetch.py"
s = open(p, encoding="utf-8").read()

# 1) 下侧再加更靠左的档位（x=0.38 只到 -1.71%，需要更远）
s = s.replace(
    "_FRACS_DN = (0.46, 0.42, 0.50, 0.38, 0.54, 0.34)   # 直接就是画布 x 比例",
    "_FRACS_DN = (0.40, 0.36, 0.44, 0.32, 0.28, 0.24, 0.48, 0.52)   # 直接就是画布 x 比例",
    1)
s = s.replace(
    "_FRACS_UP = (0.68, 0.64, 0.72, 0.60, 0.76, 0.56)",
    "_FRACS_UP = (0.68, 0.64, 0.72, 0.60, 0.76, 0.80, 0.84)",
    1)

# 2) 上方/下方窗口不够对称时，用「上侧 pct 反推下侧目标」再补试一次
anchor = '''        if up is not None and dn is not None:
            gap = abs(up_pct - dn_pct)'''
new_anchor = '''        # 上方成功但下方失败 -> 用上方的百分比反推下方应该在的位置再试
        if up is not None and dn is None and up_pct:
            _target_pct = min(up_pct, 3.4)
            _est_x = 0.68 - _target_pct * 0.088   # 经验斜率：1% ≈ 0.088 画布宽
            for _xf in (_est_x, _est_x - 0.03, _est_x + 0.03):
                r, pct, why = _try(-1, max(0.12, min(0.55, _xf)))
                if r is None:
                    continue
                if 2.0 <= pct <= 5.0:
                    dn, dn_pct, dn_why = r, pct, why + " | 按上侧反推"
                    break

        if up is not None and dn is not None:
            gap = abs(up_pct - dn_pct)'''
assert anchor in s, "对称补试插入点未匹配"
s = s.replace(anchor, new_anchor, 1)

# 3) 下方彻底失败时，回退到机器人自算 proxy 的 R（诚实标注来源）
old_fail = '''        if not up or not dn:
            why = "read failed: up=%s(%s) dn=%s(%s)" % (
                bool(up), up_why, bool(dn), dn_why)
            out["why"] = why'''
new_fail = '''        if not up or not dn:
            why = "read failed: up=%s(%s) dn=%s(%s)" % (
                bool(up), up_why, bool(dn), dn_why)
            out["why"] = why
            # 回退：从机器人日志读它自算的 L2[PROXY] R，明确标注来源
            try:
                import re as _re
                _logs = sorted((ROOT / "logs").glob("btc-*.log"))
                if _logs:
                    _txt = _logs[-1].read_text(encoding="utf-8",
                                               errors="ignore").splitlines()[-500:]
                    for _ln in reversed(_txt):
                        _m = _re.search(r"L2\\[PROXY\\]\\s+R=([\\d.]+)\\s+上3%=([\\d.]+)M\\s+下3%=([\\d.]+)M", _ln)
                        if _m:
                            out["fallback_proxy"] = {
                                "r": float(_m.group(1)),
                                "up_m": float(_m.group(2)),
                                "dn_m": float(_m.group(3)),
                                "source": "机器人自算 L2[PROXY]（非 CoinGlass 实测）",
                            }
                            out["why"] += " | 回退: proxy R=%.2f" % float(_m.group(1))
                            break
            except Exception:
                pass'''
assert old_fail in s, "失败分支未匹配"
s = s.replace(old_fail, new_fail, 1)

open(p, "w", encoding="utf-8").write(s)
ast.parse(s)
print("local_fetch.py: 下侧更多档位 + 按上侧反推 + 失败回退 proxy")
