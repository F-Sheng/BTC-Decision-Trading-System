"""用「无 hover / 有 hover」两张截图做差分，精确定位 tooltip 矩形。

为什么要这么做：
  之前靠猜测 hover 位置 + 固定偏移裁剪，结果裁剪区把右侧 y 轴刻度也包进去了，
  VLM 会把 y 轴上的 "757.94M" 当成累计值（实测读成 78359），把价格读成 410310。
  差分法能精确框出 tooltip 那块变化的区域，天然排除坐标轴。
"""

from __future__ import annotations

import json
import sys

sys.path.insert(0, r"D:\DP workspace\btc_system")
sys.path.insert(0, r"E:\btc-ai")

import numpy as np
from PIL import Image

from core import cdp

MAP = "https://www.coinglass.com/pro/futures/LiquidationMap"
OUT = r"E:\btc-ai\snapshots\_diff_test"


def diff_tooltip_box(base_path: str, hover_path: str, cx: int, cy: int,
                     near_px: int = 760, min_run: int = 120) -> tuple | None:
    """返回 tooltip 的 (left, top, right, bottom)，失败返回 None。"""
    a = np.asarray(Image.open(base_path).convert("L"), dtype=np.int16)
    b = np.asarray(Image.open(hover_path).convert("L"), dtype=np.int16)
    if a.shape != b.shape:
        return None
    h, w = a.shape
    d = np.abs(a - b)
    # 只在光标附近找（避免图例、闪烁光标干扰）
    x0 = max(0, cx - near_px)
    x1 = min(w, cx + near_px)
    y0 = max(0, cy - 260)
    y1 = min(h, cy + 260)
    sub = d[y0:y1, x0:x1]
    mask = sub > 18

    # 逐行统计变化像素数：tooltip 行会有 ~200+ 个变化像素，十字线只有 1-2 个
    rowcnt = mask.sum(axis=1)
    rows = np.where(rowcnt >= min_run)[0]
    if len(rows) < 8:
        return None
    top = y0 + int(rows.min())
    bot = y0 + int(rows.max()) + 1

    band = mask[rows.min():rows.max() + 1, :]
    colcnt = band.sum(axis=0)
    cols = np.where(colcnt >= max(6, band.shape[0] // 3))[0]
    if len(cols) < 20:
        return None
    left = x0 + int(cols.min())
    right = x0 + int(cols.max()) + 1
    if right - left < 80 or bot - top < 30:
        return None
    pad = 8
    return (max(0, left - pad), max(0, top - pad),
            min(w, right + pad), min(h, bot + pad))


def main():
    import os
    os.makedirs(OUT, exist_ok=True)
    pg = cdp.open_page(MAP)
    try:
        pg.goto(MAP, wait=4.0)
        pg.call("Network.enable", {})
        pg.call("Network.setCacheDisabled", {"cacheDisabled": True})
        pg.call("Page.reload", {"ignoreCache": True})
        import time
        time.sleep(6)

        info = json.loads(pg.eval_js("""(() => {
          const cv = document.querySelector('canvas');
          const b = cv.getBoundingClientRect();
          return JSON.stringify({left:b.left, top:b.top, w:b.width, h:b.height});
        })()"""))
        cl, ct, cw, ch = info["left"], info["top"], info["w"], info["h"]
        mid_y = int(ct + ch * 0.45)

        # 1) 无 hover 基准图（鼠标移到图表外）
        pg.call("Input.dispatchMouseEvent",
                {"type": "mouseMoved", "x": 5, "y": 5, "button": "none", "buttons": 0})
        time.sleep(0.6)
        base = os.path.join(OUT, "base.jpg")
        pg.screenshot(base, quality=60)

        # 2) hover 到上方 ~3%
        x = int(cl + cw * 0.92)
        pg.call("Input.dispatchMouseEvent",
                {"type": "mouseMoved", "x": x, "y": mid_y, "button": "none", "buttons": 0})
        time.sleep(0.7)
        hov = os.path.join(OUT, "hover.jpg")
        pg.screenshot(hov, quality=60)

        box = diff_tooltip_box(base, hov, x, mid_y)
        print("hover x =", x, " canvas =", (cl, ct, cw, ch))
        print("tooltip box =", box)
        if not box:
            print("差分定位失败")
            return

        crop = os.path.join(OUT, "crop.jpg")
        img = Image.open(hov)
        img.crop(box).save(crop, quality=95)
        print("裁剪尺寸 =", img.crop(box).size, "->", crop)

        import vl_coin
        res, raw = vl_coin.ask(crop, "upper")
        print("VLM =", json.dumps(res, ensure_ascii=False))
        print("raw =", raw[:300])
    finally:
        pg.close()


if __name__ == "__main__":
    main()
