"""清空所有持仓（正确支持双向模式）。"""
import sys
import time

sys.path.insert(0, r"D:\DP workspace\btc_system")
from core.okx import OkxClient
from core.utils import setup_logging

setup_logging("flat")
c = OkxClient(simulated=True)

print("=== 清空条件单 ===")
for inst in ("BTC-USDT-SWAP", "ETH-USDT-SWAP"):
    for a in c._request("GET", f"/api/v5/trade/orders-algo-pending?instId={inst}&ordType=conditional"
                        ).get("data") or []:
        try:
            c.cancel_algo(a.get("algoId"), inst)
            print(f"  撤销 {inst} {a.get('algoId')} posSide={a.get('posSide')}")
        except Exception as e:
            print(f"  撤销失败: {e}")
time.sleep(1)

print("\n=== 平掉所有持仓 ===")
for round_ in range(3):
    poss = [p for p in c.positions() if abs(float(p.get("pos") or 0)) > 0]
    if not poss:
        break
    for p in poss:
        inst = p["instId"]
        sz = abs(float(p["pos"]))
        ps = p.get("posSide")
        if ps == "long":
            side, pside = "sell", "long"
        elif ps == "short":
            side, pside = "buy", "short"
        else:  # net 模式
            side, pside = ("buy" if float(p["pos"]) < 0 else "sell"), None
        print(f"  平 {inst} posSide={ps} {sz} 张 -> {side}")
        try:
            body = {"instId": inst, "tdMode": "cross", "side": side,
                    "ordType": "market", "sz": f"{sz:.2f}", "reduceOnly": True,
                    "clOrdId": f"CL{int(time.time()) % 1000000}"}
            if pside:
                body["posSide"] = pside
            r = c._request("POST", "/api/v5/trade/order", body=body)["data"][0]
            print(f"     ordId={r.get('ordId')} sCode={r.get('sCode')} {r.get('sMsg','')}")
        except Exception as e:
            print(f"     失败: {e}")
        time.sleep(2)
    time.sleep(2)

print("\n=== 结果 ===")
left = [p for p in c.positions() if abs(float(p.get("pos") or 0)) > 0]
if left:
    for p in left:
        print(f"  仍持有 {p['instId']} posSide={p.get('posSide')} pos={p['pos']}")
else:
    print("  ✅ 全部清空")
print(f"  账户权益 {float(c.balance().get('totalEq') or 0):,.2f}")
