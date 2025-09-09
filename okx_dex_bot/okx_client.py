import base64
import dataclasses
import datetime as dt
import hmac
import hashlib
import json
import random
import time
from typing import Dict, Optional, Tuple

import requests

from .config import OKX_HOST
from .logging_setup import setup_logger

log = setup_logger()

@dataclasses.dataclass
class OkxCreds:
    key: str
    secret: str
    passphrase: str


class OkxClient:
    """OKX DEX REST клієнт з обробкою 429 (rate limit)."""

    def __init__(self, creds: OkxCreds, proxy: Optional[str] = None):
        self.creds = creds
        self.session = requests.Session()
        if proxy:
            self.session.proxies.update({"http": proxy, "https": proxy})
        self.session.headers.update({"Content-Type": "application/json"})

    def _signature(self, method: str, path: str, body: Optional[str]) -> Tuple[str, str]:
        ts = dt.datetime.utcnow().isoformat(timespec="milliseconds") + "Z"
        prehash = ts + method.upper() + path + (body or "")
        sig = hmac.new(self.creds.secret.encode(), prehash.encode(), hashlib.sha256).digest()
        return base64.b64encode(sig).decode(), ts

    def _headers(self, sig: str, ts: str) -> Dict[str, str]:
        return {
            "OK-ACCESS-KEY": self.creds.key,
            "OK-ACCESS-SIGN": sig,
            "OK-ACCESS-TIMESTAMP": ts,
            "OK-ACCESS-PASSPHRASE": self.creds.passphrase,
        }

    def _send_with_retries(self, prepped, *, max_retries=5, base_sleep=1.0):
        for attempt in range(1, max_retries + 1):
            resp = self.session.send(prepped, timeout=30)
            if resp.status_code != 429:
                resp.raise_for_status()
                return resp
            ra = resp.headers.get("Retry-After")
            if ra and ra.isdigit():
                sleep_s = int(ra)
            else:
                sleep_s = min(base_sleep * (2 ** (attempt - 1)), 8.0)
            sleep_s += random.uniform(0, 0.5)
            log.warning(f"[429] Rate limited. Retry in {sleep_s:.2f}s (attempt {attempt}/{max_retries})")
            time.sleep(sleep_s)
        resp.raise_for_status()

    def get(self, path: str, params: Dict[str, str]) -> dict:
        req = requests.Request("GET", OKX_HOST + path, params=params)
        prepped = self.session.prepare_request(req)
        request_path = prepped.path_url
        sig, ts = self._signature("GET", request_path, None)
        prepped.headers.update(self._headers(sig, ts))
        resp = self._send_with_retries(prepped)
        return resp.json()

    def post(self, path: str, payload: dict) -> dict:
        body = json.dumps(payload, separators=(",", ":"))
        req = requests.Request("POST", OKX_HOST + path, data=body)
        prepped = self.session.prepare_request(req)
        request_path = prepped.path_url
        sig, ts = self._signature("POST", request_path, body)
        prepped.headers.update(self._headers(sig, ts))
        prepped.headers["Content-Type"] = "application/json"
        resp = self._send_with_retries(prepped)
        return resp.json()
