"""状态持久化与交易日志（SQLite + JSON）。"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, asdict, field
from datetime import datetime

from .utils import ROOT, read_json, write_json, CST, now_ms

STATE_FILE = ROOT / "state" / "bot_state.json"
DB_FILE = ROOT / "state" / "journal.sqlite"
CYCLE_LOG = ROOT / "logs" / "cycles.jsonl"


@dataclass
class Position:
    side: str = ""              # 'long' / 'short'
    entry: float = 0.0
    size: float = 0.0           # 张数
    stop: float = 0.0
    targets: list = field(default_factory=list)
    tp_filled: list = field(default_factory=list)   # 已完成的档位索引
    opened_at: int = 0
    model: str = ""
    algo_id: str = ""
    cl_ord_id: str = ""
    counter_trend: bool = False
    peak_pnl_pct: float = 0.0
    risk0: float = 0.0        # 开仓时的原始风险距离（止损被移动后仍保留）

    @property
    def is_open(self) -> bool:
        return bool(self.side) and self.size > 0


@dataclass
class BotState:
    # 双向持仓：{"long": Position, "short": Position}，各自独立
    positions: dict = field(default_factory=dict)
    loss_streak: int = 0
    win_streak: int = 0
    trades_total: int = 0
    wins: int = 0
    last_cycle: int = 0
    last_signal: dict = field(default_factory=dict)
    halted_until: int = 0
    cycle_count: int = 0
    equity_peak: float = 0.0
    day_key: str = ""
    day_start_equity: float = 0.0
    day_halted: bool = False
    started_at: int = 0
    start_equity: float = 0.0

    def get_pos(self, side: str) -> Position:
        p = self.positions.get(side)
        return p if isinstance(p, Position) else Position()

    def set_pos(self, side: str, pos: Position):
        if pos is None or not pos.is_open:
            self.positions.pop(side, None)
        else:
            self.positions[side] = pos

    def open_sides(self) -> list:
        return [k for k, v in self.positions.items()
                if isinstance(v, Position) and v.is_open]

    def n_open(self) -> int:
        return len(self.open_sides())

    @property
    def position(self) -> Position:
        """兼容旧代码：返回任意一个已开仓位（优先多头）。"""
        for s_ in ("long", "short"):
            p = self.positions.get(s_)
            if isinstance(p, Position) and p.is_open:
                return p
        return Position()


def load_state() -> BotState:
    raw = read_json(STATE_FILE, {}) or {}
    posmap = {}
    for side_, praw in (raw.get("positions") or {}).items():
        if not isinstance(praw, dict):
            continue
        praw = {k: v for k, v in praw.items() if k in Position.__dataclass_fields__}
        try:
            pp = Position(**praw)
        except Exception:
            continue
        if pp.is_open:
            posmap[side_] = pp
    # 兼容旧格式：单 position 字段
    if not posmap:
        _praw = raw.get("position") or {}
        _praw = {k: v for k, v in _praw.items() if k in Position.__dataclass_fields__}
        if _praw:
            pp = Position(**_praw)
            if pp.is_open:
                posmap[pp.side] = pp
    st = BotState(positions=posmap)
    for k in ("loss_streak", "win_streak", "trades_total", "wins",
              "last_cycle", "halted_until", "cycle_count", "equity_peak",
              "day_key", "day_start_equity", "day_halted",
              "started_at", "start_equity"):
        if k in raw:
            setattr(st, k, raw[k])
    st.last_signal = raw.get("last_signal", {})
    return st


def save_state(st: BotState) -> None:
    write_json(STATE_FILE, {
        "positions": {k: asdict(v) for k, v in st.positions.items()
                      if isinstance(v, Position) and v.is_open},
        "loss_streak": st.loss_streak,
        "win_streak": st.win_streak,
        "trades_total": st.trades_total,
        "wins": st.wins,
        "last_cycle": st.last_cycle,
        "last_signal": st.last_signal,
        "halted_until": st.halted_until,
        "cycle_count": st.cycle_count,
        "equity_peak": st.equity_peak,
        "day_key": st.day_key,
        "day_start_equity": st.day_start_equity,
        "day_halted": st.day_halted,
        "started_at": st.started_at,
        "start_equity": st.start_equity,
        "saved_at": datetime.now(CST).isoformat(),
    })


# ---------------- SQLite ----------------
def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB_FILE)
    c.execute("""
    CREATE TABLE IF NOT EXISTS trades (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        open_ts INTEGER, close_ts INTEGER,
        inst_id TEXT, side TEXT, model TEXT,
        entry REAL, exit REAL, size REAL, stop REAL,
        r_multiple REAL, pnl_usd REAL, pnl_pct REAL,
        reason TEXT, notes TEXT, counter_trend INTEGER
    )""")
    c.execute("""
    CREATE TABLE IF NOT EXISTS equity (
        ts INTEGER PRIMARY KEY, equity REAL, upl REAL
    )""")
    c.execute("""
    CREATE TABLE IF NOT EXISTS signals (
        ts INTEGER, model TEXT, side TEXT, entry REAL, stop REAL,
        conf REAL, accepted INTEGER, reason TEXT
    )""")
    c.commit()
    return c


def log_trade(rec: dict) -> None:
    c = _conn()
    c.execute("""INSERT INTO trades
        (open_ts,close_ts,inst_id,side,model,entry,exit,size,stop,
         r_multiple,pnl_usd,pnl_pct,reason,notes,counter_trend)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
        rec.get("open_ts"), rec.get("close_ts"), rec.get("inst_id"),
        rec.get("side"), rec.get("model"), rec.get("entry"), rec.get("exit"),
        rec.get("size"), rec.get("stop"), rec.get("r_multiple"),
        rec.get("pnl_usd"), rec.get("pnl_pct"), rec.get("reason"),
        rec.get("notes"), int(bool(rec.get("counter_trend"))),
    ))
    c.commit()
    c.close()


def log_equity(equity: float, upl: float = 0.0) -> None:
    c = _conn()
    c.execute("INSERT OR REPLACE INTO equity (ts,equity,upl) VALUES (?,?,?)",
              (now_ms(), equity, upl))
    c.commit()
    c.close()


def log_signal(ts: int, model: str, side: str, entry: float, stop: float,
               conf: float, accepted: bool, reason: str) -> None:
    c = _conn()
    c.execute("INSERT INTO signals (ts,model,side,entry,stop,conf,accepted,reason)"
              " VALUES (?,?,?,?,?,?,?,?)",
              (ts, model, side, entry, stop, conf, int(accepted), reason))
    c.commit()
    c.close()


def stats(days: int = 30) -> dict:
    c = _conn()
    cur = c.execute("""
        SELECT COUNT(*), SUM(CASE WHEN pnl_usd>0 THEN 1 ELSE 0 END),
               AVG(r_multiple), SUM(pnl_usd)
        FROM trades WHERE close_ts > ?""", (now_ms() - days * 86400000,))
    n, w, avg_r, pnl = cur.fetchone()
    c.close()
    n = n or 0
    return {
        "trades": n,
        "win_rate": (w or 0) / n if n else 0.0,
        "avg_r": avg_r or 0.0,
        "pnl_usd": pnl or 0.0,
    }
