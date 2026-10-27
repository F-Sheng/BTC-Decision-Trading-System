"""测算：不用本地 VLM 时，一轮取数需要多久

对比三条路径：
  A. 现状（差分 + 双读/三读 VLM）
  B. 只截图 + 差分定位，不做 VLM 读数（= 若改用 API 或人工读数的最坏情况）
  C. 纯页面加载 + DOM 抽取（LSR + X），完全无截图
"""

import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(r"D:\DP workspace\btc_system")
sys.path.insert(0, str(ROOT))
sys.path.insert(0, r"E:\btc-ai")

import local_fetch as LF
from core import cdp

MAP = LF.MAP_URL
SNAP = Path(r"E:\btc-ai\snapshots") / ("t_" + datetime.now().strftime("%H%M"))
SNAP.mkdir(parents=True, exist_ok=True)

print("=== B: 地图「只加载+截图+差分」，不做 VLM 读数 ===")
t0 = time.time()
pg = cdp.open_page(MAP)
try:
    pg.goto(MAP, wait=4.0)
    t_goto = time.time() - t0
    t1 = time.time()
    pg.call("Network.enable", {})
    pg.call("Network.setCacheDisabled", {"cacheDisabled": True})
    pg.call("Page.reload", {"ignoreCache": True})
    time.sleep(6)
    t_reload = time.time() - t1

    t2 = time.time()
    info = {}
    for _ in range(3):
        info = __import__("json").loads(pg.eval_js(LF.JS_MAP_READY) or "{}")
        if info.get("ok"):
            break
        time.sleep(3)
    t_ready = time.time() - t2
    print("  goto %.1fs / reload %.1fs / 等 canvas %.1fs" % (t_goto, t_reload, t_ready))

    if info.get("ok"):
        cl, ct, cw, ch = info["left"], info["top"], info["w"], info["h"]
        mid_y = int(ct + ch * 0.45)
        # 基准图
        t3 = time.time()
        pg.call("Input.dispatchMouseEvent",
                {"type": "mouseMoved", "x": 5, "y": 5, "button": "none", "buttons": 0})
        time.sleep(0.6)
        base = SNAP / "base.jpg"
        pg.screenshot(str(base), quality=60)
        t_base = time.time() - t3

        # 两次 hover + 截图 + 差分（不含 VLM）
        t4 = time.time()
        for frac in (0.66, 1 - 0.66):
            x = int(cl + cw * frac)
            x = max(int(cl + 10), min(int(cl + cw - 10), x))
            pg.call("Input.dispatchMouseEvent",
                    {"type": "mouseMoved", "x": x, "y": mid_y,
                     "button": "none", "buttons": 0})
            time.sleep(0.7)
            hp = SNAP / ("h_%d.jpg" % int(frac * 100))
            pg.screenshot(str(hp), quality=60)
            box = LF._diff_box(str(base), str(hp), x, mid_y)
            print("    frac=%.2f diff_box=%s" % (frac, box))
        t_hover = time.time() - t4
        print("  基准图 %.1fs / 两次hover+截图+差分 %.1fs" % (t_base, t_hover))
    print("  B 合计: %.1f 秒" % (time.time() - t0))
finally:
    pg.close()

print()
print("=== C: LSR + X（无 VLM）===")
t0 = time.time()
LF.fetch_lsr()
t_lsr = time.time() - t0
t0 = time.time()
LF.fetch_x(LF.X_PRO, "pro")
t_pro = time.time() - t0
t0 = time.time()
LF.fetch_x(LF.X_RETAIL, "retail")
t_ret = time.time() - t0
print("  LSR %.1fs / X专业 %.1fs / X散户 %.1fs  -> 合计 %.1f 秒"
      % (t_lsr, t_pro, t_ret, t_lsr + t_pro + t_ret))
