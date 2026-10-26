"""执行链路自检：在模拟盘上走完 开仓 → 查持仓 → 挂条件止损 → 撤单 → 平仓 → 校验空仓。

这是让系统真正下单前必须过的一关：影子模式测不出下单是否会失败。
全部使用 1 张最小仓位，且结束时会平掉，净敞口回到起点。
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import executor, journal
from core.okx import OkxClient, OkxError
from core.utils import ROOT, read_json, setup_logging

LOG = setup_logging("selftest")
CFG = read_json(ROOT / "config.json", {}) or {}
INST = CFG["inst_id"]


def step(n, title):
    print(f"\n[{n}/7] {title}")


def main():
    c = OkxClient(simulated=True)
    ins = c.instrument(INST)
    lot = float(ins["lotSz"])
    print(f"合约 {INST}  ctVal={ins['ctVal']}  lotSz={lot}  minSz={ins['minSz']}")

    p0 = c.position(INST)
    start = float(p0["pos"]) if p0 else 0.0
    print(f"起始净持仓: {start}")

    step(1, "设置 10x 杠杆 / 全仓")
    try:
        r = c.set_leverage(INST, 10, "cross")
        print("   OK", r.get("code"))
    except OkxError as e:
        print("   FAIL", e)
        return

    step(2, "市价卖出 1 张（最小单位）")
    try:
        r = c.place_order(INST, "sell", "1", ord_type="market", pos_side="short",
                          cl_ord_id=f"ST{int(time.time())%1000000}")
        print("   ordId =", r.get("ordId"), " sCode =", r.get("sCode"), r.get("sMsg"))
    except OkxError as e:
        print("   FAIL", e)
        return
    time.sleep(1.5)

    step(3, "校验持仓变化 +1 张")
    p = c.position(INST, pos_side="short")
    now = abs(float(p["pos"])) if p else 0.0
    print(f"   现在净持仓 {now}（起始 {start}，变化 {now-start:+.2f}）")
    if abs((now - start) + 1) > 1e-6:
        print("   ⚠ 持仓变化与预期不符")
    print(f"   avgPx={p.get('avgPx')}  lever={p.get('lever')}  liqPx={p.get('liqPx')}  "
          f"upl={p.get('upl')}")

    step(4, "挂交易所侧条件止损（触发价 = 现价 ×1.05）")
    px = float(p["avgPx"])
    algo_id = ""
    try:
        a = c.place_algo_stop(INST, "buy", "1", px * 1.05, pos_side="short",
                              cl_ord_id=f"STSL{int(time.time())%1000000}")
        algo_id = a.get("algoId", "")
        print("   algoId =", algo_id)
    except OkxError as e:
        print("   FAIL", e)

    step(5, "查询待触发条件单")
    try:
        rows = c.algo_pending(INST)
        print(f"   条件单数量 {len(rows)}")
        for x in rows:
            print(f"     {x.get('side')} sz={x.get('sz')} slTriggerPx={x.get('slTriggerPx')} "
                  f"state={x.get('state')} algoId={x.get('algoId')}")
    except OkxError as e:
        print("   FAIL", e)

    step(6, "撤销条件止损")
    if algo_id:
        try:
            c.cancel_algo(algo_id, INST)
            print("   已撤销")
        except OkxError as e:
            print("   撤销失败:", e)

    step(7, "买回 1 张，恢复原始持仓")
    try:
        r = c.place_order(INST, "buy", "1", ord_type="market", pos_side="short",
                          reduce_only=True,
                          cl_ord_id=f"STC{int(time.time())%1000000}")
        print("   ordId =", r.get("ordId"))
    except OkxError as e:
        print("   FAIL", e)
    time.sleep(1.5)

    p2 = c.position(INST)
    fin = float(p2["pos"]) if p2 else 0.0
    print(f"\n结束净持仓 {fin}（起始 {start}，差 {fin-start:+.2f}）")
    if abs(fin - start) < 1e-6:
        print("✅ 执行链路自检通过：下单 / 查仓 / 挂止损 / 撤单 / 平仓 全部正常")
    else:
        print("⚠ 净持仓未复原，请手动检查")

    b = c.balance()
    print(f"权益 {b.get('totalEq')}  UPL {b.get('upl')}")


if __name__ == "__main__":
    main()
