import ast

p = "run.py"
s = open(p, encoding="utf-8").read()

# ---------- 1) 打分后缓存关键位（供下一轮加仓判定用） ----------
old_sc = '''    cache["sc_dir"] = getattr(sc, "direction", None)'''
new_sc = '''    cache["sc_dir"] = getattr(sc, "direction", None)
    # 缓存关键位供下一轮「加仓判定」使用（manage/加仓在打分之前执行）
    try:
        from core import levels as _lvmod
        _res, _sup = _lvmod.build(l3, price)
        cache["levels"] = (_res, _sup)
    except Exception as _e:
        pass'''
assert old_sc in s, "sc_dir 锚点未匹配"
s = s.replace(old_sc, new_sc, 1)

# ---------- 2) 在持仓管理循环之后插入加仓判定 ----------
old_loop = '''            journal.save_state(st)

    # ---- 定时刷新数据层 ----'''
new_loop = '''            journal.save_state(st)

    # ---- 加仓判定（2026-10-28 用户要求：多空两个方向都要）----
    # 逻辑：价格测试关键位并朝持仓方向确认后，加当前仓位的 add_size_mult
    if cfg.get("add_enabled", True) and cache.get("levels"):
        try:
            from core import add_position
            _res, _sup = cache["levels"]
            for _side in st.open_sides():
                _p = st.get_pos(_side)
                if not _p.is_open:
                    continue
                _plan = add_position.evaluate(_p, price, (_res, _sup), cfg, equity, LOG)
                if not _plan:
                    continue
                # 防抖：距上次加仓价格变动不足则跳过
                _gap = float(cfg.get("add_min_gap_pct", 0.5)) / 100.0
                _ref = _p.last_add_px or _p.entry
                if _ref and abs(price - _ref) / _ref < _gap:
                    continue
                _sz = add_position.size_for(_p, cfg, lot)
                if _sz <= 0:
                    continue
                LOG.warning("   [加仓] %s %s 关键位%.1f 触发（现价%.1f），加 %.2f 张（原%.2f张）",
                            _side, _plan["trigger"], _plan["key"], price, _sz, _p.size)
                try:
                    from core.strategy import Signal as _Sig
                    _pside = "sell" if _side == "long" else "buy"
                    _key = _plan["key"]
                    _off = float(cfg.get("maker_offset_usd", 110))
                    # 加多：支撑位下方 110 点挂；加空：阻力位上方 110 点挂
                    _add_px = (_key - _off) if _side == "long" else (_key + _off)
                    _tick = float(client.instrument(inst).get("tickSz") or 0.1)
                    _oid, _fp = executor._place_entry(
                        client, inst, _pside, executor._f(_sz, lot), _side,
                        _tick, _add_px, cfg, LOG, f"ADD{int(time.time())%1000000}")
                    if _oid:
                        _p.size = executor._f(_p.size + _sz, lot)
                        _p.add_count = int(getattr(_p, "add_count", 0)) + 1
                        _p.last_add_px = price
                        LOG.warning("   [加仓完成] %s 现持仓 %.2f 张，累计加仓 %d 次",
                                    _side, _p.size, _p.add_count)
                        journal.save_state(st)
                except Exception as _e:
                    LOG.warning("   加仓下单失败: %s", _e)
        except Exception as _e:
            LOG.warning("   加仓判定异常: %s", _e)

    # ---- 定时刷新数据层 ----'''
assert old_loop in s, "持仓循环尾部锚点未匹配"
s = s.replace(old_loop, new_loop, 1)

open(p, "w", encoding="utf-8").write(s)
ast.parse(s)
print("run.py: 加仓判定已接入（多空双向，maker 挂关键位外侧 110 点）")
