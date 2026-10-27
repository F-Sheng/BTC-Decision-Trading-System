import ast

p = "tools_lv_map.py"
s = open(p, encoding="utf-8").read()

# 窗口对称检查已过时（那是截图时代的检查），改为「窗口实际值是否符合请求」+ 新增数据陈旧检查
old = '''        ok("LV 窗口对称", abs((st.get("up_pct") or 0) - (st.get("dn_pct") or 0)) < 0.5,
           "%.2f%% vs %.2f%%" % (st.get("up_pct") or 0, st.get("dn_pct") or 0))'''
new = '''        # 注：旧版的「窗口对称」检查来自截图时代（手动 hover 会偏到 +3.26%/-4.48%）。
        # 现在 API 直接按 ±3% 索引取 bin，天然对称，改成检查「实际窗口是否落在 2.7~3.3%」
        _upw, _dnw = st.get("up_pct") or 0, st.get("dn_pct") or 0
        ok("LV 窗口命中", 2.7 <= _upw <= 3.3 and 2.7 <= _dnw <= 3.3,
           "上 %.2f%% / 下 %.2f%%（请求 ±3%%）" % (_upw, _dnw))'''
assert old in s, "对称检查未匹配"
s = s.replace(old, new, 1)

# 新增：coinglass.json 的 R 是否陈旧
anchor = '''    # 4. LiquidVision 清算汇总'''
new_chk = '''    # 3b. coinglass.json 里的 R 是否新鲜（用户 2026-10-28 指出我引用过 2 小时前的旧 R）
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

    # 4. LiquidVision 清算汇总'''
assert anchor in s
s = s.replace(anchor, new_chk, 1)

open(p, "w", encoding="utf-8").write(s)
ast.parse(s)
print("tools_lv_map.py: 对称检查改为窗口命中检查 + 新增 R 新鲜度检查")
