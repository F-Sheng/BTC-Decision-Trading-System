"""L4 事件层：财经日历、期权到期、突发新闻监测 + LLM 文本分类。

原则：
- 事实由本模块从一手源抓取（FRED / 公开日历 / RSS）
- LLM 只做「对给定文本的分类与抽取」，绝不让它凭记忆报数字
"""
from __future__ import annotations

import json
import re
import time
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from .utils import ROOT, read_json, write_json, CST

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0 Safari/537.36"
NEWS_CACHE = ROOT / "state" / "news_cache.json"
LLM_CACHE = ROOT / "state" / "llm_cache.json"

FEEDS = {
    "CoinDesk": "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "Cointelegraph": "https://cointelegraph.com/rss",
    "TheBlock": "https://www.theblock.co/rss.xml",
}

# 2026 年 FOMC 例会（可在 config.json 覆盖）
# 注意：FOMC 决议公布时间是美国东部 14:00，换算北京时间是【次日 02:00】。
# 这里必须填「北京日期」，即美东会议日 +1 天。
# 2026-10-28 修正：原来填的是美东日期，导致 10-28 全天被误判为事件静默期。
FOMC_2026 = [
    "2026-01-29", "2026-03-19", "2026-04-30", "2026-06-18",
    "2026-07-30", "2026-09-17", "2026-10-29", "2026-12-10",
]
# 决议公布的具体时间（北京时间 HH:MM），用于 30 分钟级静默窗
FOMC_DECISION_TIME = "02:00"
# 政治/监管类事件：对加密与 AI 板块的方向性影响比单次 FOMC 更大
POLITICAL_2026 = [
    # 美国中期选举：众议院 435 席 + 参议院 35 席改选
    # 影响链条：国会归属 → CLARITY 法案等加密立法推进速度 → 板块估值
    #   共和党保住两院 → 政策连续、立法顺畅 → 利好
    #   民主党夺回众议院 → 立法僵局 → 利空
    {"name": "美国中期选举", "date": "2026-11-03", "impact": "high",
     "note": "众议院435席+参议院35席改选；共和党保两院=政策连续，国会分治=立法僵局"},
    # 中期选举后的跛脚鸭会期，加密立法若未过会归零
    {"name": "跛脚鸭会期开始", "date": "2026-11-09", "impact": "mid",
     "note": "选举后至新国会就任，未通过的加密法案窗口关闭"},
    # CPI
]

# 2026 年 CPI 发布日（BLS 通常 8:30 ET；可在 config 覆盖）
CPI_2026 = [
    "2026-01-13", "2026-02-11", "2026-03-11", "2026-04-10", "2026-05-12",
    "2026-06-10", "2026-07-14", "2026-08-12", "2026-09-11", "2026-10-14",
    "2026-11-10", "2026-12-10",
]

# 高冲击关键词（突发状况）
SHOCK_WORDS = [
    "hack", "exploit", "breach", "stolen", "halt", "suspend", "insolven",
    "bankrupt", "default", "liquidat", "ban", "lawsuit", "sec sues", "indict",
    "war", "attack", "sanction", "tariff", "emergency", "crash", "plunge",
    "outflow", "rug", "depeg",
]
BULL_WORDS = [
    "etf inflow", "approval", "adopt", "treasury", "reserve", "buyback",
    "rate cut", "dovish", "institutional", "accumulat", "all-time high",
]
BEAR_WORDS = [
    "etf outflow", "rate hike", "hawkish", "sell-off", "selloff", "liquidation",
    "regulator", "crackdown", "inflation", "lawsuit",
]


# ---------------- 日历 ----------------
def _days_until(date_str: str) -> int:
    d = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=CST)
    return (d.date() - datetime.now(CST).date()).days


