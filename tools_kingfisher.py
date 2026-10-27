"""从 Kingfisher 清算地图页面 DOM 直接读关键价位（无需读图）

来源：https://thekingfisher.io/liquidations-map （用户 2026-10-28 提供）
特点：
  · 不需要登录
  · 数据是 Binance 高杠杆
  · DOM 里直接有文字：Strongest zone below / Price at snapshot / Strongest zone above
  · 累计金额在 canvas 里取不到，所以只能拿「最强清算价位」

价值：
  · 100% 稳定，零 VLM 幻觉风险，约 15 秒
  · 可替代「关键位」来源（比机器人自算 PROXY 更贴真实清算簇）
  · 页面自带更新时间，可判断数据新鲜度

局限：
  · 没有累计金额 → 算不出 R 值（燃料比）
  · 地图有约 15-20 分钟延迟
"""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(r"D:\DP workspace\btc_system")
sys.path.insert(0, str(ROOT))

OUT = ROOT / "data" / "kingfisher.json"
URL = "https://thekingfisher.io/liquidations-map"


def fetch() -> dict:
    from core import cdp

    pg = cdp.open_page("https://thekingfisher.io/")
    try:
        pg.goto(URL, wait=12)
        time.sleep(8)
        txt = pg.eval_js("document.body.innerText") or ""
    finally:
        pg.close()

    d = {"ok": False, "url": URL,
         "ts": time.strftime("%Y-%m-%d %H:%M:%S")}

    def grab(label: str):
        m = re.search(label + r"\s*\$?([\d,]+(?:\.\d+)?)", txt)
        if not m:
            return None
        try:
            return float(m.group(1).replace(",", ""))
        except Exception:
            return None

    d["zone_below"] = grab(r"Strongest zone below")
    d["snapshot_px"] = grab(r"Price at snapshot")
    d["zone_above"] = grab(r"Strongest zone above")

    # 数据更新时间与新鲜度
    m = re.search(r"Map updated ([A-Za-z]{3} \d{1,2}, [\d:]+ UTC)", txt)
    d["updated"] = m.group(1) if m else None
    m2 = re.search(r"(\d+)\s*(minutes?|小时|hours?)\s*ago", txt)
    d["age_min"] = int(m2.group(1)) if m2 else None

    d["stale_warning"] = "may be out of date" in txt.lower()

    # 距离百分比
    sp = d.get("snapshot_px")
    if sp and d.get("zone_below"):
        d["below_pct"] = round((sp - d["zone_below"]) / sp * 100, 3)
    if sp and d.get("zone_above"):
        d["above_pct"] = round((d["zone_above"] - sp) / sp * 100, 3)

    d["ok"] = bool(sp and (d.get("zone_above") or d.get("zone_below")))
    if not d["ok"]:
        d["why"] = "DOM 里没找到三个价位，可能页面结构变化"
        d["raw_head"] = txt[:300]
    return d


def main():
    d = fetch()
    try:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass

    if "--json" in sys.argv:
        print(json.dumps(d, ensure_ascii=False, indent=1))
        return 0 if d.get("ok") else 1

    if not d.get("ok"):
        print("Kingfisher 读取失败:", d.get("why"))
        return 1
    print("=== Kingfisher 清算地图（DOM 直读，无读图）===")
    print("  Strongest zone below  %.1f  (%+.3f%%)"
          % (d["zone_below"], -d.get("below_pct", 0) if d.get("zone_below") else 0)
          if d.get("zone_below") else "  Strongest zone below  无")
    print("  Price at snapshot     %.1f" % d["snapshot_px"])
    if d.get("zone_above"):
        print("  Strongest zone above  %.1f  (%+.3f%%)"
              % (d["zone_above"], d.get("above_pct", 0)))
    print("  数据更新: %s（%s 分钟前）%s"
          % (d.get("updated"), d.get("age_min"),
             " ⚠ 页面提示可能过期" if d.get("stale_warning") else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
