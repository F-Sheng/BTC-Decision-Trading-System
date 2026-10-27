import json
import sys
import time

sys.path.insert(0, r"D:\DP workspace\btc_system")
from core import cdp

URL = "https://thekingfisher.io/bitcoin-liquidations-map"

pg = cdp.open_page("https://thekingfisher.io/")
try:
    pg.goto(URL, wait=12)
    time.sleep(8)
    print("title:", pg.eval_js("document.title"))
    info = pg.eval_js(r"""(() => {
      const out = {
        canvas: document.querySelectorAll('canvas').length,
        svg: document.querySelectorAll('svg').length,
        iframe: document.querySelectorAll('iframe').length,
        rects: document.querySelectorAll('rect').length,
        paths: document.querySelectorAll('path').length,
        bodyLen: document.body.innerText.length,
        hasLogin: /sign in|log in|登录/i.test(document.body.innerText),
      };
      // 找可能的图表容器
      out.bigEls = [...document.querySelectorAll('div,section')]
        .filter(e => { const r = e.getBoundingClientRect();
                       return r.width > 500 && r.height > 250; })
        .slice(0, 8)
        .map(e => ({tag: e.tagName, cls: (e.className||'').toString().slice(0,50),
                    w: Math.round(e.getBoundingClientRect().width),
                    h: Math.round(e.getBoundingClientRect().height),
                    txt: (e.innerText||'').replace(/\s+/g,' ').slice(0,60)}));
      return JSON.stringify(out);
    })()""")
    print(json.dumps(json.loads(info), ensure_ascii=False, indent=1)[:2500])
    print()
    print("=== 页面文本前 800 字 ===")
    txt = pg.eval_js("document.body.innerText.slice(0,800)")
    print(txt)
finally:
    pg.close()
