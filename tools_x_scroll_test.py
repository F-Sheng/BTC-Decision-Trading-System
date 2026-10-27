"""用真实键盘/滚轮事件让 X 时间线加载更多（用户建议模拟键盘）

之前用 window.scrollBy 和容器 scrollTop 都无效（X 是虚拟列表）。
本脚本试三种真实输入：
  1. Input.dispatchKeyEvent  PageDown
  2. Input.dispatchMouseEvent type=mouseWheel（真实滚轮）
  3. 两者交替
"""

import json
import sys
import time

sys.path.insert(0, r"D:\DP workspace\btc_system")
from core import cdp

JS_X = r"""(() => {
  const arts = [];
  document.querySelectorAll('article').forEach(a => {
    const txt = (a.innerText || '').replace(/\s+/g, ' ').trim();
    if (txt.length < 30) return;
    const t = a.querySelector('time');
    const link = a.querySelector('a[href*="/status/"]');
    arts.push({txt: txt.slice(0, 200), ts: t ? t.getAttribute('datetime') : '',
               url: link ? link.href : ''});
  });
  return JSON.stringify(arts.slice(0, 60));
})()"""

JS_COUNT = "document.querySelectorAll('article').length"


def wheel(pg, dy=1200, times=1):
    for _ in range(times):
        pg.call("Input.dispatchMouseEvent", {
            "type": "mouseWheel", "x": 700, "y": 500,
            "deltaX": 0, "deltaY": dy,
            "pointerType": "mouse", "button": "none", "buttons": 0,
        })
        time.sleep(1.2)


def pagedown(pg, times=1):
    for _ in range(times):
        # keyDown + keyUp，带正确的 windowsVirtualKeyCode
        pg.call("Input.dispatchKeyEvent", {
            "type": "rawKeyDown", "windowsVirtualKeyCode": 34,
            "nativeVirtualKeyCode": 34, "key": "PageDown", "code": "PageDown",
        })
        pg.call("Input.dispatchKeyEvent", {
            "type": "keyUp", "windowsVirtualKeyCode": 34,
            "nativeVirtualKeyCode": 34, "key": "PageDown", "code": "PageDown",
        })
        time.sleep(1.2)


def main():
    pg = cdp.open_page("https://x.com/home")
    try:
        pg.goto("https://x.com/home", wait=12)
        time.sleep(6)
        # 切到「正在关注」
        r = pg.eval_js(r"""(() => {
          const tabs = [...document.querySelectorAll('[role="tab"]')];
          for (const t of tabs) {
            const s = (t.innerText || '').trim();
            if (s === '正在关注' || s === 'Following') { t.click(); return 'clicked:' + s; }
          }
          return 'not-found';
        })()""")
        print("  切 tab:", r)
        time.sleep(5)

        print("  初始 article 数:", pg.eval_js(JS_COUNT))
        print()
        print("=== 试 1：鼠标滚轮 ===")
        for i in range(3):
            wheel(pg, 1400, 1)
            n = pg.eval_js(JS_COUNT)
            print("    滚 %d 次后: %s 条" % (i + 1, n))

        print()
        print("=== 试 2：PageDown ===")
        for i in range(3):
            pagedown(pg, 1)
            n = pg.eval_js(JS_COUNT)
            print("    PageDown %d 次后: %s 条" % (i + 1, n))

        print()
        print("=== 试 3：滚轮+PageDown 交替 ===")
        for i in range(4):
            wheel(pg, 1800, 1)
            pagedown(pg, 1)
            n = pg.eval_js(JS_COUNT)
            print("    第 %d 轮后: %s 条" % (i + 1, n))

        arr = json.loads(pg.eval_js(JS_X) or "[]")
        print()
        print("=== 最终抓到 %d 条 ===" % len(arr))
        for a in arr[:15]:
            print("    %s" % a["txt"][:90])
    finally:
        pg.close()


if __name__ == "__main__":
    main()
