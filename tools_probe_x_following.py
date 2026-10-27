"""探测 X 关注列表，为优化信息源做准备

思路：用户关注了大量加密/政治博主，现在只硬编码搜 5 个账号，覆盖太窄。
如果能把关注列表拉下来，就能动态生成检索 query，覆盖面大得多。
"""

import json
import sys
import time

sys.path.insert(0, r"D:\DP workspace\btc_system")
from core import cdp

pg = cdp.open_page("https://x.com/home")
try:
    pg.goto("https://x.com/home", wait=10)
    time.sleep(6)

    # 1) 先确认登录态和当前账号
    info = pg.eval_js(r"""(() => {
      const out = {};
      const t = document.body.innerText;
      out.loggedIn = !/Sign in|登录/i.test(t.slice(0, 500));
      // 侧栏 ProfileLink 里有账号名
      const link = document.querySelector('a[data-testid="AppTabBar_Profile_Link"]');
      out.handle = link ? link.getAttribute('href') : null;
      out.bodyHead = t.slice(0, 200).replace(/\s+/g, ' ');
      // 首页时间线条数
      out.articles = document.querySelectorAll('article').length;
      return JSON.stringify(out);
    })()""")
    print("=== 账号状态 ===")
    print(" ", info)

    # 2) 打开 following 页
    d = json.loads(info)
    handle = (d.get("handle") or "").strip("/")
    if not handle:
        print("  拿不到 handle，放弃")
    else:
        url = "https://x.com/%s/following" % handle
        print()
        print("=== 打开关注列表 %s ===" % url)
        pg.goto(url, wait=10)
        time.sleep(6)
        for _ in range(3):
            pg.eval_js("window.scrollBy(0, 2500); 1")
            time.sleep(2.5)
        users = pg.eval_js(r"""(() => {
          const out = [];
          document.querySelectorAll('[data-testid="UserCell"]').forEach(c => {
            const t = (c.innerText || '').replace(/\s+/g, ' ').trim();
            const a = c.querySelector('a[href^="/"]');
            const href = a ? a.getAttribute('href') : '';
            out.push({handle: href.replace('/', ''), text: t.slice(0, 90)});
          });
          return JSON.stringify(out.slice(0, 80));
        })()""")
        lst = json.loads(users)
        print("  抓到关注账号 %d 个:" % len(lst))
        for u in lst[:40]:
            print("    @%-20s %s" % (u["handle"], u["text"][:70]))
finally:
    pg.close()
