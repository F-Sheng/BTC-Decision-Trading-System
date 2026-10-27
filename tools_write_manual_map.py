"""Agent 人工介入写清算地图数据（三读不一致时的兜底通道）

背景（用户 2026-10-27 要求）：
  local_fetch 的双读/三读都失败时，不能卡住整条链路。
  此时由 Agent（我）亲自用 read_image 看那几张 crop 图，确认数字后：
    python tools_write_manual_map.py --spot 80334 \
        --up-px 82951 --up-short-m 563.19 \
        --dn-px 78430 --dn-long-m 552.10
  本脚本做与 local_fetch 完全相同的校验和写入，保证数据结构一致。

  写完后自动清除 data/map_escalation.json 标记。
"""

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(r"D:\DP workspace\btc_system")
sys.path.insert(0, str(ROOT))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--spot", type=float, required=True)
    ap.add_argument("--up-px", type=float, required=True)
    ap.add_argument("--up-short-m", type=float, required=True)
    ap.add_argument("--dn-px", type=float, required=True)
    ap.add_argument("--dn-long-m", type=float, required=True)
    ap.add_argument("--note", default="Agent 人工看图")
    a = ap.parse_args()

    spot = a.spot
    up_pct = (a.up_px - spot) / spot * 100
    dn_pct = (spot - a.dn_px) / spot * 100
    r = a.up_short_m / a.dn_long_m if a.dn_long_m else 0

    errs = []
    if not (1.5 <= up_pct <= 6.0):
        errs.append("up_pct %.2f" % up_pct)
    if not (1.5 <= dn_pct <= 6.0):
        errs.append("dn_pct %.2f" % dn_pct)
    if abs(up_pct - dn_pct) > 2.0:
        errs.append("asymmetric gap %.2f" % abs(up_pct - dn_pct))
    if not (0.2 <= r <= 5.0):
        errs.append("R %.2f" % r)

    print("spot=%s  up=+%.2f%%(%sM)  dn=-%.2f%%(%sM)  R=%.2f"
          % (spot, up_pct, a.up_short_m, dn_pct, a.dn_long_m, r))
    if errs:
        print("校验未通过:", "; ".join(errs))
        print("仍然写入（人工确认优先），但会标记 manual_override")
    else:
        print("校验通过")

    p = ROOT / "data" / "coinglass.json"
    try:
        d = json.load(open(p, encoding="utf-8"))
    except Exception:
        d = {}
    d["captured_at"] = int(time.time() * 1000)
    d["binance_liqmap_1d"] = {
        "spot": spot,
        "near": {"pct": round((up_pct + dn_pct) / 2, 2),
                 "up_px": a.up_px, "short": int(a.up_short_m * 1e6),
                 "dn_px": a.dn_px, "long": int(a.dn_long_m * 1e6)},
        "far": {"pct": round(up_pct, 2), "up_px": a.up_px,
                "short": int(a.up_short_m * 1e6),
                "dn_px": a.dn_px, "long": int(a.dn_long_m * 1e6)},
        "points": [{"px": a.up_px, "short": int(a.up_short_m * 1e6)},
                   {"px": a.dn_px, "long": int(a.dn_long_m * 1e6)}],
        "method": "MANUAL: " + a.note,
        "axis_note": "x轴非线性，按 tooltip 真实价格反算百分比",
    }
    if errs:
        d["manual_override"] = {"ts": datetime.now().strftime("%Y-%m-%dT%H:%M"),
                                "issues": errs}
    json.dump(d, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("已写入", p)

    # 追加 snapshot
    try:
        from core import journal
        st = journal.load_state()
        pos = None
        for sd in ("short", "long"):
            if sd in st.open_sides():
                p0 = st.get_pos(sd)
                pos = {"side": p0.side, "entry": p0.entry, "size": p0.size,
                       "sl": p0.stop,
                       "tp1": p0.targets[0] if p0.targets else 0,
                       "upl_pct": round((p0.entry - spot) / p0.entry * 100, 2)
                       if sd == "short" else
                       round((spot - p0.entry) / p0.entry * 100, 2)}
        snap = {
            "ts": datetime.now().strftime("%Y-%m-%dT%H:%M"), "px": spot,
            "r": round(r, 2), "r_window": "3%",
            "r_note": "manual +%.2f%% / -%.2f%%" % (up_pct, dn_pct),
            "up_px": a.up_px, "up_short_m": a.up_short_m,
            "dn_px": a.dn_px, "dn_long_m": a.dn_long_m,
            "pos": pos, "manual": True,
        }
        jl = ROOT / "data" / "market_snapshot.jsonl"
        with open(jl, "a", encoding="utf-8") as f:
            f.write(json.dumps(snap, ensure_ascii=False) + "\n")
        print("已追加 market_snapshot.jsonl")
    except Exception as e:
        print("snapshot 追加失败:", e)

    esc = ROOT / "data" / "map_escalation.json"
    if esc.exists():
        try:
            esc.unlink()
            print("已清除 map_escalation.json")
        except Exception:
            pass


if __name__ == "__main__":
    main()
