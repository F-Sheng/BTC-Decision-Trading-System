"""OKX v5 REST 客户端（支持模拟盘）。

设计要点：
- 同步 urllib，零第三方依赖
- HMAC-SHA256 签名，时间戳严格 ISO8601 毫秒
- 令牌桶限速，避免 429
- 所有方法失败返回 None / 抛 OkxError，由上层决定重试
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from .utils import read_json, ROOT

BASE = "https://www.okx.com"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")


class OkxError(Exception):
    def __init__(self, code: str, msg: str, endpoint: str = ""):
        self.code = code
        self.msg = msg
        self.endpoint = endpoint
        super().__init__(f"[{code}] {msg} @ {endpoint}")


class RateLimiter:
    """简单令牌桶：默认 8 req/s，留足余量。"""

    def __init__(self, rate: float = 8.0, burst: int = 12):
        self.rate = rate
        self.burst = burst
        self.tokens = float(burst)
        self.last = time.monotonic()
        self.lock = threading.Lock()

    def acquire(self) -> None:
        with self.lock:
            now = time.monotonic()
            self.tokens = min(self.burst, self.tokens + (now - self.last) * self.rate)
            self.last = now
            if self.tokens < 1.0:
                wait = (1.0 - self.tokens) / self.rate
                time.sleep(wait)
                self.tokens = 0.0
                self.last = time.monotonic()
            else:
                self.tokens -= 1.0


class OkxClient:
    def __init__(self, simulated: bool = True, secrets_path=None):
        p = secrets_path or (ROOT / "secrets.json")
        s = read_json(p, {}) or {}
        self.key = s.get("okx_api_key", "")
        self.secret = s.get("okx_secret_key", "")
        self.passphrase = s.get("okx_passphrase", "")
        self.simulated = simulated
        self.rl = RateLimiter()
        if not (self.key and self.secret and self.passphrase):
            raise RuntimeError(f"缺少 OKX 密钥，请检查 {p}")

    # ---------- 签名 ----------
    @staticmethod
    def _ts() -> str:
        t = time.time()
        return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)) + f".{int(t * 1000) % 1000:03d}Z"

    def _headers(self, method: str, path_qs: str, body: str) -> dict:
        ts = self._ts()
        msg = f"{ts}{method.upper()}{path_qs}{body}"
        sign = base64.b64encode(
            hmac.new(self.secret.encode(), msg.encode(), hashlib.sha256).digest()
        ).decode()
        h = {
            "OK-ACCESS-KEY": self.key,
            "OK-ACCESS-SIGN": sign,
            "OK-ACCESS-TIMESTAMP": ts,
            "OK-ACCESS-PASSPHRASE": self.passphrase,
            "Content-Type": "application/json",
            "User-Agent": UA,
            "Accept": "application/json",
        }
        if self.simulated:
            h["x-simulated-trading"] = "1"
        return h

    # ---------- 底层请求 ----------
    def _request(self, method: str, path: str, params: dict | None = None,
                 body: dict | None = None, auth: bool = True) -> dict:
        qs = ""
        if params:
            clean = {k: v for k, v in params.items() if v is not None}
            if clean:
                qs = "?" + urllib.parse.urlencode(clean)
        url = BASE + path + qs
        body_str = json.dumps(body, separators=(",", ":")) if body else ""

        headers = self._headers(method, path + qs, body_str) if auth else {
            "User-Agent": UA, "Accept": "application/json"}
        if body_str:
            headers["Content-Type"] = "application/json"

        self.rl.acquire()
        req = urllib.request.Request(
            url,
            data=body_str.encode() if body_str else None,
            headers=headers,
            method=method.upper(),
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                payload = json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "ignore")[:300]
            raise OkxError(str(e.code), detail, path) from None
        except Exception as e:  # noqa: BLE001
            raise OkxError("NETWORK", str(e), path) from None

        if payload.get("code") != "0":
            raise OkxError(payload.get("code", "?"), payload.get("msg", ""), path)
        return payload

    # ---------- 公共行情 ----------
    def server_time(self) -> int:
        return int(self._request("GET", "/api/v5/public/time", auth=False)["data"][0]["ts"])

    def ticker(self, inst_id: str) -> dict:
        return self._request("GET", "/api/v5/market/ticker",
                             {"instId": inst_id}, auth=False)["data"][0]

    def index_price(self, inst_id: str = "BTC-USDT") -> float:
        d = self._request("GET", "/api/v5/market/index-tickers",
                          {"instId": inst_id}, auth=False)["data"][0]
        return float(d["idxPx"])

    def candles(self, inst_id: str, bar: str = "15m", limit: int = 300,
                after: str | None = None, history: bool = False) -> list[list]:
        """返回按时间升序的 [[ts, o, h, l, c, vol, volCcy, volCcyQuote, confirm], ...]。"""
        path = "/api/v5/market/history-candles" if history else "/api/v5/market/candles"
        d = self._request("GET", path,
                          {"instId": inst_id, "bar": bar, "limit": str(limit),
                           "after": after}, auth=False)["data"]
        d = [c for c in d if len(c) >= 9]
        d.sort(key=lambda x: int(x[0]))
        return d

    def candles_bulk(self, inst_id: str, bar: str = "15m", total: int = 800,
                     deep: bool = False) -> list[list]:
        """分页拉取历史，用于指标和清算代理。deep=True 时走 history-candles 取更久。"""
        out: list[list] = []
        after = None
        while len(out) < total:
            batch = self.candles(inst_id, bar=bar, limit=300, after=after,
                                 history=deep)
            if not batch:
                break
            out = batch + out
            after = batch[0][0]
            if len(batch) < 300:
                break
        # 去重 + 排序
        seen, uniq = set(), []
        for c in sorted(out, key=lambda x: int(x[0])):
            if c[0] in seen:
                continue
            seen.add(c[0])
            uniq.append(c)
        return uniq[-total:]

    def funding_rate(self, inst_id: str) -> dict:
        return self._request("GET", "/api/v5/public/funding-rate",
                             {"instId": inst_id}, auth=False)["data"][0]

    def open_interest(self, inst_id: str) -> dict:
        return self._request("GET", "/api/v5/public/open-interest",
                             {"instType": "SWAP", "instId": inst_id}, auth=False)["data"][0]

    def long_short_ratio(self, ccy: str = "BTC", period: str = "1H", limit: int = 48) -> list:
        d = self._request("GET", "/api/v5/rubik/stat/contracts/long-short-account-ratio",
                          {"ccy": ccy, "period": period}, auth=False)["data"]
        return d[:limit]

    def taker_volume(self, ccy: str = "BTC", period: str = "1H", limit: int = 24) -> list:
        """主动买卖量比：[ts, sellVol, buyVol]"""
        d = self._request("GET", "/api/v5/rubik/stat/taker-volume",
                          {"ccy": ccy, "instType": "CONTRACTS", "period": period},
                          auth=False)["data"]
        return d[:limit]

    def instrument(self, inst_id: str) -> dict:
        return self._request("GET", "/api/v5/public/instruments",
                             {"instType": "SWAP", "instId": inst_id}, auth=False)["data"][0]

    # ---------- 账户 ----------
    def balance(self) -> dict:
        return self._request("GET", "/api/v5/account/balance")["data"][0]

    def equity_usdt(self) -> float:
        b = self.balance()
        return float(b.get("totalEq") or 0)

    def positions(self, inst_type: str = "SWAP") -> list[dict]:
        return self._request("GET", "/api/v5/account/positions",
                             {"instType": inst_type}).get("data", [])

    def position(self, inst_id: str, pos_side: str | None = None) -> dict | None:
        """取指定合约的持仓。双向模式下按 posSide 过滤。"""
        for p in self.positions():
            if p["instId"] != inst_id:
                continue
            if float(p.get("pos") or 0) == 0:
                continue
            if pos_side and p.get("posSide") not in (pos_side, "net"):
                continue
            return p
        return None

    def set_leverage(self, inst_id: str, lever: int, mgn_mode: str = "cross",
                     pos_side: str | None = None) -> dict:
        body = {"instId": inst_id, "lever": str(lever), "mgnMode": mgn_mode}
        if pos_side:
            body["posSide"] = pos_side
        return self._request("POST", "/api/v5/account/set-leverage", body=body)

    def set_position_mode(self, mode: str = "net_mode") -> dict:
        return self._request("POST", "/api/v5/account/set-position-mode", body={"posMode": mode})

    # ---------- 交易 ----------
    def place_order(self, inst_id: str, side: str, sz: str, td_mode: str = "cross",
                    ord_type: str = "market", px: str | None = None,
                    reduce_only: bool = False, pos_side: str | None = None,
                    cl_ord_id: str | None = None) -> dict:
        body: dict[str, Any] = {
            "instId": inst_id, "tdMode": td_mode, "side": side,
            "ordType": ord_type, "sz": sz,
        }
        if px:
            body["px"] = px
        if reduce_only:
            body["reduceOnly"] = "true"
        if pos_side:
            body["posSide"] = pos_side
        if cl_ord_id:
            body["clOrdId"] = cl_ord_id[:32]
        _r = self._request("POST", "/api/v5/trade/order", body=body)["data"][0]
        if _r.get("sCode") not in (None, "0"):
            raise OkxError(_r.get("sCode"), _r.get("sMsg", ""))
        return _r

    def place_algo_stop(self, inst_id: str, side: str, sz: str,
                        sl_px: float, td_mode: str = "cross",
                        pos_side: str | None = None,
                        cl_ord_id: str | None = None) -> dict:
        """交易所侧条件止损单（常驻，防止机器人挂掉后裸奔）。"""
        body = {
            "instId": inst_id, "tdMode": td_mode, "side": side, "sz": sz,
            "ordType": "conditional", "reduceOnly": "true",
            "slTriggerPx": f"{sl_px:.1f}", "slOrdPx": "-1",
            "slTriggerPxType": "last",
        }
        if pos_side:
            body["posSide"] = pos_side
        if cl_ord_id:
            body["algoClOrdId"] = cl_ord_id[:32]
        return self._request("POST", "/api/v5/trade/order-algo", body=body)["data"][0]

    def cancel_algo(self, algo_id: str, inst_id: str) -> dict:
        return self._request("POST", "/api/v5/trade/cancel-algos",
                             body=[{"algoId": algo_id, "instId": inst_id}])

    def algo_pending(self, inst_id: str) -> list[dict]:
        return self._request(
            "GET", "/api/v5/trade/orders-algo-pending",
            {"instType": "SWAP", "instId": inst_id, "ordType": "conditional"}
        ).get("data", [])

    def pending_orders(self, inst_id: str) -> list[dict]:
        return self._request(
            "GET", "/api/v5/trade/orders-pending",
            {"instType": "SWAP", "instId": inst_id}
        ).get("data", [])

    def fills(self, inst_id: str, limit: int = 50) -> list[dict]:
        return self._request(
            "GET", "/api/v5/trade/fills",
            {"instType": "SWAP", "instId": inst_id, "limit": str(limit)}
        ).get("data", [])

    # ---------- 工具 ----------
    def contracts_for_notional(self, inst_id: str, notional_usd: float, price: float) -> str:
        """名义价值 → 张数（向下取整到 lotSz）。"""
        ins = self.instrument(inst_id)
        ct_val = float(ins["ctVal"])          # 每张对应标的数量
        lot_sz = float(ins["lotSz"])
        qty_coin = notional_usd / price
        n = qty_coin / ct_val
        n = int(n / lot_sz) * lot_sz
        return f"{n:.{max(0, len(str(lot_sz).split('.')[-1]))}f}"
