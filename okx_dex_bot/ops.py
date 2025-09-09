# okx_dex_bot/ops.py
from __future__ import annotations

import random
import time
from decimal import Decimal
from typing import Dict, List

from eth_account import Account
from web3 import Web3

from .balances import fetch_balances
from .config import TRADING_TOKENS, USDT_BSC
from .dex import do_swap
from .logging_setup import setup_logger
from .okx_client import OkxClient
from .rpc import RpcRotator

log = setup_logger()

def sell_all_tokens_simple(
    okx: OkxClient,
    w3: Web3,
    rot: RpcRotator,
    acct: Account,
    tokens: List[Dict] = None,
    *,
    attempts_per_token: int = 3,
) -> Dict[str, Decimal]:
    """
    Продає *весь* баланс кожного токена з конфіга у USDT.
    Без чанків, лише до attempts_per_token (3) спроб на токен.
    Повертає мапу {symbol: отримано_USDT}.
    """
    if tokens is None:
        tokens = TRADING_TOKENS

    # 1) Зняти баланси разом (BNB/USDT + усі токени з конфігурації)
    bals = fetch_balances(okx, acct.address, tokens)

    got_usdt: Dict[str, Decimal] = {}

    for t in tokens:
        sym   = t["symbol"]
        addr  = t["address"]
        dec   = t["decimals"]
        amt   = bals.get(sym, Decimal(0))

        if amt <= 0:
            continue

        log.info(f"[{acct.address[:6]}…] Try sell ALL {sym}: amount={amt}")
        success = False

        for attempt in range(1, attempts_per_token + 1):
            try:
                # ВАЖЛИВО: внутрішніх ретраїв тут НЕ хочемо, тому max_attempts=1
                tx, usdt_back, _ = do_swap(
                    okx, w3, rot, acct,
                    from_token=addr, to_token=USDT_BSC,
                    amount_in=amt, decimals_in=dec,
                    max_attempts=1,
                )
                got_usdt[sym] = got_usdt.get(sym, Decimal("0")) + usdt_back
                log.info(f"SOLD {sym} → {usdt_back} USDT | tx={tx}")
                success = True
                break
            except Exception as e:
                log.warning(f"SIMPLE SELL {sym} attempt {attempt}/{attempts_per_token} failed: {e}")
                # трохи почекати + покрутити RPC
                w3, _ = rot.rotate_and_connect()
                time.sleep(0.6 + random.uniform(0.2, 0.8))

        if not success:
            log.error(f"FAILED to sell {sym} after {attempts_per_token} attempts")

    if not got_usdt:
        log.info("No tokens were sold (zero balances or all attempts failed).")
    else:
        total = sum(got_usdt.values(), Decimal("0"))
        pretty = ", ".join(f"{k}: {v}" for k, v in got_usdt.items())
        log.info(f"SIMPLE SELL summary → {total} USDT ({pretty})")

    return got_usdt
