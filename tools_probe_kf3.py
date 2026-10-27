import json
import sys
import time

sys.path.insert(0, r"D:\DP workspace\btc_system")
from core import cdp

URL = "https://thekingfisher.io/liquidations-map"

pg = cdp.open_page("https://thekingfisher.io/")
try:
    pg.goto(URL, wait=14)
    time.sleep(10)

    print("=== 精确定位 Cumulative above/below 的数值 ===")
    out = pg.eval_js(r"""(() => {
      const res = [];
      // 找所有含 Cumulative 的叶子元素，取它的父级文本
      document.querySelectorAll('*').forEach(el => {
        if (el.children.length > 0) return;
        const t = (el.textContent || '').trim();
        if (/Cumulative/i.test(t)) {
          const p = el.parentElement;
          res.push({
            label: t,
            parentText: p ? (p.innerText || '').replace(/\s+/g,' ').trim().slice(0,120) : '',
            grandText: (p && p.parentElement)
              ? (p.parentElement.innerText || '').replace(/\s+/g,' ').trim().slice(0,200) : '',
          });
        }
      });
      return JSON.stringify(res.slice(0, 6));
    })()""")
    print(out)

    print()
    print("=== 找页面里所有的 $金额 / M / B 数值 ===")
    out2 = pg.eval_js(r"""(() => {
      const t = document.body.innerText;
      const money = t.match(/\$[\d,.]+[KMB]?/g) || [];
      const big = t.match(/\b[\d.]+[KMB]\b/g) || [];
      return JSON.stringify({money: [...new Set(money)].slice(0,25),
                             big: [...new Set(big)].slice(0,25)});
    })()""")
    print(out2)

    print()
    print("=== 找 Strongest zone 附近结构 ===")
    out3 = pg.eval_js(r"""(() => {
      const res = [];
      document.querySelectorAll('*').forEach(el => {
        if (el.children.length > 0) return;
        const t = (el.textContent || '').trim();
        if (/Strongest zone|Cumulative|Price at snapshot/i.test(t)) {
          let p = el;
          for (let i=0;i<3;i++) p = p.parentElement || p;
          res.push({label: t, box: (p.innerText||'').replace(/\s+/g,' ').slice(0,160)});
        }
      });
      return JSON.stringify(res.slice(0,8));
    })()""")
    print(out3)
finally:
    pg.close()
