"""本地全自动取数 v2 —— CDP 直连 Chrome-自动化 (9222) + 本地 VLM

设计目标：图片永不进入 Agent 上下文。Agent 只读文本 JSON。

流程：
  1. 清算地图：截图 -> 本地 Qwen2.5-VL 读数 -> 得 现价/上方空头/下方多头
     （hover 位置用 VLM 反馈迭代校准，保证落在 2.5%~3.5%）
  2. 多空比：CDP Runtime.evaluate 直接抽表格
  3. X 推特：滚动加载 + 精确解析互动数 -> x_posts.json
  4. 全部写入 data/coinglass.json / data/x_posts.json / data/market_snapshot.jsonl

用法：
  python local_fetch.py                # 全量（地图 + LSR + X）
  python local_fetch.py --no-map       # 跳过地图（只取 LSR + X）
  python local_fetch.py --no-x         # 跳过 X
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(r"D:\DP workspace\btc_system")
sys.path.insert(0, str(ROOT))
sys.path.insert(0, r"E:\btc-ai")

from PIL import Image  # noqa: E402
from core import cdp  # noqa: E402
import vl_coin      # noqa: E402  (E:\btc-ai\vl_coin.py)

SNAP_BASE = Path(r"E:\btc-ai\snapshots")

MAP_URL = "https://www.coinglass.com/pro/futures/LiquidationMap"
LSR_URL = "https://www.coinglass.com/LongShortRatio"
X_PRO = ("https://x.com/search?q=%28from%3AColeMacro%20OR%20from%3AKillaXBT"
         "%20OR%20from%3Awoonomic%20OR%20from%3ARaoulGMI"
         "%20OR%20from%3ANickTimiraos%29%20BTC&f=live")
X_RETAIL = "https://x.com/search?q=%24BTC%20lang%3Aen%20min_faves%3A1500&f=live"

# ---------------- JS 片段 ----------------

JS_LSR = r"""(() => {
  const out = {};
  const rows = [];
  document.querySelectorAll('table tr').forEach(tr => {
    const c = [...tr.querySelectorAll('td')].map(td => td.innerText.replace(/\s+/g,' ').trim());
    if (c.length >= 3 && c[0]) rows.push(c.slice(0,3));
  });
  out.rows = rows;
  const t = document.body.innerText;
  const poll = {};
  ['Very Bullish','Bullish','Neutral','Bearish','Very Bearish'].forEach(k => {
    const mm = t.match(new RegExp(k + '\\s+(\\d+)%'));
    if (mm) poll[k] = parseInt(mm[1]);
  });
  out.poll = poll;
  return JSON.stringify(out);
})()"""

# X：滚动 3 次加载更多，用 aria-label 解析互动数
JS_SCROLL = r"""(() => { window.scrollBy(0, 2200); return 1; })()"""

JS_X = r"""(() => {
  const arts = [];
  document.querySelectorAll('article').forEach(a => {
    const txt = (a.innerText || '').replace(/\s+/g, ' ').trim();
    if (txt.length < 40) return;
    const meta = {};
    a.querySelectorAll('[aria-label]').forEach(el => {
      const lb = el.getAttribute('aria-label') || '';
      const m = lb.match(/^(\d[\d,.]*[KMB]?)\s+(repl|likes?|reposts?|views?)/i);
      if (m) meta[m[2].toLowerCase()] = m[1];
    });
    const timeEl = a.querySelector('time');
    const link = a.querySelector('a[href*="/status/"]');
    arts.push({
      txt: txt.slice(0, 300),
      ts: timeEl ? (timeEl.getAttribute('datetime') || '') : '',
      likes: meta.likes || meta.like || '',
      reposts: meta.reposts || meta.repost || '',
      replies: meta.repl || '',
      views: meta.views || meta.view || '',
      url: link ? link.href : ''
    });
  });
  return JSON.stringify(arts.slice(0, 8));
})()"""

JS_X_RETRY = r"""(() => {
  if (/出错了|Try reloading/i.test(document.body.innerText)) {
    const els = [...document.querySelectorAll('div[role="button"], span, button')];
    const b = els.find(e => (e.innerText||'').trim() === '重试');
    if (b) { b.click(); return 'clicked'; }
  }
  return 'no-error';
})()"""

JS_MAP_READY = r"""(() => {
  const cv = document.querySelector('canvas');
  if (!cv) return JSON.stringify({ok:false, why:'no canvas'});
  const b = cv.getBoundingClientRect();
  const t = document.body.innerText;
  const m = t.match(/Current Price[:：]\s*([\d,.]+)/);
  return JSON.stringify({ok:true, left:b.left, top:b.top, w:b.width, h:b.height,
                         price: m ? parseFloat(m[1].replace(/,/g,'')) : null});
})()"""


def _hard_reload(pg: cdp.Page, wait: float = 4.5) -> None:
    """强制绕过缓存刷新页面（避免读到旧 DOM / 旧图表数据）。"""
    try:
        pg.call("Network.enable", {})
        pg.call("Network.setCacheDisabled", {"cacheDisabled": True})
    except Exception:
        pass
    try:
        pg.call("Page.reload", {"ignoreCache": True})
    except Exception:
        pass
    time.sleep(wait)


def now_ts():
    return datetime.now().strftime("%Y-%m-%dT%H:%M")


def fmt_int(v):
    try:
        return int(round(float(v)))
    except Exception:
        return 0


# ---------------- 清算地图 ----------------

def _hover_at(pg: cdp.Page, x: int, y: int):
    """用 CDP 发真实鼠标事件（trusted），触发 canvas tooltip。"""
    pg.call("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y,
                                         "button": "none", "buttons": 0})
    time.sleep(0.45)


def _crop_tooltip(src: str, dst: str, cx: int, cy: int,
                  half_w: int = 600, half_h: int = 150) -> str:
    """裁出 hover 点附近区域供 VLM 精读。

    实测：上侧 hover 时 tooltip 出现在光标左侧，下侧 hover 时在右侧，
    所以以光标为中心左右各取 half_w，确保两种情况都覆盖。
    """
    try:
        img = Image.open(src)
        left = max(0, cx - half_w)
        top = max(0, cy - half_h)
        right = min(img.width, cx + half_w)
        bottom = min(img.height, cy + half_h)
        img.crop((left, top, right, bottom)).save(dst, quality=92)
        return dst
    except Exception:
        return src


def _vlm_read(path: str, kind: str) -> dict:
    try:
        res, _raw = vl_coin.ask(path, kind)
        return res
    except Exception as e:
        return {"error": str(e)}


def fetch_map() -> dict:
    """返回 {spot, upper:{px,short}, lower:{px,long}, shots:[...]}"""
    snapdir = SNAP_BASE / datetime.now().strftime("%H%M")
    snapdir.mkdir(parents=True, exist_ok=True)

    pg = cdp.open_page(MAP_URL)
    out = {"ok": False, "shots": []}
    try:
        pg.goto(MAP_URL, wait=4.0)
        _hard_reload(pg, 6.0)
        info = json.loads(pg.eval_js(JS_MAP_READY) or "{}")
        if not info.get("ok"):
            out["why"] = info.get("why", "map not ready")
            return out

        cv_left = info["left"]
        cv_top = info["top"]
        cv_w = info["w"]
        cv_h = info["h"]
        mid_y = int(cv_top + cv_h * 0.45)

        # 1) 基础截图 -> VLM 读现价（页面文本读不到时兜底）
        p_base = snapdir / "1_base.jpg"
        pg.screenshot(str(p_base), quality=50)
        out["shots"].append(str(p_base))
        base = _vlm_read(str(p_base), "base")
        spot = base.get("top_current_price") or info.get("price")
        if not spot:
            out["why"] = "no spot price"
            return out
        out["spot"] = spot

        # 2) 现价对应的 x：用图中心估算，再用 VLM 反馈校准
        #    CoinGlass 清算图的现价大致在画布中央偏右，先取 55% 位置
        x_center = int(cv_left + cv_w * 0.50)

        def probe_up(offset_px: int):
            x = min(int(cv_left + cv_w - 8), x_center + offset_px)
            _hover_at(pg, x, mid_y)
            p = snapdir / "2_upper.jpg"
            pg.screenshot(str(p), quality=55)
            cp = snapdir / "2_upper_crop.jpg"
            _crop_tooltip(str(p), str(cp), x, mid_y)
            r = _vlm_read(str(cp), "upper")
            r["_full"] = str(p)
            return r, str(cp)

        def probe_dn(offset_px: int):
            x = max(int(cv_left + 8), x_center - offset_px)
            _hover_at(pg, x, mid_y)
            p = snapdir / "3_lower.jpg"
            pg.screenshot(str(p), quality=55)
            cp = snapdir / "3_lower_crop.jpg"
            _crop_tooltip(str(p), str(cp), x, mid_y)
            r = _vlm_read(str(cp), "lower")
            r["_full"] = str(p)
            return r, str(cp)

        # 初始 offset：经验值 1% ≈ 110px，3% ≈ 330px
        # 读失败时换几个偏移重试（tooltip 位置随光标变化，单点容易打偏）
        off = 330
        up, p_up = probe_up(off)
        up_px = up.get("tooltip_price")
        for _try in (380, 280, 430):
            if up_px:
                break
            off = _try
            up, p_up = probe_up(off)
            up_px = up.get("tooltip_price")
        if not up_px:
            out["why"] = "upper read failed (tried 330/380/280/430)"
            return out

        # 校准：目标 3%，按实际百分比修正一次
        def refine(px_actual, px_spot, offset):
            pct = (px_actual - px_spot) / px_spot * 100
            if pct <= 0:
                return offset
            return int(offset * 3.0 / pct)

        new_off = refine(up_px, spot, off)
        if abs(new_off - off) > 12:
            off = max(120, min(520, new_off))
            up, p_up = probe_up(off)
            up_px = up.get("tooltip_price")

        dn, p_dn = probe_dn(off)
        dn_px = dn.get("tooltip_price")
        # 下方累计值偶尔漏读 -> 换个偏移再试一次
        if not dn.get("cumulative_long_m"):
            dn2, p_dn2 = probe_dn(max(200, off - 60))
            if dn2.get("cumulative_long_m"):
                dn, p_dn = dn2, p_dn2
                dn_px = dn2.get("tooltip_price")
            elif not dn.get("cumulative_long_m"):
                # 再换一个方向
                dn3, p_dn3 = probe_dn(off + 60)
                if dn3.get("cumulative_long_m"):
                    dn, p_dn = dn3, p_dn3
                    dn_px = dn3.get("tooltip_price")

        if not up_px or not dn_px:
            out["why"] = "tooltip read failed"
            return out

        # ---- 有效性校验（防止脏数据进入 L2 打分）----
        _up_pct = (up_px - spot) / spot * 100
        _dn_pct = (spot - dn_px) / spot * 100
        _sm = up.get("cumulative_short_m")
        _lm = dn.get("cumulative_long_m")
        _err = None
        if not (2.0 <= _up_pct <= 5.0):
            _err = "upper pct %.2f%% out of 2~5%%" % _up_pct
        elif not (2.0 <= _dn_pct <= 5.0):
            _err = "lower pct %.2f%% out of 2~5%%" % _dn_pct
        elif abs(_up_pct - _dn_pct) > 1.8:
            _err = "asymmetric window %.2f%% vs %.2f%%" % (_up_pct, _dn_pct)
        elif not _sm or not _lm or _sm <= 0 or _lm <= 0:
            _err = "cumulative missing short=%s long=%s" % (_sm, _lm)
        else:
            _r = _sm / _lm
            if not (0.2 <= _r <= 5.0):
                _err = "R=%.2f implausible" % _r
        if _err:
            out["why"] = "validation failed: " + _err
            out["raw"] = {"up_pct": round(_up_pct, 2), "dn_pct": round(_dn_pct, 2),
                          "short_m": _sm, "long_m": _lm}
            return out

        out.update({
            "ok": True,
            "upper": {"px": up_px, "short_m": up.get("cumulative_short_m"),
                      "pct": round((up_px - spot) / spot * 100, 2)},
            "lower": {"px": dn_px, "long_m": dn.get("cumulative_long_m"),
                      "pct": round((spot - dn_px) / spot * 100, 2)},
        })
        out["shots"] = [str(p_base), p_up, p_dn]
        return out
    finally:
        pg.close()


# ---------------- 多空比 ----------------

def fetch_lsr() -> dict:
    pg = cdp.open_page(LSR_URL)
    try:
        pg.goto(LSR_URL, wait=3.0)
        _hard_reload(pg, 4.0)
        for _ in range(4):
            raw = pg.eval_js(JS_LSR)
            if raw:
                d = json.loads(raw)
                if d.get("rows"):
                    return d
            time.sleep(2.5)
        return {}
    finally:
        pg.close()


def parse_lsr(d: dict) -> dict:
    rows = d.get("rows") or []
    out = {}
    for name, off in (("binance", 0), ("okx", 4), ("bybit", 8)):
        rec = {}
        for r in rows[off:off + 4]:
            if not r or not r[0]:
                continue
            k, v, s = r[0].strip(), (r[1] or "").strip(), (r[2] or "").strip()
            if k == "Retail" and v:
                rec["retail"] = float(v)
            elif k == "Whale Account" and v:
                rec["whale_account"] = float(v)
            elif k == "Whale Position" and v:
                rec["whale_position"] = float(v)
            elif k == "Smart Money Sentiment":
                rec["smart_money"] = s
        if rec.get("whale_position"):
            wp = rec["whale_position"]
            rec["whale_bias"] = wp
            rec["whale_bias_long_pct"] = round(wp / (1 + wp) * 100, 2)
        if rec:
            out[name] = rec
    if d.get("poll"):
        out["_poll"] = d["poll"]
    return out


# ---------------- X ----------------

def fetch_x(url: str, tier: str) -> list[dict]:
    pg = cdp.open_page(url)
    try:
        pg.goto(url, wait=3.0)
        _hard_reload(pg, 8.0)
        arr = []
        for attempt in range(4):
            # 先处理「出错了」
            st = pg.eval_js(JS_X_RETRY)
            time.sleep(2.5 if attempt else 1.0)
            raw = pg.eval_js(JS_X)
            arr = json.loads(raw) if raw else []
            if arr:
                # 滚一次再抓，拿更多
                pg.eval_js(JS_SCROLL)
                time.sleep(2.0)
                raw2 = pg.eval_js(JS_X)
                arr2 = json.loads(raw2) if raw2 else []
                if len(arr2) > len(arr):
                    arr = arr2
                break
            time.sleep(2.5)
        posts = []
        for a in arr:
            txt = a.get("txt", "")
            user = ""
            m = re.match(r"^(.{1,40}?)\s+@([A-Za-z0-9_]+)", txt)
            if m:
                user = m.group(1).strip()
            posts.append({"tier": tier, "user": user, "text": txt,
                          "likes": a.get("likes", ""),
                          "reposts": a.get("reposts", ""),
                          "replies": a.get("replies", ""),
                          "views": a.get("views", ""),
                          "ts": a.get("ts", ""), "url": a.get("url", "")})
        return posts
    finally:
        pg.close()


# ---------------- 主流程 ----------------

def main():
    args = sys.argv[1:]
    do_map = "--no-map" not in args
    do_x = "--no-x" not in args

    result = {"ts": now_ts(), "map": None, "lsr": None, "x_pro": [], "x_retail": []}

    if do_map:
        print("=== 清算地图 ===")
        try:
            m = fetch_map()
            result["map"] = m
            if m.get("ok"):
                print(f"  spot={m['spot']} upper=+{m['upper']['pct']}%@{m['upper']['px']}"
                      f"({m['upper']['short_m']}M) lower=-{m['lower']['pct']}%@{m['lower']['px']}"
                      f"({m['lower']['long_m']}M)")
            else:
                print("  失败:", m.get("why"))
        except Exception as e:
            print("  异常:", e)

    print("=== 多空比 ===")
    try:
        parsed = parse_lsr(fetch_lsr())
        result["lsr"] = parsed
        for k in ("binance", "okx", "bybit"):
            if k in parsed:
                r = parsed[k]
                print(f"  {k}: retail={r.get('retail')} whaleAcct={r.get('whale_account')} "
                      f"whalePos={r.get('whale_position')} smart={r.get('smart_money')}")
    except Exception as e:
        print("  失败:", e)

    if do_x:
        print("=== X 专业 ===")
        try:
            result["x_pro"] = fetch_x(X_PRO, "pro")
            print(f"  {len(result['x_pro'])} 条")
            for p in result["x_pro"][:2]:
                print("   -", p["text"][:90])
        except Exception as e:
            print("  失败:", e)
        print("=== X 散户 ===")
        try:
            result["x_retail"] = fetch_x(X_RETAIL, "retail")
            print(f"  {len(result['x_retail'])} 条")
        except Exception as e:
            print("  失败:", e)

    # ---- 写 coinglass.json ----
    cg = ROOT / "data" / "coinglass.json"
    try:
        d = json.load(open(cg, encoding="utf-8"))
    except Exception:
        d = {}
    d["captured_at"] = int(time.time() * 1000)
    d["source"] = "local_fetch.py via CDP 9222"

    m = result.get("map")
    if m and m.get("ok"):
        up, dn, spot = m["upper"], m["lower"], m["spot"]
        d["binance_liqmap_1d"] = {
            "spot": spot,
            "near": {"pct": round((up["pct"] + dn["pct"]) / 2, 2),
                     "up_px": up["px"], "short": fmt_int((up["short_m"] or 0) * 1e6),
                     "dn_px": dn["px"], "long": fmt_int((dn["long_m"] or 0) * 1e6)},
            "far": {"pct": up["pct"], "up_px": up["px"],
                    "short": fmt_int((up["short_m"] or 0) * 1e6),
                    "dn_px": dn["px"], "long": fmt_int((dn["long_m"] or 0) * 1e6)},
            "points": [{"px": up["px"], "short": fmt_int((up["short_m"] or 0) * 1e6)},
                       {"px": dn["px"], "long": fmt_int((dn["long_m"] or 0) * 1e6)}],
            "method": f"local_fetch 实测 +{up['pct']}% / -{dn['pct']}%",
            "axis_note": "x轴非线性，hover 后按 tooltip 真实价格反算百分比",
        }

    lsr = result.get("lsr") or {}
    if any(k in lsr for k in ("binance", "okx", "bybit")):
        d["lsr_by_exchange"] = {k: v for k, v in lsr.items() if not k.startswith("_")}
    if "_poll" in lsr:
        d["sentiment_poll"] = lsr["_poll"]

    json.dump(d, open(cg, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"  -> {cg}")

    # ---- 写 x_posts.json ----
    posts = result["x_pro"] + result["x_retail"]
    if posts:
        xp = ROOT / "data" / "x_posts.json"
        payload = []
        for p in posts:
            payload.append({
                "tier": p["tier"], "user": p["user"] or "unknown",
                "age": p["ts"][:16] if p["ts"] else "",
                "likes": p["likes"], "views": p["views"],
                "text": p["text"][:220], "text_cn": "",
            })
        json.dump({"captured_at": int(time.time() * 1000),
                   "note": "local_fetch.py via CDP", "posts": payload},
                  open(xp, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print(f"  -> {xp} ({len(payload)} 条)")

    # ---- 追加 market_snapshot.jsonl ----
    if m and m.get("ok"):
        try:
            from core import journal
            st = journal.load_state()
            pos = st.get_pos("short") if "short" in st.open_sides() else None
            up, dn = m["upper"], m["lower"]
            sm, lm = up.get("short_m"), dn.get("long_m")
            if not sm or not lm:
                raise ValueError("cumulative missing: short=%s long=%s" % (sm, lm))
            r_val = round(sm / lm, 2)
            if not (0.2 <= r_val <= 5.0):
                raise ValueError("R=%.2f implausible, refuse to write" % r_val)
            snap = {
                "ts": result["ts"], "px": m["spot"], "r": r_val,
                "r_window": "3%",
                "r_note": f"+{up['pct']}% / -{dn['pct']}%",
                "up_px": up["px"], "up_short_m": up["short_m"],
                "dn_px": dn["px"], "dn_long_m": dn["long_m"],
                "whale_long_pct": (lsr.get("binance") or {}).get("whale_bias_long_pct"),
                "smart": (lsr.get("binance") or {}).get("smart_money"),
                "pos": ({"side": pos.side, "entry": pos.entry, "size": pos.size,
                         "sl": pos.stop,
                         "tp1": pos.targets[0] if pos.targets else 0,
                         "upl_pct": round((pos.entry - m["spot"]) / pos.entry * 100, 2)}
                        if pos else None),
            }
            jl = ROOT / "data" / "market_snapshot.jsonl"
            with open(jl, "a", encoding="utf-8") as f:
                f.write(json.dumps(snap, ensure_ascii=False) + "\n")
            print(f"  -> market_snapshot.jsonl  R={r_val}")
        except Exception as e:
            print("  snapshot 失败:", e)

    # ---- 结果也存一份完整 JSON ----
    outp = ROOT / "data" / "local_fetch_last.json"
    json.dump(result, open(outp, "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    print(f"  -> {outp}")


if __name__ == "__main__":
    main()
