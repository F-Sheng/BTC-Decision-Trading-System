"""心跳调度器 —— 2 分钟 tick，内部分三级

用户 2026-10-28 批准：
  L0（每 2 分钟，约 2 秒）：OKX 现价 + 强平流 + 金十  → 只做触发判定
  L1（每 5 分钟，约 5 秒）：LV liqmap + Odaily + 完整判定 → 有事件才汇报
  L2（每 60 分钟，约 50 秒）：X 多账号 + 本地模型摘要 + LV oi/regime + Odaily宏观

DSH 定时任务是固定间隔，所以用「2 分钟 tick + 内部节流」模拟动态频率。

用法：
  python tools_heartbeat.py           # 执行本次该做的层级，输出报告
  python tools_heartbeat.py --force l1  # 强制跑某一层
  python tools_heartbeat.py --status  # 只看状态
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(r"D:\DP workspace\btc_system")
STATE = ROOT / "data" / "heartbeat_state.json"
PY = sys.executable

L1_MIN = 5
L2_MIN = 60


def load_state() -> dict:
    try:
        return json.load(open(STATE, encoding="utf-8"))
    except Exception:
        return {}


def save_state(s: dict):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    json.dump(s, open(STATE, "w", encoding="utf-8"), ensure_ascii=False, indent=2)


def run(cmd: list, timeout=180) -> tuple[bool, str]:
    try:
        r = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True,
                           timeout=timeout, encoding="utf-8", errors="ignore")
        return r.returncode == 0, (r.stdout or "") + (r.stderr or "")
    except Exception as e:
        return False, str(e)[:120]


def l0() -> dict:
    """极频层：现价 + 强平流 + 金十（约 2 秒）"""
    out = {"tier": "L0"}
    ok, txt = run([PY, "tools_liq_flow.py"], timeout=60)
    out["liq_flow_ok"] = ok
    if ok:
        burst = "强平潮: 是" in txt
        out["burst"] = burst
        for ln in txt.splitlines():
            if "5分钟" in ln or "最新强平" in ln:
                out.setdefault("liq_lines", []).append(ln.strip())
    ok2, txt2 = run([PY, "tools_jin10.py"], timeout=60)
    out["jin10_ok"] = ok2
    if ok2:
        # 2026-10-28 优化：原来只要窗口内有★★就触发 L1，导致同一条新闻
        # 会被反复触发（实测连续 5 轮都是同一条停火消息）。
        # 改为「只有出现此前未见过的★★ 才算 news_high」。
        tops = []
        for ln in txt2.splitlines():
            t = ln.strip()
            # 排除统计行「（输出 N 条：★★x / ★y ...）」和分隔线
            if "★★" not in t or "输出" in t or t.startswith("==="):
                continue
            tops.append(re.sub(r"\\s+", " ", t)[:100])
        out["top_news"] = tops[0] if tops else None
        prev = set(load_state().get("seen_news", []))
        fresh = [t for t in tops if t not in prev]
        out["news_high"] = bool(fresh)
        out["fresh_news"] = fresh[:2]
        if fresh:
            st = load_state()
            st["seen_news"] = (list(prev) + fresh)[-60:]
            save_state(st)
    return out


def l1() -> dict:
    """主力层：LV liqmap + Odaily + 完整判定（约 8 秒）"""
    out = {"tier": "L1"}
    ok, txt = run([PY, "tools_lv_map.py"], timeout=120)
    out["lv_ok"] = ok
    if ok:
        for ln in txt.splitlines():
            if "R =" in ln or "空头清算" in ln or "多头清算" in ln:
                out.setdefault("lv_lines", []).append(ln.strip())
    ok2, _ = run([PY, "tools_odaily.py"], timeout=90)
    out["odaily_ok"] = ok2
    ok3, txt3 = run([PY, "tools_should_fetch.py"], timeout=90)
    out["decision_ok"] = ok3
    try:
        d = json.loads(txt3)
        out["need_fetch"] = d.get("need_fetch")
        out["read_mode"] = d.get("read_mode")
        out["reasons"] = d.get("reasons") or []
    except Exception:
        pass
    return out


def l2() -> dict:
    """慢层：X 多账号 + 本地模型摘要 + LV oi/regime + Odaily 宏观（约 3 分钟）"""
    out = {"tier": "L2"}
    ok, txt = run([PY, "tools_x_multi.py"], timeout=600)
    out["x_fetch_ok"] = ok
    if ok:
        for ln in txt.splitlines():
            if "合并结果" in ln:
                out["x_count"] = ln.strip()
    ok2, txt2 = run([PY, "tools_x_digest.py"], timeout=400)
    out["x_digest_ok"] = ok2
    if ok2:
        for ln in txt2.splitlines():
            if "情绪合计" in ln:
                out["x_mood"] = ln.strip()
    ok3, txt3 = run([PY, "tools_macro_odaily.py"], timeout=200)
    out["macro_ok"] = ok3
    if ok3:
        for ln in txt3.splitlines():
            if "总市值" in ln:
                out["macro"] = ln.strip()
                break
    return out


def main():
    args = sys.argv[1:]
    st = load_state()
    now = time.time()

    if "--status" in args:
        print(json.dumps({
            "now": datetime.now().strftime("%H:%M:%S"),
            "last_L1_ago_min": round((now - st.get("last_L1", 0)) / 60, 1),
            "last_L2_ago_min": round((now - st.get("last_L2", 0)) / 60, 1),
            "L1_due": (now - st.get("last_L1", 0)) / 60 >= L1_MIN,
            "L2_due": (now - st.get("last_L2", 0)) / 60 >= L2_MIN,
        }, ensure_ascii=False, indent=1))
        return 0

    force = None
    if "--force" in args:
        try:
            force = args[args.index("--force") + 1].lower()
        except Exception:
            pass

    result = {"ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "tiers": []}

    # L0 每次都跑
    if force in (None, "l0", "all"):
        r = l0()
        result["tiers"].append(r)

    trig = (force in ("l1", "all")
            or (now - st.get("last_L1", 0)) / 60 >= L1_MIN
            or (result["tiers"] and result["tiers"][0].get("burst"))
            or (result["tiers"] and result["tiers"][0].get("news_high")))
    if trig:
        r = l1()
        result["tiers"].append(r)
        st["last_L1"] = time.time()

    if force in ("l2", "all") or (now - st.get("last_L2", 0)) / 60 >= L2_MIN:
        r = l2()
        result["tiers"].append(r)
        st["last_L2"] = time.time()

    # 重新读一次再合并，避免用开头的旧快照覆盖 l0() 写入的 seen_news
    fresh_state = load_state()
    fresh_state["last_L1"] = st.get("last_L1", fresh_state.get("last_L1", 0))
    fresh_state["last_L2"] = st.get("last_L2", fresh_state.get("last_L2", 0))
    save_state(fresh_state)
    print(json.dumps(result, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
