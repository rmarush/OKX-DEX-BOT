# okx_dex_bot/trade_log.py
from __future__ import annotations
import csv
from pathlib import Path
from datetime import datetime, timezone
from decimal import Decimal

from .config import TRADE_LOG_PATH

_HEADERS = ["timestamp_iso","wallet","token","side","usdt_amount","token_amount","tx_hash"]

def log_trade(wallet: str, token: str, side: str, usdt_amount: Decimal, token_amount: Decimal, tx_hash: str):
    TRADE_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    new_file = not TRADE_LOG_PATH.exists()
    with TRADE_LOG_PATH.open("a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(_HEADERS)
        ts = datetime.now(timezone.utc).isoformat()
        w.writerow([ts, wallet, token, side, str(usdt_amount), str(token_amount), tx_hash])
