"""把 Odaily Skill 的宏观分析接入每轮决策

来源：https://github.com/ODAILY/odaily_plugin- （已克隆到 D:\\DP workspace\\odaily_skill）
调用：python run.py get_crypto_market_analysis '{"focus":"overview"}'

提供我们之前没有的数据：
  · 全市场总市值 + 24h 变化
  · BTC 市占率
  · 恐惧贪婪指数
  · 8 个主流币行情（ETH/BNB/XRP/SOL/DOGE/ADA/AVAX 相对强弱）
  · 24h 涨跌幅 TOP5（板块轮动）
  · 宏观相关快讯（含 Odaily 链接）

输出：data/macro_odaily.json，供 Agent 判断与机器人 L1 参考
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(r"D:\DP workspace\btc_system")
SKILL = Path(r"D:\DP workspace\odaily_skill")
OUT = ROOT / "data" / "macro_odaily.json"


def _run(tool: str, args: dict | None = None) -> str:
    cmd = [sys.executable, "run.py", tool, json.dumps(args or {})]
    r = subprocess.run(cmd, cwd=str(SKILL), capture_output=True,
                       text=True, timeout=90, encoding="utf-8", errors="ignore")
    return r.stdout or ""


def parse(text: str) -> dict:
    """从 skill 的格式化输出里抽结构化字段"""
    d = {"raw_len": len(text)}

    def grab(pat, cast=float):
        m = re.search(pat, text)
        if not m:
            return None
        try:
            return cast(m.group(1).replace(",", "").replace("$", ""))
        except Exception:
            return None

    d["total_mcap_t"] = grab(r"总市值:\s*\$?([\d.]+)T")
    d["total_mcap_pct"] = grab(r"总市值:.*?\(.*?([+-][\d.]+)%")
    d["vol24h_b"] = grab(r"24H交易量:\s*\$?([\d.]+)B")
    d["btc_dominance"] = grab(r"BTC市占率:\s*([\d.]+)%")
    d["fear_greed"] = grab(r"恐惧贪婪:\s*(\d+)", int)

    # 主流币涨跌
    coins = {}
    for m in re.finditer(r"^\s*(BTC|ETH|BNB|XRP|SOL|DOGE|ADA|AVAX)\s+\$?([\d,.]+)\s+.*?([+-][\d.]+)%",
                         text, re.M):
        try:
            coins[m.group(1)] = {"px": float(m.group(2).replace(",", "")),
                                 "chg": float(m.group(3))}
        except Exception:
            pass
    d["coins"] = coins

    # 资金费率
    fr = re.search(r"BTC:.*?资金费率.*?([+-][\d.]+)%", text)
    d["btc_funding"] = float(fr.group(1)) if fr else None

    # 涨跌幅榜
    gain = re.findall(r"🟢\s*([A-Z0-9]+)\s+🟢\s*\+([\d.]+)%", text)
    lose = re.findall(r"🔴\s*([A-Z0-9]+)\s+🔴\s*-([\d.]+)%", text)
    d["top_gainers"] = [{"sym": s, "pct": float(p)} for s, p in gain[:5]]
    d["top_losers"] = [{"sym": s, "pct": -float(p)} for s, p in lose[:5]]

    # 宏观快讯标题
    heads = re.findall(r"• (.{6,80})", text)
    d["macro_heads"] = heads[:8]

    return d


def main():
    out = {"ts": time.strftime("%Y-%m-%d %H:%M:%S")}
    try:
        text = _run("get_crypto_market_analysis", {"focus": "overview"})
        if not text.strip():
            out["ok"] = False
            out["why"] = "skill 无输出"
        else:
            parsed = parse(text)
            out.update(parsed)
            out["ok"] = bool(parsed.get("total_mcap_t") or parsed.get("coins"))
            if not out.get("ok"):
                out["why"] = "输出解析失败，原始前 300 字: " + text[:300]
    except Exception as e:
        out["ok"] = False
        out["why"] = str(e)[:150]

    json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    if "--json" in sys.argv:
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return 0 if out.get("ok") else 1

    if not out.get("ok"):
        print("Odaily 宏观分析失败:", out.get("why"))
        return 1

    print("=== Odaily 宏观（skill）===")
    print("  总市值 %.2fT (%+.2f%%)  24H量 %.1fB  BTC市占 %.1f%%  恐惧贪婪 %s"
          % (out.get("total_mcap_t") or 0, out.get("total_mcap_pct") or 0,
             out.get("vol24h_b") or 0, out.get("btc_dominance") or 0,
             out.get("fear_greed")))
    cs = out.get("coins") or {}
    if cs:
        print("  主流币: " + "  ".join("%s %+.1f%%" % (k, v["chg"])
                                       for k, v in list(cs.items())[:6]))
    if out.get("btc_funding") is not None:
        print("  BTC 资金费率 %+.4f%%" % out["btc_funding"])
    if out.get("top_gainers"):
        print("  涨幅榜: " + " ".join("%s %+.1f%%" % (g["sym"], g["pct"])
                                      for g in out["top_gainers"][:5]))
    if out.get("top_losers"):
        print("  跌幅榜: " + " ".join("%s %+.1f%%" % (g["sym"], g["pct"])
                                      for g in out["top_losers"][:5]))
    for h in (out.get("macro_heads") or [])[:3]:
        print("  · " + h)
    return 0


if __name__ == "__main__":
    sys.exit(main())
