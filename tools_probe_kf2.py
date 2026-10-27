import json
import re
import sys
import time

sys.path.insert(0, r"D:\DP workspace\btc_system")
from core import cdp

URL = "https://thekingfisher.io/liquidations-map"

pg = cdp.open_page("https://thekingfisher.io/")
try:
    pg.goto(URL, wait=14)
    time.sleep(10)

    print("=== 页面文本（前 1500 字）===")
    print(pg.eval_js("document.body.innerText.slice(0,1500)"))
    print()

    # 找页面里的价格数值（7万-9万之间的 5 位数）
    print("=== 页面中 70k-90k 的价格数值 ===")
    nums = pg.eval_js(r"""(() => {
      const t = document.body.innerText;
      const m = t.match(/\b([7-9]\d,[0-9]{3})\b/g) || [];
      return JSON.stringify([...new Set(m)].slice(0, 20));
    })()""")
    print(" ", nums)

    # 检查 canvas 尺寸与是否有可读取的 JS 数据
    print()
    print("=== canvas / SVG 细节 ===")
    detail = pg.eval_js(r"""(() => {
      const out = {};
      const cv = document.querySelector('canvas');
      if (cv) out.canvasSize = [cv.width, cv.height,
                                Math.round(cv.getBoundingClientRect().width),
                                Math.round(cv.getBoundingClientRect().height)];
      // 收集 SVG 里的文字
      const svgTexts = [];
      document.querySelectorAll('svg text').forEach(t => {
        const s = (t.textContent||'').trim();
        if (s) svgTexts.push(s);
      });
      out.svgTextCount = svgTexts.length;
      out.svgTexts = svgTexts.slice(0, 40);
      // 是否有 window 上的数据对象
      out.winKeys = Object.keys(window).filter(k =>
        /liq|map|chart|data|king|store/i.test(k)).slice(0, 20);
      return JSON.stringify(out);
    })()""")
    d = json.loads(detail)
    print(" canvas:", d.get("canvasSize"))
    print(" SVG 文字数:", d.get("svgTextCount"))
    print(" SVG 文本样本:", d.get("svgTexts", [])[:25])
    print(" window 相关键:", d.get("winKeys"))
finally:
    pg.close()
