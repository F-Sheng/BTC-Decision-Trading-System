"""影子账户 (Shadow Book)：不真下单，用实时价格推进虚拟持仓，积累前向测试样本。

为什么需要它：
- 47 天回测显示形态入场没有可证实的 edge（均 R ≈ 0，扣费后为负）
- 但 L1 宏观 + L2 清算这两层无法用 OHLC 历史回测（没有历史清算数据）
- 唯一诚实的验证方式：让系统按实时数据运行，记录"本会怎么交易"，事后统计
- 影子账户没有任何资金风险，且严格无未来函数
"""
from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field

from .utils import ROOT, now_ms, read_json, safe_div, write_json

SHADOW_FILE = ROOT / "state" / "shadow.json"


@dataclass
class ShadowPos:
    pid: str
    side: str
    model: str
    entry: float
    stop: float
    targets: list
    opened_at: int
    confidence: float = 0.0
    tp_filled: list = field(default_factory=list)
    stop_now: float = 0.0
    max_favorable: float = 0.0
    max_adverse: float = 0.0
    l1: int = 0
    l2: int = 0
    note: str = ""


class ShadowBook:
    def __init__(self):
        raw = read_json(SHADOW_FILE, {}) or {}
        self.open: list[dict] = raw.get("open", [])
        self.closed: list[dict] = raw.get("closed", [])

    # ---------- 持久化 ----------
    def save(self) -> None:
        write_json(SHADOW_FILE, {
            "open": self.open,
            "closed": self.closed[-500:],
            "updated": now_ms(),
        })

    # ---------- 开仓 ----------
    def add(self, sig, plan, sc, logger) -> None:
        if any(p["side"] == sig.side for p in self.open):
            return
        p = ShadowPos(
            pid=uuid.uuid4().hex[:8], side=sig.side, model=sig.model,
            entry=sig.entry, stop=sig.stop, targets=list(sig.targets),
            opened_at=now_ms(), confidence=sig.confidence,
            stop_now=sig.stop, l1=sc.l1_total, l2=sc.l2_score,
            note="; ".join(sig.reasons)[:120],
        )
        self.open.append(asdict(p))
        logger.info("[SHADOW] 记入虚拟仓 %s %s@%.0f SL=%.0f 目标=%s",
                    p.side, p.model, p.entry, p.stop, p.targets)
        self.save()

    # ---------- 推进 ----------
    def update(self, price: float, cfg: dict, logger, equity: float = 0.0) -> None:
        keep = []
        for p in self.open:
            side, entry, stop = p["side"], p["entry"], p["stop_now"] or p["stop"]
            risk = abs(stop - entry) or entry * cfg["stop_pct_min"]
            move = (price - entry) if side == "long" else (entry - price)
            p["max_favorable"] = max(p.get("max_favorable", 0), move / risk)
            p["max_adverse"] = min(p.get("max_adverse", 0), move / risk)

            # 止损
            hit_stop = (price <= stop) if side == "long" else (price >= stop)
            if hit_stop:
                self._close(p, stop, "SL" if abs(stop - entry) > 1e-9 else "BE",
                            risk, logger, equity)
                continue

            # 分批止盈
            for k, tp in enumerate(p["targets"]):
                if k in p["tp_filled"]:
                    continue
                hit = (price >= tp) if side == "long" else (price <= tp)
                if hit:
                    p["tp_filled"].append(k)
                    if k == 0:
                        p["stop_now"] = entry        # 移保本
                    logger.info("[SHADOW] %s 触及 TP%d @%.0f", p["model"], k + 1, price)
                    if k == len(p["targets"]) - 1:
                        self._close(p, tp, f"TP{k+1}", risk, logger, equity)
                        break
            else:
                # 时间止损
                hours = (now_ms() - p["opened_at"]) / 3.6e6
                if hours >= cfg["time_stop_hours_full"] and not p["tp_filled"]:
                    self._close(p, price, "TIME", risk, logger, equity)
                    continue
                keep.append(p)
                continue
            if len(p["tp_filled"]) >= len(p["targets"]):
                continue
            keep.append(p)
        self.open = keep
        self.save()

    def _close(self, p: dict, price: float, why: str, risk: float,
               logger, equity: float) -> None:
        side, entry = p["side"], p["entry"]
        move = (price - entry) if side == "long" else (entry - price)
        r = safe_div(move, risk)
        w = [0.40, 0.35, 0.25]
        if p["tp_filled"] and why.startswith("SL"):
            r = sum(w[k] * abs(p["targets"][k] - entry) / risk for k in p["tp_filled"])
        rec = {**p, "exit": price, "close_ts": now_ms(), "reason": why, "r": r}
        self.closed.append(rec)
        logger.info("[SHADOW] 平仓 %s %s → %.0f 原因=%s R=%+.2f",
                    p["side"], p["model"], price, why, r)

    # ---------- 统计 ----------
    def stats(self, since_ms: int = 0) -> dict:
        cs = [c for c in self.closed if c["close_ts"] >= since_ms]
        n = len(cs)
        if not n:
            return {"n": 0, "win": 0, "win_rate": 0.0, "avg_r": 0.0,
                    "total_r": 0.0, "open": len(self.open)}
        rs = [c["r"] for c in cs]
        wins = sum(1 for r in rs if r > 0)
        return {
            "n": n, "win": wins, "win_rate": wins / n,
            "avg_r": sum(rs) / n, "total_r": sum(rs),
            "open": len(self.open),
            "by_model": {
                m: {
                    "n": len([c for c in cs if c["model"] == m]),
                    "avg_r": (sum(c["r"] for c in cs if c["model"] == m) /
                              max(1, len([c for c in cs if c["model"] == m]))),
                } for m in set(c["model"] for c in cs)
            },
        }

    def summary_line(self) -> str:
        s = self.stats()
        if not s["n"]:
            return f"影子账户: 持有{len(self.open)} 笔，尚无平仓样本"
        return (f"影子账户: 已平{s['n']}笔 胜率{s['win_rate']*100:.0f}% "
                f"均R{s['avg_r']:+.2f} 总R{s['total_r']:+.1f} | 持有{len(self.open)}笔")
