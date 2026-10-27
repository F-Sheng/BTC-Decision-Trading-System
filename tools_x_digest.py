"""用本地文本模型（qwen2.5:3b）对 X 推文做初筛 + 摘要 + 情绪打分

目的：覆盖 38 条推文但不把原文全塞给 Agent（省 token）。
本地模型做三件事：
  1. 过滤：只保留与 BTC/宏观/地缘/监管相关的
  2. 摘要：每条压成一句话（中文）
  3. 打分：-2 ~ +2（对 BTC 的利多利空）

输出 data/x_digest.json，Agent 只读这个摘要文件。

用法：
  python tools_x_digest.py            # 摘要并写文件
  python tools_x_digest.py --json
"""

from __future__ import annotations

import json
import re
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(r"D:\DP workspace\btc_system")
SRC = ROOT / "data" / "x_posts.json"
OUT = ROOT / "data" / "x_digest.json"

OLLAMA = "http://127.0.0.1:11434/api/generate"
MODEL = "qwen2.5:3b"

PROMPT = """你是加密货币交易分析助手。下面是从 X(Twitter) 抓取的推文列表，请处理：

规则：
1. 只保留与「BTC 价格 / 加密市场 / 宏观经济 / 地缘政治 / 监管政策 / 大额资金流向」相关的推文
2. 广告、纯营销、无关闲聊全部丢弃
3. 每条保留的推文压成一句中文摘要（不超过 40 字）
4. 给每条打情绪分：-2=明显利空BTC, -1=略利空, 0=中性, +1=略利多, +2=明显利多

输出严格的 JSON 数组，不要解释、不要 markdown：
[{"i": 序号, "s": "中文摘要", "m": 情绪分}, ...]

推文列表：
%s
"""


def load_posts() -> list:
    try:
        d = json.load(open(SRC, encoding="utf-8"))
        return d.get("posts") or []
    except Exception:
        return []


def call_llm(prompt: str, timeout: int = 240) -> str:
    body = json.dumps({
        "model": MODEL, "prompt": prompt, "stream": False,
        "options": {"temperature": 0.1, "num_predict": 1600},
    }).encode()
    req = urllib.request.Request(
        OLLAMA, data=body,
        headers={"Content-Type": "application/json"})
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    r = json.loads(op.open(req, timeout=timeout).read().decode())
    return r.get("response", "")


def parse_json_array(s: str) -> list:
    """从模型输出里抠出 JSON 数组（模型经常包 ```json 或不闭合）"""
    if not s:
        return []
    m = re.search(r"\[[\s\S]*\]", s)
    if not m:
        return []
    txt = m.group(0)
    # 去掉可能的尾逗号
    txt = re.sub(r",\s*\]", "]", txt)
    try:
        return json.loads(txt)
    except Exception:
        pass
    # 逐条修：给裸值补引号
    try:
        txt2 = re.sub(r'(":\s*)([A-Za-z\u4e00-\u9fff][^",\]\n]*)',
                      lambda m2: m2.group(1) + '"' + m2.group(2).strip() + '"', txt)
        return json.loads(txt2)
    except Exception:
        return []


def main():
    posts = load_posts()
    if not posts:
        print("x_posts.json 无数据")
        return 1

    lines = []
    for i, p in enumerate(posts):
        t = (p.get("text") or "").replace("\n", " ")[:180]
        lines.append("%d. [%s] @%s: %s" % (i, p.get("tier", "?"),
                                           p.get("user", "?"), t))
    prompt = PROMPT % "\n".join(lines)

    t0 = time.time()
    try:
        raw = call_llm(prompt)
    except Exception as e:
        print("本地模型调用失败:", str(e)[:100])
        return 1
    dt = time.time() - t0

    arr = parse_json_array(raw)
    print("=== 本地模型初筛（%.1f 秒，%d 条 -> %d 条）===" % (dt, len(posts), len(arr)))

    digest = []
    for item in arr:
        try:
            i = int(item.get("i"))
            if i < 0 or i >= len(posts):
                continue
            p = posts[i]
            digest.append({
                "i": i, "summary": str(item.get("s") or "")[:80],
                "mood": int(item.get("m") or 0),
                "tier": p.get("tier"), "user": p.get("user"),
                "age": p.get("age", ""), "url": p.get("url", ""),
                "orig": (p.get("text") or "")[:120],
            })
        except Exception:
            continue

    digest.sort(key=lambda x: (-abs(x["mood"]), x["i"]))

    payload = {
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "model": MODEL, "src_count": len(posts),
        "kept": len(digest), "elapsed_s": round(dt, 1),
        "mood_sum": sum(d["mood"] for d in digest),
        "digest": digest,
    }
    json.dump(payload, open(OUT, "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)

    if "--json" in sys.argv:
        print(json.dumps(payload, ensure_ascii=False, indent=1)[:3000])
        return 0

    for d in digest[:15]:
        star = "+" * d["mood"] if d["mood"] > 0 else ("-" * abs(d["mood"])
                                                      if d["mood"] < 0 else "0")
        print("  [%3s] %-16s %s" % (star, (d["user"] or "")[:16], d["summary"]))
    print()
    print("  情绪合计 %+d  已写入 %s" % (payload["mood_sum"], OUT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
