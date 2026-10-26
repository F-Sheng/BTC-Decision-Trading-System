"""通用工具：日志、重试、时间、JSON 持久化、原子写。"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
import traceback
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Callable, TypeVar

ROOT = Path(__file__).resolve().parent.parent
STATE_DIR = ROOT / "state"
LOG_DIR = ROOT / "logs"
DATA_DIR = ROOT / "data"
for _d in (STATE_DIR, LOG_DIR, DATA_DIR):
    _d.mkdir(parents=True, exist_ok=True)

T = TypeVar("T")
CST = timezone(timedelta(hours=8))


def setup_logging(name: str = "btc") -> logging.Logger:
    """双通道日志：控制台 + 按日滚动文件。

    守护模式下设置环境变量 BTC_NO_CONSOLE=1 可关闭控制台输出，
    避免因 stdout 管道缓冲写满而阻塞主循环（曾导致循环卡死）。
    """
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-7s %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )
    if os.environ.get("BTC_NO_CONSOLE", "") not in ("1", "true", "True"):
        sh = logging.StreamHandler(sys.stdout)
        sh.setFormatter(fmt)
        logger.addHandler(sh)

    fh = logging.FileHandler(
        LOG_DIR / f"{name}-{datetime.now(CST).strftime('%Y%m%d')}.log",
        encoding="utf-8",
    )
    fh.setFormatter(fmt)
    logger.addHandler(fh)
    return logger


def now_ms() -> int:
    return int(time.time() * 1000)


def now_cst() -> datetime:
    return datetime.now(CST)


def ts_to_cst(ms: int | str) -> datetime:
    return datetime.fromtimestamp(int(ms) / 1000, CST)


def fmt_ts(ms: int | str) -> str:
    return ts_to_cst(ms).strftime("%m-%d %H:%M")


def retry(fn: Callable[[], T], tries: int = 3, base: float = 0.8,
          logger: logging.Logger | None = None, label: str = "") -> T | None:
    """指数退避重试。失败返回 None（不抛出，避免打断主循环）。"""
    last = None
    for i in range(tries):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
            last = e
            if logger:
                logger.warning("retry %s [%d/%d]: %s", label or fn, i + 1, tries, e)
            time.sleep(base * (2 ** i))
    if logger:
        logger.error("gave up %s: %s", label or fn, last)
    return None


def read_json(path: Path, default: Any = None) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except Exception:
        return default


def write_json(path: Path, obj: Any) -> None:
    """原子写，避免进程被杀时留下半个文件。"""
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2, default=str)
    os.replace(tmp, path)


def append_jsonl(path: Path, obj: Any) -> None:
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False, default=str) + "\n")


def pct(x: float, digits: int = 2) -> str:
    return f"{x * 100:+.{digits}f}%"


def safe_div(a: float, b: float, default: float = 0.0) -> float:
    return a / b if b else default


def guard(logger: logging.Logger, label: str):
    """装饰器：把未捕获异常降级为日志，保证主循环不死。"""
    def deco(fn):
        def wrapper(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except Exception as e:  # noqa: BLE001
                logger.error("[%s] 异常: %s\n%s", label, e, traceback.format_exc(limit=3))
                return None
        return wrapper
    return deco