def next_events(cfg: dict | None = None, horizon: int = 14) -> list[dict]:
    """未来 horizon 天内的事件（负值=已过）。"""
    cfg = cfg or {}
    fomc = cfg.get("fomc_dates") or FOMC_2026
    cpi = cfg.get("cpi_dates") or CPI_2026
    out = []
    for d in fomc:
        k = _days_until(d)
        if -1 <= k <= horizon:
            out.append({"name": "FOMC 利率决议", "date": d, "days": k, "impact": "high"})
    for d in cpi:
        k = _days_until(d)
        if -1 <= k <= horizon:
            out.append({"name": "美国 CPI", "date": d, "days": k, "impact": "high"})
    for ev in POLITICAL_2026:
        k = _days_until(ev["date"])
        if -1 <= k <= horizon:
            out.append({"name": ev["name"], "date": ev["date"], "days": k,
                        "impact": ev["impact"], "note": ev.get("note", "")})

    # 期权到期：每周五 08:00 UTC；月末周五为月度大到期
    today = datetime.now(CST)
    for i in range(horizon + 1):
        d = today + timedelta(days=i)
        if d.weekday() == 4:  # Friday
            monthly = d.day + 7 > 31   # 该月最后一个周五
            out.append({
                "name": "BTC 期权月度到期" if monthly else "BTC 期权周度到期",
                "date": d.strftime("%Y-%m-%d"),
                "days": i,
                "impact": "high" if monthly else "low",
            })
    return sorted(out, key=lambda x: x["days"])


# ---------------- 新闻 ----------------
def _strip_tags(s: str) -> str:
    s = re.sub(r"<[^>]+>", "", s)
    s = (s.replace("&amp;", "&").replace("&quot;", '"')
          .replace("&#39;", "'").replace("&lt;", "<").replace("&gt;", ">")
          .replace("&#8217;", "'").replace("&#8220;", '"').replace("&#8221;", '"'))
    return s.strip()


def fetch_headlines(max_age_h: int = 24) -> list[dict]:
    out: list[dict] = []
    cutoff = time.time() - max_age_h * 3600
    for src, url in FEEDS.items():
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=20) as r:
                xml = r.read().decode("utf-8", "ignore")
        except Exception:
            continue
        for item in re.findall(r"<item>(.*?)</item>", xml, re.S)[:40]:
            t = re.search(r"<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>", item, re.S)
            p = re.search(r"<pubDate>(.*?)</pubDate>", item, re.S)
            if not t:
                continue
            title = _strip_tags(t.group(1))
            if not title or len(title) < 12:
                continue
            ts = None
            if p:
                try:
                    from email.utils import parsedate_to_datetime
                    ts = parsedate_to_datetime(p.group(1).strip()).timestamp()
                except Exception:
                    ts = None
            if ts and ts < cutoff:
                continue
            out.append({"source": src, "title": title, "ts": ts})
    out.sort(key=lambda x: x.get("ts") or 0, reverse=True)
    return out[:60]


def keyword_scan(heads: list[dict]) -> dict:
    shock, bull, bear = [], [], []
    for h in heads:
        t = h["title"].lower()
        if any(w in t for w in SHOCK_WORDS):
            shock.append(h["title"])
        if any(w in t for w in BULL_WORDS):
            bull.append(h["title"])
        if any(w in t for w in BEAR_WORDS):
            bear.append(h["title"])
    return {"shock": shock, "bull": bull, "bear": bear,
            "net": len(bull) - len(bear)}


