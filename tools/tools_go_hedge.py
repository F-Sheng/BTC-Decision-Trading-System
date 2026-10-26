"""清空所有持仓与挂单，然后切换到双向持仓模式。"""
import sys
import time

sys.path.insert(0, r"D:\DP workspace\btc_system")
from core.okx import OkxClient
from core.utils import setup_logging

setup_logging("switch")
c = OkxClient(simulated=True)

print("=" * 62)
print("1) 撤销全部条件单")
print("=" * 62)
for inst in ("BTC-USDT-SWAP", "ETH-USDT-SWAP"):
    try:
        for a in c.algo_pending(inst):
            try:
                c.cancel_algo(a.get("algoId"), inst)
                print(f"   撤销 {inst} algoId={a.get('algoId')}")
            except Exception as e:
                print(f"   撤销失败 {a.get('algoId')}: {e}")
    except Exception as e:
        print(f"   {inst} 查询挂单失败: {e}")
time.sleep(1)

print()
print("=" * 62)
print("2) 市价平掉全部持仓")
print("=" * 62)
for p in list(c.positions()):
    sz = abs(float(p.get("pos") or 0))
    if sz <= 0:
        continue
    side = "buy" if float(p["pos"]) < 0 else "sell"
    inst = p["instId"]
    print(f"   平仓 {inst}: {side} {sz} 张")
    try:
        r = c.place_order(inst, side, sz, ord_type="market",
                          cl_ord_id=f"FLAT{int(time.time()) % 1000000}",
                          reduce_only=True)
        print(f"     ordId={r.get('ordId')} sCode={r.get('sCode')}")
    except Exception as e:
        print(f"     失败: {e}")
    time.sleep(2)

print()
print("=" * 62)
print("3) 剩余持仓检查")
print("=" * 62)
left = [p for p in c.positions() if abs(float(p.get("pos") or 0)) > 0]
if left:
    for p in left:
        print(f"   仍持有 {p['instId']} pos={p['pos']}")
else:
    print("   ✅ 已无持仓")

print()
print("=" * 62)
print("4) 切换到双向持仓 long_short_mode")
print("=" * 62)
ok = False
for attempt in range(3):
    try:
        r = c._request("POST", "/api/v5/account/set-position-mode",
                       body={"posMode": "long_short_mode"})
        print(f"   尝试{attempt+1}: code={r.get('code')} msg={r.get('msg')}")
        ok = True
        break
    except Exception as e:
        print(f"   尝试{attempt+1} 失败: {e}")
        time.sleep(3)

time.sleep(2)
d = c._request("GET", "/api/v5/account/config")["data"][0]
print(f"\n   最终 posMode = {d.get('posMode')}")
print(f"   acctLv      = {d.get('acctLv')}")
print("   " + ("✅ 双向持仓已启用" if d.get("posMode") == "long_short_mode" else "❌ 仍是单向，需排查"))
