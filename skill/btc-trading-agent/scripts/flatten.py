"""平掉本系统接管的 BTC 持仓，并清空本地状态，让系统从零开始。

只处理 BTC-USDT-SWAP；ETH 等其他合约不碰。
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import journal
from core.okx import OkxClient, OkxError
from core.utils import ROOT, read_json, setup_logging, write_json

LOG = setup_logging("flatten")
CFG = read_json(ROOT / "config.json", {}) or {}
INST = CFG["inst_id"]


def main():
    c = OkxClient(simulated=True)
    ins = c.instrument(INST)
    lot = float(ins["lotSz"])
    dec = len(str(lot).split(".")[-1]) if "." in str(lot) else 0

    print(f"=== {INST} 平仓 ===")
    # 双向模式下同一合约可能同时有多空两条腿，必须逐条平
    legs = [x for x in c.positions()
            if x["instId"] == INST and abs(float(x.get("pos") or 0)) > 0]
    if not legs:
        print("已无持仓，无需操作")
    else:
        # 1) 先撤掉所有条件单
        for a in c.algo_pending(INST):
            try:
                c.cancel_algo(a["algoId"], INST)
                print(f"  撤销条件单 {a['algoId']} posSide={a.get('posSide')}")
            except OkxError as e:
                print(f"  撤单失败 {a['algoId']}: {e}")
        time.sleep(1)

        for leg in legs:
            raw = float(leg["pos"])
            ps = leg.get("posSide")
            # 双向模式：pos 永远为正，方向看 posSide；单向模式才看正负号
            side = ps if ps in ("long", "short") else ("long" if raw > 0 else "short")
            size = abs(raw)
            close_side = "sell" if side == "long" else "buy"
            print(f"当前持仓: {side} {size} 张 @ {leg['avgPx']}  upl={leg.get('upl')}")
            sz = f"{size:.{dec}f}"
            try:
                r = c.place_order(INST, close_side, sz, ord_type="market",
                                  reduce_only=True,
                                  pos_side=ps if ps in ("long", "short") else None,
                                  cl_ord_id=f"FL{int(time.time())%1000000}")
                print(f"  平仓单 {close_side} {sz} posSide={ps} ordId={r.get('ordId')} sCode={r.get('sCode')}")
            except OkxError as e:
                print(f"  平仓失败: {e}")
            time.sleep(2)

        left = [x for x in c.positions()
                if x["instId"] == INST and abs(float(x.get("pos") or 0)) > 0]
        print(f"  平仓后剩余: {[(x.get('posSide'), x['pos']) for x in left] or '无'}")

    # 3) 清空本地状态里的持仓
    st = journal.load_state()
    for _sd in st.open_sides():
        _q = st.get_pos(_sd)
        print(f"  本地状态持仓 {_sd} {_q.size}张 → 清空")
    st.positions = {}
    journal.save_state(st)
    print("  本地状态已重置为空仓")

    b = c.balance()
    print(f"\n权益 {b.get('totalEq')}  UPL {b.get('upl')}")
    print("\n剩余的 SWAP 持仓：")
    for x in c.positions():
        if abs(float(x.get("pos") or 0)) > 0:
            print(f"  {x['instId']} {x['pos']} @{x['avgPx']} (本系统不管理)")


if __name__ == "__main__":
    main()
