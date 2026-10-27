"""读 X「正在关注」时间线（用户截图指出的那个 tab）

用户 2026-10-28 指出：主页有「为你推荐 / 正在关注」两个 tab，
直接读「正在关注」比我跑 4 批账号搜索快得多，而且内容就是用户关注的人。

比多账号搜索的优势：
  · 一次请求拿到所有关注者的最新推文（不需要 25 个账号分批）
  · 不需要维护账号清单
  · 速度快：约 15 秒 vs 4 批约 60 秒
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(r"D:\DP workspace\btc_system")
sys.path.insert(0, str(ROOT))

OUT = ROOT / "data" / "x_posts.json"

JS_FIND_TAB = r"""(() => {
  // 找「正在关注」tab
  const tabs = [...document.querySelectorAll('[role="tab"], a[role="tab"], div[role="presentation"]')];
  const names = ['正在关注', 'Following', '关注'];
  for (const t of tabs) {
    const txt = (t.innerText || '').trim();
    if (names.includes(txt)) {
      t.click();
      return 'clicked:' + txt;
    }
  }
  // 退一步：用文本匹配任意可点元素
  const all = [...document.querySelectorAll('span, div')];
  for (const el of all) {
    const txt = (el.innerText || '').trim();
    if (names.includes(txt) && el.parentElement) {
      el.parentElement.click();
      return 'clicked-parent:' + txt;
    }
  }
  return 'not-found';
})()"""

JS_X = r"""(() => {
  const arts = [];
  document.querySelectorAll('article').forEach(a => {
    const txt = (a.innerText || '').replace(/\s+/g, ' ').trim();
    if (txt.length < 30) return;
    const meta = {};
    a.querySelectorAll('[aria-label]').forEach(el => {
      const lb = el.getAttribute('aria-label') || '';
      const m = lb.match(/^(\d[\d,.]*[KMB]?)\s+(repl|likes?|reposts?|views?)/i);
      if (m) meta[m[2].toLowerCase()] = m[1];
    });
    const t = a.querySelector('time');
    const link = a.querySelector('a[href*="/status/"]');
    arts.push({txt: txt.slice(0, 340), ts: t ? (t.getAttribute('datetime') || '') : '',
               likes: meta.likes || meta.like || '',
               reposts: meta.reposts || meta.repost || '',
               replies: meta.repl || '', views: meta.views || meta.view || '',
               url: link ? link.href : ''});
  });
  return JSON.stringify(arts.slice(0, 40));
})()"""

JS_STATUS = r"""(() => {
  const t = document.body.innerText;
  return JSON.stringify({
    url: location.href,
    err: /Something went wrong|出错了|Try again/i.test(t),
    bodyLen: t.length,
    articles: document.querySelectorAll('article').length,
    tabs: [...document.querySelectorAll('[role="tab"]')].map(e => (e.innerText||'').trim()).slice(0,6)
  });
})()"""


def fetch_following(verbose=True) -> list[dict]:
    from core import cdp

    pg = cdp.open_page("https://x.com/home")
    try:
        pg.goto("https://x.com/home", wait=12)
        time.sleep(6)

        for attempt in range(3):
            st = json.loads(pg.eval_js(JS_STATUS) or "{}")
            if verbose:
                print("  状态:", json.dumps(st, ensure_ascii=False))
            if st.get("err"):
                # 点「重试」
                pg.eval_js("""(() => {
                  const b = [...document.querySelectorAll('div[role="button"],span,button')]
                    .find(e => /重试|Retry|Try again/i.test(e.innerText||''));
                  if (b) b.click();
                  return !!b;
                })()""")
                time.sleep(5)
                continue
            if st.get("articles", 0) > 0:
                break
            time.sleep(4)

        # 切到「正在关注」tab
        r = pg.eval_js(JS_FIND_TAB)
        if verbose:
            print("  切 tab:", r)
        time.sleep(5)
        if verbose:
            st2 = json.loads(pg.eval_js(JS_STATUS) or "{}")
            print("  切换后:", json.dumps(st2, ensure_ascii=False))

        # 滚动加载
        # X 的时间线是虚拟列表，window.scrollBy 无效，必须滚它自己的容器
        for _ in range(6):
            pg.eval_js(r"""(() => {
              const sc = document.scrollingElement || document.documentElement;
              const cand = [sc, document.querySelector('main'), document.querySelector('[data-testid="primaryColumn"]')];
              for (const el of cand) {
                if (el && el.scrollHeight > el.clientHeight + 50) {
                  el.scrollTop += 2000;
                }
              }
              window.scrollBy(0, 2000);
              return 1;
            })()""")
            time.sleep(2.2)

        raw = pg.eval_js(JS_X)
        arr = json.loads(raw) if raw else []
        out = []
        for a in arr:
            txt = a.get("txt", "")
            # 2026-10-28: 过滤广告（截图里能看到「广告」标签）
            if any(k in txt[:80] for k in ("广告", "Promoted", "Sponsored")):
                continue
            # 抽作者：形如 "名字 @handle · 时间"
            who = ""
            for tok in txt.split("·")[0].split():
                if tok.startswith("@"):
                    who = tok[1:]
                    break
            out.append({"tier": "following", "who": who, "text": txt,
                        "likes": a.get("likes", ""), "views": a.get("views", ""),
                        "reposts": a.get("reposts", ""), "replies": a.get("replies", ""),
                        "ts": a.get("ts", ""), "url": a.get("url", "")})
        return out
    finally:
        pg.close()


def main():
    print("=== 读取「正在关注」时间线 ===")
    t0 = time.time()
    try:
        ps = fetch_following()
    except Exception as e:
        print("失败:", str(e)[:120])
        return 1
    dt = time.time() - t0

    print()
    print("  抓到 %d 条（%.1f 秒）" % (len(ps), dt))
    for p in ps[:12]:
        print("   @%-16s %s" % ((p["who"] or "?")[:16], p["text"][:80]))

    if ps:
        payload = [{"tier": p["tier"], "user": p["who"] or "unknown",
                    "age": (p["ts"] or "")[:16], "likes": p["likes"],
                    "views": p["views"], "text": p["text"][:240],
                    "text_cn": "", "url": p["url"]} for p in ps]
        OUT.write_text(json.dumps({
            "captured_at": int(time.time() * 1000),
            "note": "「正在关注」时间线（tools_x_following.py）",
            "count": len(payload), "posts": payload,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        print()
        print("  已写入", OUT)
    else:
        print("  未抓到内容 —— 可能 X 拦截了 home 页面")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
