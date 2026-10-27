"""agent_view.json 接入层（方案 B+C）

B = size_mult：我的判断乘到仓位上（0~1，1=不干预，0.5=半仓）
C = veto_new：禁止新开仓，但已有仓位照常管理（止损/止盈/移动止损不受影响）

设计原则：
  · 只读、失败即忽略（任何异常都不能影响交易主流程）
  · 有过期保护：超过 expire_min 分钟视为无意见
  · 旁路模式（mode="shadow"）：只记录不生效，便于先验证
  · 默认 mode="shadow"，改 config 里 agent_view_mode="live" 才真正生效

文件：data/agent_view.json
{
  "ts": "2026-10-27T20:40",
  "mode": "shadow|live",
  "direction_hint": "short|long|neutral",
  "confidence": 0.0~1.0,
  "size_mult": 0.0~1.0,
  "veto_new": false,
  "reasons": ["..."],
  "risk_flags": ["fomc_t1"]
}
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

from .utils import read_json

ROOT = Path(__file__).resolve().parent.parent
FILE = ROOT / "data" / "agent_view.json"
SHADOW_LOG = ROOT / "data" / "agent_view_log.jsonl"

EXPIRE_SEC = 90 * 60          # 超过 90 分钟视为无意见
SIZE_MULT_MIN = 0.0
SIZE_MULT_MAX = 1.0


@dataclass
class View:
    ok: bool = False
    fresh: bool = False
    age_min: float = 0.0
    mode: str = "shadow"
    direction: str = "neutral"
    confidence: float = 0.0
    size_mult: float = 1.0
    veto_new: bool = False
    reasons: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)

    def apply_size(self, n: float) -> float:
        """把 size_mult 乘到张数上。只缩不放。"""
        if not (self.ok and self.fresh and self.mode == "live"):
            return n
        m = max(SIZE_MULT_MIN, min(SIZE_MULT_MAX, self.size_mult))
        return n * m

    def blocked(self) -> bool:
        """是否禁止新开仓。"""
        return bool(self.ok and self.fresh and self.mode == "live" and self.veto_new)

    def summary(self) -> str:
        if not self.ok:
            return "AgentView[无]"
        tag = "鲜" if self.fresh else "旧"
        return (f"AgentView[{tag}{self.age_min:.0f}m {self.mode}] "
                f"{self.direction} conf={self.confidence:.2f} "
                f"size={self.size_mult:.2f} veto={self.veto_new}")


def load() -> View:
    v = View()
    try:
        raw = read_json(FILE, {}) or {}
        if not raw:
            return v
        v.ok = True
        ts = raw.get("ts")
        try:
            from datetime import datetime
            t = datetime.strptime(str(ts), "%Y-%m-%dT%H:%M").timestamp()
            v.age_min = (time.time() - t) / 60
            v.fresh = v.age_min < EXPIRE_SEC / 60
        except Exception:
            v.fresh = False
        v.mode = str(raw.get("mode") or "shadow").lower()
        v.direction = str(raw.get("direction_hint") or "neutral").lower()
        v.confidence = float(raw.get("confidence") or 0)
        v.size_mult = float(raw.get("size_mult") or 1.0)
        v.veto_new = bool(raw.get("veto_new"))
        v.reasons = list(raw.get("reasons") or [])
        v.flags = list(raw.get("risk_flags") or [])
    except Exception:
        # 任何异常都退化为"无意见"，绝不阻塞交易
        return View()
    return v


def log_apply(kind: str, before: float, after: float, v: View, extra: str = ""):
    """把 AgentView 的实际影响记一笔，便于事后评估它到底有没有用。"""
    import json
    try:
        SHADOW_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(SHADOW_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "t": time.strftime("%Y-%m-%d %H:%M:%S"),
                "kind": kind, "before": round(before, 2),
                "after": round(after, 2),
                "mode": v.mode, "dir": v.direction,
                "conf": v.confidence, "size_mult": v.size_mult,
                "veto": v.veto_new, "extra": extra,
            }, ensure_ascii=False) + "\n")
    except Exception:
        pass


def write_view(direction: str, confidence: float, size_mult: float,
               veto_new: bool, reasons: list[str], flags: list[str],
               mode: str = "shadow") -> Path:
    """给 Agent（我）用：把判断写成文件。"""
    import json
    from datetime import datetime
    FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "ts": datetime.now().strftime("%Y-%m-%dT%H:%M"),
        "mode": mode,
        "direction_hint": direction,
        "confidence": round(float(confidence), 2),
        "size_mult": round(float(size_mult), 2),
        "veto_new": bool(veto_new),
        "reasons": reasons,
        "risk_flags": flags,
    }
    with open(FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return FILE
