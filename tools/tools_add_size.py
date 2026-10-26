"""把现有半仓补到满仓（33% 保证金 / 风险上限 5%）。"""
import sys
import time

sys.path.insert(0, r"D:\DP workspace\btc_system")
from core import journal, risk
from core.okx import OkxClient
from core.utils import ROOT, read_json, setup_logging

LOG = setup_logging("addsize")
c = OkxClient(simulated=True)
cfg = read_json(ROOT / "config.json")
inst = cfg["inst_id"]
ins = c.instrument(inst)
cfg["ct_val"] = float(ins["ctVal"])
cfg["lot_sz"] = float(ins["lotSz"])
lot = cfg["lot_sz"]

price = float(c.ticker(inst)["last"])
eq = float(c.balance().get("totalEq") or 0)
st = journal.load_state()
pos = st.get_pos("short")
if not pos.is_open:
    print("没有空单，退出")
    sys.exit(0)

print(f"现价 {price:,.1f}   权益 {eq:,.0f}")
print(f"当前持仓 {pos.size:.2f} 张 @{pos.entry:,.1f}  止损 {pos.stop:,.1f}")

# 用风控模型算满仓张数
from core.strategy import Signal

sig = Signal(side="short", model="ADD", entry=price, stop=pos.stop,
             targets=list(pos.targets), counter_trend=False,
             confidence=0.65, structure=abs(pos.stop - price) / price)
sig.size_mult = 1.0
from core import liquidation
c1 = c.candles_bulk(inst, "1H", 720)
oi = c.open_interest(inst)
fr = c.funding_rate(inst)
ls = c.long_short_ratio("BTC")
lv = liquidation.build_proxy(c1, price, float(oi["oiCcy"]), float(fr["fundingRate"]),
                             float(ls[0][1]) if ls else 0, 0.5)
plan = risk.finalize(sig, eq, cfg, lv, cfg["ct_val"], lot, price,
                     open_positions=1, dfs_source=None)
target = round(plan.size_contracts, 2)
delta = round(target - pos.size, 2)
print(f"满仓目标 {target:.2f} 张（保证金 {plan.margin_pct*100:.1f}%，风险 {plan.account_risk_pct*100:.2f}%）")
print(f"需补 {delta:.2f} 张")

if delta < lot:
    print("差额不足最小张数，跳过")
    sys.exit(0)

# 撤旧止损 -> 加仓 -> 按新张数重挂
if pos.algo_id:
    try:
        c.cancel_algo(pos.algo_id, inst)
        print("已撤旧止损")
    except Exception as e:
        print("撤旧止损失败:", e)
time.sleep(1)

try:
    r = c.place_order(inst, "sell", f"{delta:.2f}", ord_type="market",
                      pos_side="short", cl_ord_id=f"ADD{int(time.time()) % 1000000}")
    print("加仓成交 ordId=", r.get("ordId"), " sCode=", r.get("sCode"))
except Exception as e:
    print("加仓失败:", e)
    sys.exit(1)
time.sleep(2)

p = c.position(inst, pos_side="short")
newsz = abs(float(p["pos"])) if p else pos.size
newpx = float(p["avgPx"]) if p else pos.entry
print(f"加仓后 张数={newsz:.2f}  均价={newpx:,.1f}")

try:
    a = c.place_algo_stop(inst, "buy", f"{newsz:.2f}", pos.stop,
                          pos_side="short", cl_ord_id=f"SL{int(time.time()) % 1000000}")
    print("新止损已挂 algoId=", a.get("algoId"))
    pos.algo_id = a.get("algoId", "")
except Exception as e:
    print("挂止损失败:", e)

pos.size = newsz
pos.entry = newpx
st.set_pos("short", pos)
journal.save_state(st)
print("本地状态已同步")
