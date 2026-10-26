"""紧急修复：撤掉错误的止损单，给空单挂正确的止损。"""
import sys
import time

sys.path.insert(0, r"D:\DP workspace\btc_system")
from core import journal
from core.okx import OkxClient
from core.utils import setup_logging

LOG = setup_logging("fix")
c = OkxClient(simulated=True)
inst = "BTC-USDT-SWAP"

print("=== 当前持仓 ===")
for p in c.positions():
    print(f"  {p['instId']} posSide={p.get('posSide')} pos={p['pos']} avgPx={p.get('avgPx')}")

print("\n=== 当前条件单 ===")
for a in c._request("GET", "/api/v5/trade/orders-algo-pending?instId=" + inst +
                    "&ordType=conditional").get("data") or []:
    print(f"  algoId={a.get('algoId')} posSide={a.get('posSide')} sz={a.get('sz')} sl={a.get('slTriggerPx')}")

st = journal.load_state()
print("\n=== 本地状态 ===")
for sd in st.open_sides():
    q = st.get_pos(sd)
    print(f"  {sd} {q.size} @{q.entry:.1f} stop={q.stop}")

# 1) 撤销所有条件单，避免重复/错误
print("\n=== 1) 撤销全部条件单 ===")
for a in c._request("GET", "/api/v5/trade/orders-algo-pending?instId=" + inst +
                    "&ordType=conditional").get("data") or []:
    try:
        c.cancel_algo(a.get("algoId"), inst)
        print(f"  已撤 {a.get('algoId')} (posSide={a.get('posSide')} sz={a.get('sz')})")
    except Exception as e:
        print(f"  撤销失败 {a.get('algoId')}: {e}")
time.sleep(1)

# 2) 按本地状态逐边重挂止损
print("\n=== 2) 按本地状态重挂止损 ===")
for sd in st.open_sides():
    q = st.get_pos(sd)
    try:
        alg_side = "buy" if sd == "short" else "sell"
        a = c.place_algo_stop(inst, alg_side, f"{q.size:.2f}", q.stop,
                              pos_side=sd, cl_ord_id=f"FX{int(time.time()) % 1000000}")
        q.algo_id = a.get("algoId", "")
        st.set_pos(sd, q)
        print(f"  {sd} {q.size:.2f}张 止损{q.stop:.1f} -> algoId={a.get('algoId')}")
        time.sleep(1)
    except Exception as e:
        print(f"  {sd} 挂止损失败: {e}")

journal.save_state(st)
print("\n=== 3) 最终条件单 ===")
for a in c._request("GET", "/api/v5/trade/orders-algo-pending?instId=" + inst +
                    "&ordType=conditional").get("data") or []:
    print(f"  algoId={a.get('algoId')} posSide={a.get('posSide')} sz={a.get('sz')} sl={a.get('slTriggerPx')}")
