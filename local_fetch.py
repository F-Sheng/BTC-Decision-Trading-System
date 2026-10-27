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


DIFF_NEAR = 1150
DIFF_MIN_RUN = 120


def _diff_box(base_path: str, hover_path: str, cx: int, cy: int):
    """用「无 hover / 有 hover」两张图差分，精确定位 tooltip 矩形。

    这是关键改进：固定偏移裁剪会把右侧 y 轴刻度包含进去，
    VLM 会把 "757.94M" 当成累计值（实测读成 78359）、把价格读成 410310。
    差分框只覆盖真正变化的 tooltip 区域，天然排除坐标轴。
    """
    try:
        import numpy as np

        a = np.asarray(Image.open(base_path).convert("L"), dtype=np.int16)
        b = np.asarray(Image.open(hover_path).convert("L"), dtype=np.int16)
        if a.shape != b.shape:
            return None
        h, w = a.shape
        d = np.abs(a - b)
        x0, x1 = max(0, cx - DIFF_NEAR), min(w, cx + DIFF_NEAR)
        y0, y1 = max(0, cy - 260), min(h, cy + 260)
        sub = d[y0:y1, x0:x1]
        mask = sub > 18
        rowcnt = mask.sum(axis=1)
        rows = np.where(rowcnt >= DIFF_MIN_RUN)[0]
        if len(rows) < 8:
            return None
        top, bot = y0 + int(rows.min()), y0 + int(rows.max()) + 1
        band = mask[rows.min():rows.max() + 1, :]
        colcnt = band.sum(axis=0)
        cols = np.where(colcnt >= max(6, band.shape[0] // 3))[0]
        if len(cols) < 20:
            return None
        left, right = x0 + int(cols.min()), x0 + int(cols.max()) + 1
        if right - left < 80 or bot - top < 30:
            return None
        pad = 8
        return (max(0, left - pad), max(0, top - pad),
                min(w, right + pad), min(h, bot + pad))
    except Exception:
        return None


def _read_side(pg, base_path, snapdir, tag, x, y, kind):
    """hover -> 截图 -> 差分定位 -> 裁剪 -> VLM 读两次（双读校验）。

    返回 (结果 dict 或 None, 说明)
    """
    pg.call("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y,
                                         "button": "none", "buttons": 0})
    time.sleep(0.7)
    hp = snapdir / ("hover_%s.jpg" % tag)
    pg.screenshot(str(hp), quality=60)
    box = _diff_box(str(base_path), str(hp), x, y)
    if not box:
        return None, "diff locate failed"

    img = Image.open(str(hp))
    # 双读：两种 padding，结果必须一致
    reads = []
    for pad in (8, 14):
        b2 = (max(0, box[0] - pad), max(0, box[1] - pad),
              min(img.width, box[2] + pad), min(img.height, box[3] + pad))
        cp = snapdir / ("crop_%s_%d.jpg" % (tag, pad))
        img.crop(b2).save(str(cp), quality=95)
        r = _vlm_read(str(cp), kind)
        reads.append(r)

    key = "cumulative_short_m" if kind == "upper" else "cumulative_long_m"
    p1, p2 = reads[0].get("tooltip_price"), reads[1].get("tooltip_price")
    c1, c2 = reads[0].get(key), reads[1].get(key)
    if p1 and p2 and p1 == p2 and c1 and c2 and abs(c1 - c2) < 0.5:
        return {"tooltip_price": p1, key: c1}, "dual-read OK"
    # 不一致 -> 第三次
    r3 = _vlm_read(str(snapdir / ("crop_%s_8.jpg" % tag)), kind)
    cands_p = [v for v in (p1, p2, r3.get("tooltip_price")) if v]
    cands_c = [v for v in (c1, c2, r3.get(key)) if v]
    if not cands_p or not cands_c:
        return None, "triple-read empty"
    from collections import Counter
    mp = Counter(cands_p).most_common(1)[0][0]
    mc = Counter([round(v, 2) for v in cands_c]).most_common(1)[0][0]
    agree_p = sum(1 for v in cands_p if v == mp)
    agree_c = sum(1 for v in cands_c if abs(v - mc) < 0.5)
    if agree_p >= 2 and agree_c >= 2:
        return {"tooltip_price": mp, key: mc}, "triple-read majority"
    return None, "INCONSISTENT: px=%s cum=%s" % (cands_p, cands_c)


def fetch_map() -> dict:
    """返回 {spot, upper:{px,short_m,pct}, lower:{px,long_m,pct}, shots:[...]}"""
    snapdir = SNAP_BASE / datetime.now().strftime("%H%M")
    snapdir.mkdir(parents=True, exist_ok=True)

    pg = cdp.open_page(MAP_URL)
    out = {"ok": False, "shots": []}
    try:
        pg.goto(MAP_URL, wait=4.0)
        _hard_reload(pg, 6.0)

        # canvas 未渲染时（VPN 抖动/慢加载）重试，最多 3 次
        info = {}
        for _attempt in range(3):
            info = json.loads(pg.eval_js(JS_MAP_READY) or "{}")
            if info.get("ok"):
                break
            time.sleep(3.5 + _attempt * 2)
            if _attempt < 2:
                try:
                    _hard_reload(pg, 5.0 + _attempt * 2)
                except Exception:
                    pass
        if not info.get("ok"):
            out["why"] = "map not ready after 3 tries: %s" % info.get("why", "no canvas")
            try:
                esc = {
                    "ts": datetime.now().strftime("%Y-%m-%dT%H:%M"),
                    "why": out["why"],
                    "hint": ("图表未渲染，非读数问题。多为网络抖动或 CoinGlass 慢加载；"
                             "可下一轮重试，或人工打开 Chrome-自动化 确认页面正常。"),
                    "action": "no_manual_value_available",
                }
                (ROOT / "data" / "map_escalation.json").write_text(
                    json.dumps(esc, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception:
                pass
            return out
        cl, ct, cw, ch = info["left"], info["top"], info["w"], info["h"]
        mid_y = int(ct + ch * 0.45)

        # 1) 无 hover 基准图（鼠标移出图表）
        pg.call("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": 5, "y": 5,
                                             "button": "none", "buttons": 0})
        time.sleep(0.6)
        base = snapdir / "base.jpg"
        pg.screenshot(str(base), quality=60)
        out["shots"].append(str(base))

        # 现价：页面文本（比 VLM 可靠）
        spot = info.get("price")
        if not spot:
            b = _vlm_read(str(base), "base")
            spot = b.get("top_current_price")
        if not spot:
            out["why"] = "no spot price"
            return out
        out["spot"] = spot

        # 2) 上下各读一个落在目标区的位置（贪心 + 早退）
        #    目标优先 2.6~3.6%（更接近 3%）。
        #    早期版本每侧试 8 档、每档 2-3 次 VLM -> 单轮地图 190 秒；
        #    改成命中目标区立刻返回，最多 4+4 档，预计降到 40~70 秒。
        _FRACS = (0.66, 0.62, 0.71, 0.57, 0.75, 0.53, 0.80, 0.49)

        def _try(sign: int, frac: float):
            x = int(cl + cw * frac) if sign > 0 else int(cl + cw * (1 - frac))
            x = max(int(cl + 10), min(int(cl + cw - 10), x))
            kind = "upper" if sign > 0 else "lower"
            r, why = _read_side(pg, base, snapdir, kind, x, mid_y, kind)
            if not r:
                return None, None, why
            px = r["tooltip_price"]
            pct = (px - spot) / spot * 100 if sign > 0 else (spot - px) / spot * 100
            return r, pct, why

        def pick(sign: int):
            best = (None, None, "no acceptable position")
            for frac in _FRACS[:4]:
                r, pct, why = _try(sign, frac)
                if r is None:
                    continue
                if 2.6 <= pct <= 3.6:
                    return r, pct, why
                if 2.0 <= pct <= 5.0 and best[0] is None:
                    best = (r, pct, why)
            return best

        up, up_pct, up_why = pick(+1)
        dn, dn_pct, dn_why = pick(-1)

        # 两侧差距过大 -> 各补试一档，取更接近对方的那一档
        if up is not None and dn is not None and abs(up_pct - dn_pct) > 0.9:
            for sign in (+1, -1):
                cur_pct = up_pct if sign > 0 else dn_pct
                other_pct = dn_pct if sign > 0 else up_pct
                for frac in _FRACS[4:6]:
                    r, pct, why = _try(sign, frac)
                    if r is None or not (2.0 <= pct <= 5.0):
                        continue
                    if abs(pct - other_pct) < abs(cur_pct - other_pct):
                        if sign > 0:
                            up, up_pct, up_why = r, pct, why
                        else:
                            dn, dn_pct, dn_why = r, pct, why
                        break

        if up is not None and dn is not None:
            gap = abs(up_pct - dn_pct)
            up_why = "%s | gap=%.2fpt" % (up_why, gap)
            dn_why = "%s | gap=%.2fpt" % (dn_why, gap)

        out["up_why"], out["dn_why"] = up_why, dn_why
        if not up or not dn:
            why = "read failed: up=%s(%s) dn=%s(%s)" % (
                bool(up), up_why, bool(dn), dn_why)
            out["why"] = why
            # 升级给 Agent：保存裁剪图路径，由 Agent 亲自看图定数（不进入长期上下文）
            try:
                esc = {
                    "ts": datetime.now().strftime("%Y-%m-%dT%H:%M"),
                    "spot": spot,
                    "why": why,
                    "crops": sorted(str(x) for x in snapdir.glob("crop_*.jpg")),
                    "hovr": sorted(str(x) for x in snapdir.glob("hover_*.jpg")),
                    "base": str(base),
                    "action": ("请 Agent 用 read_image 看对应裁剪图，确认 "
                               "tooltip 价格与累计值，然后执行 "
                               "python tools_write_manual_map.py --spot <> "
                               "--up-px <> --up-short-m <> --dn-px <> --dn-long-m <>"),
                }
                (ROOT / "data" / "map_escalation.json").write_text(
                    json.dumps(esc, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception:
                pass
            return out

        sm = up.get("cumulative_short_m")
        lm = dn.get("cumulative_long_m")
        out.update({
            "ok": True,
            "upper": {"px": up["tooltip_price"], "short_m": sm, "pct": round(up_pct, 2)},
            "lower": {"px": dn["tooltip_price"], "long_m": lm, "pct": round(dn_pct, 2)},
        })
        # 校验
        _err = None
        # 守卫①：累计值恰好等于「价格/100」——VLM 常把 tooltip 里的价格
        # 误当成累计清算值（实测 78,798 -> 787.98M，校验原本拦不住）
        for _lbl, _px, _val in (("upper", up_px, sm), ("lower", dn_px, lm)):
            if _val and _px and abs(_val - _px / 100) < 1.5:
                out["ok"] = False
                out["why"] = ("validation failed: %s 累计值 %.2fM 疑似把价格 %.0f 读错"
                              % (_lbl, _val, _px))
                return out

        # 守卫②：R 与上一轮快照突变 >40% -> 大概率读数错误
        try:
            _jl = ROOT / "data" / "market_snapshot.jsonl"
            _prev_r = None
            if _jl.exists():
                for _ln in reversed(_jl.read_text(encoding="utf-8").splitlines()[-5:]):
                    try:
                        _o = json.loads(_ln)
                        if _o.get("r") and _o.get("r_window") == "3%":
                            _prev_r = float(_o["r"])
                            break
                    except Exception:
                        continue
            if _prev_r and sm and lm:
                _cur_r = sm / lm
                if abs(_cur_r - _prev_r) / _prev_r > 0.40:
                    out["ok"] = False
                    out["why"] = ("validation failed: R 突变 %.2f -> %.2f（>40%%），"
                                  "疑似读数错误" % (_prev_r, _cur_r))
                    return out
        except Exception:
            pass

        if not sm or not lm or sm <= 0 or lm <= 0:
            _err = "cumulative missing short=%s long=%s" % (sm, lm)
        elif abs(up_pct - dn_pct) > 1.8:
            _err = "asymmetric %.2f%% vs %.2f%%" % (up_pct, dn_pct)
        else:
            _r = sm / lm
            if not (0.2 <= _r <= 5.0):
                _err = "R=%.2f implausible" % _r
        if _err:
            out["ok"] = False
            out["why"] = "validation failed: " + _err
            try:
                esc = {
                    "ts": datetime.now().strftime("%Y-%m-%dT%H:%M"),
                    "spot": spot,
                    "why": out["why"],
                    "raw": {"up": out.get("upper"), "lower": out.get("lower")},
                    "crops": sorted(str(x) for x in snapdir.glob("crop_*.jpg")),
                    "action": ("请 Agent 用 read_image 核对裁剪图，然后执行 "
                               "tools_write_manual_map.py 人工写数"),
                }
                (ROOT / "data" / "map_escalation.json").write_text(
                    json.dumps(esc, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception:
                pass
        else:
            # 成功则清除升级标记
            f = ROOT / "data" / "map_escalation.json"
            if f.exists():
                try:
                    f.unlink()
                except Exception:
                    pass
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
    x_only = "--x-only" in args
    result = {"ts": now_ts(), "map": None, "lsr": None,
              "x_pro": [], "x_retail": []}
    if x_only:
        do_map = False
        print("=== X only 模式 ===")
        for u, tier in ((X_PRO, "pro"), (X_RETAIL, "retail")):
            try:
                ps = fetch_x(u, tier)
                print("  %s %d 条" % (tier, len(ps)))
                result["x_" + tier] = ps
            except Exception as e:
                print("  %s 失败: %s" % (tier, e))
        _posts = result["x_pro"] + result["x_retail"]
        if _posts:
            _xp = ROOT / "data" / "x_posts.json"
            _payload = [{"tier": q["tier"], "user": q["user"] or "unknown",
                         "age": q["ts"][:16] if q["ts"] else "",
                         "likes": q["likes"], "views": q["views"],
                         "text": q["text"][:220], "text_cn": ""} for q in _posts]
            json.dump({"captured_at": int(time.time() * 1000),
                       "note": "local_fetch.py --x-only", "posts": _payload},
                      open(_xp, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
            print("  -> %s (%d 条)" % (_xp, len(_payload)))
        return
    do_x = "--no-x" not in args

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
