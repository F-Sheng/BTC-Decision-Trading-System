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
    p = c.position(INST)
    if not p:
        print("已无持仓，无需操作")
    else:
        pos = float(p["pos"])
        side = "long" if pos > 0 else "short"
        size = abs(pos)
        print(f"当前持仓: {side} {size} 张 @ {p['avgPx']}  upl={p.get('upl')}")

        # 1) 撤掉所有条件单
        for a in c.algo_pending(INST):
            try:
                c.cancel_algo(a["algoId"], INST)
                print(f"  撤销条件单 {a['algoId']}")
            except OkxError as e:
                print(f"  撤单失败 {a['algoId']}: {e}")

        # 2) 反向市价平仓
        close_side = "buy" if pos < 0 else "sell"
        sz = f"{size:.{dec}f}"
        r = c.place_order(INST, close_side, sz, ord_type="market",
                          reduce_only=True,
                          cl_ord_id=f"FL{int(time.time())%1000000}")
        print(f"  平仓单 {close_side} {sz}  ordId={r.get('ordId')} sCode={r.get('sCode')}")
        time.sleep(2)

        p2 = c.position(INST)
        left = abs(float(p2["pos"])) if p2 else 0.0
        print(f"  平仓后剩余: {left}")

    # 3) 清空本地状态里的持仓
    st = journal.load_state()
    old = st.position
    if old.is_open:
        print(f"  本地状态持仓 {old.side} {old.size}张 → 清空")
    st.position = journal.Position()
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
