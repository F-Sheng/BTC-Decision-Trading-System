"""金十快讯 · 改进版：按重要性排序 + 时间窗口 + 存档

改进点（2026-10-27 复盘）：
  原版问题：
    1. 时间倒序输出，噪音（个股公告/辟谣）排在最前，重要新闻被埋在 15 条之后
    2. Agent 每轮用 Select-Object -First 3 截断，只看到前 2 条噪音
    3. 只拉 40 条，15 分钟内新闻多时重要消息滚出窗口
    4. 没有存历史，无法回查"过去 2 小时漏了什么"

  本版：
    · 只输出关键词命中的条目（★），噪音直接丢弃
    · 按"重要度"排序：战争/停火/美联储/中东 > 油价/选举 > 其他
    · 支持 --hours N 只看最近 N 小时
    · 每次运行追加到 data/news_log.jsonl，可回查
    · --all 显示全部，--noise 显示噪音（调试用）

用法：
  python tools_jin10.py              # 最近6小时的重要新闻，按重要度排序
  python tools_jin10.py --hours 2    # 最近2小时
  python tools_jin10.py --all        # 全部条目
"""

import json
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

CST = timezone(timedelta(hours=8))
ROOT = Path(r"D:\DP workspace\btc_system")
LOG = ROOT / "data" / "news_log.jsonl"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36")

# 分级权重：越高越重要（对 BTC 定价影响越大）
WEIGHTS = {
    **EXTRA_KEYWORDS,
    # 地缘战争 —— 油价/避险，最高权重
    "战争": 10, "停火": 10, "停战": 10, "和平": 9, "结束冲突": 9,
    "伊朗": 8, "哈梅内伊": 9, "以色列": 8, "中东": 7, "导弹": 8,
    "打击": 7, "制裁": 7, "霍尔木兹": 9,
    # 美联储/货币
    "美联储": 9, "鲍威尔": 9, "FOMC": 9, "降息": 8, "加息": 8,
    "利率决议": 9, "CPI": 8, "通胀": 7, "非农": 7, "国债拍卖": 6,
    # 政治/选举
    "中期选举": 8, "众议院": 7, "参议院": 7, "特朗普": 7, "关税": 7,
    # 加密
    "比特币": 8, "加密": 7, "ETF": 7, "爆仓": 6, "清算": 5,
    # 能源
    "原油": 7, "OPEC": 7, "油价": 7,
    # 市场
    "纳斯达克": 5, "科技股": 5, "美元": 5, "黄金": 4, "收益率": 5,
}

# 表态类词：出现这些说明只是"说要"，没有实质动作 -> 降权
SOFT_WORDS = ["考虑", "必须", "寻求", "呼吁", "希望", "愿意", "表示", "称",
              "计划", "准备", "拟", "或将", "应该", "需要", "重申", "强调"]
# 实质类词：真正的进展 -> 加权
HARD_WORDS = ["签署", "达成协议", "停火生效", "解除制裁", "正式", "宣布停火",
              "撤军", "重开", "开放霍尔木兹", "协议生效"]

# 2026-10-27 补漏：原来「击中/袭击/无人机/炮击」不在表里，
# 导致「俄罗斯称在黑海击中一艘干散货船」得分 0 被排到最后。
EXTRA_KEYWORDS = {
    "击中": 7, "袭击": 7, "无人机": 6, "炮击": 7, "空袭": 7,
    "黑海": 7, "敖德萨": 7, "哈尔科夫": 7, "基辅": 7, "顿巴斯": 6,
    "委内瑞拉": 6, "石油": 6, "天然气": 6, "海峡": 7,
    "停火": 9, "和平谈判": 8, "谈判": 6, "斡旋": 6,
    "加息": 8, "降息": 8, "缩表": 8, "收益率": 6, "国债": 4,
    "非农": 8, "CPI": 8, "PCE": 8, "GDP": 6,
    "辞职": 6, "弹劾": 7, "战时": 7, "紧急状态": 7,
}

NOISE_WORDS = ["辟谣", "短剧", "ST", "*ST", "个股", "涨停", "跌停",
               "盘初上涨", "盘初下跌", "黄金T+D", "白银T+D", "新闻联播"]


def fetch():
    url = "https://flash-api.jin10.com/get_flash_list?channel=-8200&vip=1"
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "x-app-id": "bVBF4FyRTn5NJF5n",
        "x-version": "1.0.0", "Referer": "https://www.jin10.com/"})
    raw = json.loads(urllib.request.urlopen(req, timeout=15).read().decode())
    return raw.get("data") or []


def parse_time(it):
    """金十的 time 字段格式不稳定：可能是毫秒时间戳，也可能是 'YYYY-MM-DD HH:MM:SS'。"""
    t = it.get("time")
    if isinstance(t, (int, float)):
        return datetime.fromtimestamp(t / 1000, CST)
    s = str(t or "").strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=CST)
        except Exception:
            continue
    return None