# ---------------- LLM 情绪分类 ----------------
def _llm(prompt: str, cfg: dict) -> dict | None:
    key = cfg.get("deepseek_key")
    base = cfg.get("deepseek_base", "https://api.deepseek.com")
    model = cfg.get("deepseek_model", "deepseek-chat")
    if not key:
        return None
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content":
             "你是加密市场情绪分类器。只能依据用户提供的标题做判断，"
             "不得引入外部记忆或编造事实。严格输出 JSON。"},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0,
        "max_tokens": 500,
        "response_format": {"type": "json_object"},
    }
    req = urllib.request.Request(
        f"{base}/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                 "User-Agent": UA},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=45) as r:
            data = json.loads(r.read().decode())
        txt = data["choices"][0]["message"]["content"]
        return json.loads(txt)
    except Exception:
        return None


def llm_sentiment(heads: list[dict], cfg: dict) -> dict:
    """把标题交给 LLM 打 -2..+2 的情绪分（带缓存，30 分钟）。"""
    cache = read_json(LLM_CACHE, {}) or {}
    sig = "|".join(sorted(h["title"] for h in heads[:25]))[:400]
    key = str(hash(sig))
    ent = cache.get(key)
    if ent and time.time() - ent["t"] < 1800:
        return ent["v"]

    titles = "\n".join(f"- {h['title']}" for h in heads[:25])
    prompt = (
        "下面是最近 24 小时的加密/宏观新闻标题。请只依据这些标题评估对 BTC 的短期影响：\n"
        f"{titles}\n\n"
        '输出 JSON：{"score": -2到2的整数, "reason": "一句话", '
        '"risk_flags": ["最值得警惕的1-3条标题"]}。'
        "-2=显著利空, 0=中性, 2=显著利好。"
    )
    v = _llm(prompt, cfg) or {"score": 0, "reason": "LLM 不可用", "risk_flags": []}
    v["score"] = max(-2, min(2, int(v.get("score", 0) or 0)))
    cache[key] = {"t": time.time(), "v": v}
    write_json(LLM_CACHE, dict(list(cache.items())[-50:]))
    return v


# ---------------- 视图 ----------------
@dataclass
class EventView:
    events: list[dict] = field(default_factory=list)
    news: list[dict] = field(default_factory=list)
    shock: list[str] = field(default_factory=list)
    sentiment: dict = field(default_factory=dict)
    adjust: int = 0             # 对 L1 的事件修正 (-2..+2)
    blackout: bool = False      # 是否进入"事件静默期"
    notes: list[str] = field(default_factory=list)

    def summary(self) -> str:
        ev = ", ".join(f"{e['name']}T{e['days']:+d}" for e in self.events[:3]) or "无"
        return (f"L4 adj={self.adjust:+d} blackout={self.blackout} "
                f"情绪={self.sentiment.get('score', 0)} 冲击={len(self.shock)} | {ev}")


def compute(cfg: dict, heads: list[dict] | None = None) -> EventView:
    evs = next_events(cfg)
    heads = heads if heads is not None else fetch_headlines()
    kw = keyword_scan(heads)
    senti = llm_sentiment(heads, cfg) if heads else {"score": 0, "reason": "无新闻"}

    adjust = 0
    notes = []

    # 1) 新闻情绪 → ±1
    s = int(senti.get("score", 0) or 0)
    if s >= 2:
        adjust += 1
        notes.append("新闻显著偏多")
    elif s <= -2:
        adjust -= 1
        notes.append("新闻显著偏空")

    # 2) 突发事件 → 额外 -1（并向风控发信号）
    if len(kw["shock"]) >= 2:
        adjust -= 1
        notes.append(f"突发风险事件 {len(kw['shock'])} 条")

    # 3) 事件窗口
    blackout = False
    for e in evs:
        if e["impact"] != "high":
            continue
        # 2026-10-28 改进：原来是「当天整天静默」，太粗（曾误封 25 小时）。
        # 现在只在高影响事件【公布时刻前后 30 分钟】静默。
        if e["days"] != 0:
            continue
        try:
            from datetime import datetime as _dt, timedelta as _td
            _t = getattr(e, "time", None) or cfg.get("fomc_decision_time", "02:00")
            _now = _dt.now(CST)
            _hh, _mm = [int(x) for x in str(_t).split(":")[:2]]
            _ev = _now.replace(hour=_hh, minute=_mm, second=0, microsecond=0)
            if abs((_now - _ev).total_seconds()) <= 30 * 60:
                blackout = True
                notes.append("%s 公布时刻前后 30 分钟：禁止下单" % e["name"])
            else:
                _h = round((_ev - _now).total_seconds() / 3600, 1)
                notes.append("%s 今日 %s 公布（还有 %.1f 小时），非静默窗" % (
                    e["name"], _t, _h))
        except Exception:
            blackout = True
            notes.append("%s 今日公布：禁止下单（时间解析失败，保守处理）" % e["name"])
        if e["name"].startswith("BTC 期权月度到期") and 0 <= e["days"] <= 3:
            notes.append(f"月度期权到期 T-{e['days']}：max pain 引力增强")

    adjust = max(-2, min(2, adjust))
    return EventView(events=evs, news=heads, shock=kw["shock"], sentiment=senti,
                     adjust=adjust, blackout=blackout, notes=notes)