def clean(it):
    dd = it.get("data") or {}
    txt = dd.get("content") or dd.get("title") or ""
    txt = "".join(c for c in txt if c not in "<>").replace("&nbsp;", " ").strip()
    return txt


def score(txt):
    s = 0
    hit = []
    for k, w in WEIGHTS.items():
        if k in txt:
            s = max(s, w)
            hit.append(k)
    if s == 0:
        return 0, hit
    soft = [w for w in SOFT_WORDS if w in txt]
    hard = [w for w in HARD_WORDS if w in txt]
    if hard:
        s = min(12, s + 2)
        hit.append("实质进展")
    elif soft:
        # 单方面表态：降权 45%（用户 10-27 指出：没有实质进展的表态权重不应高）
        s = max(1, round(s * 0.55))
        hit.append("仅表态")
    return s, hit


def main():
    args = sys.argv[1:]
    limit = 26   # 2026-10-27: 默认输出 26 条（原来只输出关键词命中的少量，导致漏重大消息）
    hours = 4
    show_all = "--all" in args
    if "--hours" in args:
        i = args.index("--hours")
        try:
            hours = float(args[i + 1])
        except Exception:
            pass

    items = fetch()
    now = datetime.now(CST)
    cutoff = now - timedelta(hours=hours)

    rows = []
    for it in items:
        txt = clean(it)
        # 2026-10-27：原来这里 len<10 直接丢弃，把 type=1 的经济数据快讯
        # （国债竞拍、初请失业金等）全丢了。改为保留，用占位说明。
        if len(txt) < 10:
            _d = it.get("data") or {}
            _name = str(_d.get("name") or _d.get("title") or "").strip()
            _val = str(_d.get("value") or _d.get("actual") or "").strip()
            if _name or _val:
                txt = "[数据] " + _name + (" = " + _val if _val else "")
            else:
                continue
        t = parse_time(it)
        if t is None or t < cutoff:
            continue
        sc, hit = score(txt)
        noisy = any(n in txt for n in NOISE_WORDS)
        rows.append({"t": t, "txt": txt, "score": sc, "hit": hit,
                     "noisy": noisy})

    # 存档（去重）
    seen = set()
    if LOG.exists():
        for line in open(LOG, encoding="utf-8"):
            try:
                seen.add(json.loads(line)["txt"][:60])
            except Exception:
                pass
    new_rows = [r for r in rows if r["txt"][:60] not in seen]
    if new_rows:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            for r in new_rows:
                f.write(json.dumps({
                    "ts": r["t"].strftime("%Y-%m-%d %H:%M"),
                    "txt": r["txt"], "score": r["score"],
                    "hit": r["hit"]}, ensure_ascii=False) + "\n")

    print(f"=== 金十 · 最近 {hours:g} 小时 · 共 {len(rows)} 条 "
          f"（新增存档 {len(new_rows)} 条）===")

    if show_all:
        for r in sorted(rows, key=lambda x: x["t"], reverse=True):
            print(f"  {r['t']:%m-%d %H:%M} [{r['score']:2d}] {r['txt'][:100]}")
        return

    # 2026-10-27 修正（用户指出「过滤太多」）：
    # 原来只输出关键词命中的条目，导致「俄罗斯称在黑海击中一艘干散货船」
    # 「奥列什基停火持续至11月1日」这类重大消息因不含预设关键词被整条丢弃。
    # 现在：只过滤真正的广告噪音（直播/点击观看），其余全部输出，按重要度排序。
    picked = [r for r in rows if not r["noisy"]]
    if not picked:
        picked = rows          # 全是噪音也不许空手而归
    # 数据类去重：同一条会同时出现「文字版」和「[数据] 版」，只留一个
    _seen_key = set()
    _dedup = []
    for r in picked:
        k = (r["t"].strftime("%H:%M"), r["txt"][:28])
        if k in _seen_key:
            continue
        _seen_key.add(k)
        _dedup.append(r)
    picked = _dedup

    picked.sort(key=lambda x: (-x["score"], -x["t"].timestamp()))
    picked = picked[:limit]

    n_high = sum(1 for r in picked if r["score"] >= 9)
    n_mid = sum(1 for r in picked if 7 <= r["score"] < 9)
    print(f"  （输出 {len(picked)} 条：★★{n_high} / ★{n_mid} / 其余 {len(picked)-n_high-n_mid}）")
    for r in picked:
        star = "★★" if r["score"] >= 9 else ("★" if r["score"] >= 7 else "·")
        print(f"  {star} {r['t']:%H:%M} [{r['score']:2d}] {r['txt'][:130]}")
        if r["hit"]:
            print(f"        关键词: {'/'.join(r['hit'][:6])}")


if __name__ == "__main__":
    main()
