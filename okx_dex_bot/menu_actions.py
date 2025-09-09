# okx_dex_bot/menu_actions.py
from __future__ import annotations

import os
from decimal import Decimal
from pathlib import Path
from typing import List, Dict

from dotenv import load_dotenv
from eth_account import Account

from .logging_setup import setup_logger, wallet_tag
from .okx_client import OkxClient, OkxCreds
from .rpc import RpcRotator
from .utils import load_lines, short_addr
from .balances import fetch_balances
from .config import (
    MIN_BNB_FOR_GAS, BUY_PART_USDT, TRADING_TOKENS,
    USDT_BSC,
)
from .dex import get_usdt_value_of_token
from .ops import sell_all_tokens_simple

log = setup_logger()


def _load_common():
    """Спільна ініціалізація: креденшли, RPC URL-и, гаманці та проксі."""
    load_dotenv()
    creds = OkxCreds(
        key=os.environ.get("OKX_API_KEY", ""),
        secret=os.environ.get("OKX_API_SECRET", ""),
        passphrase=os.environ.get("OKX_API_PASSPHRASE", ""),
    )
    assert creds.key and creds.secret and creds.passphrase, "Missing OKX API credentials in env"

    urls_env = os.environ.get("BSC_RPC_URLS", "")
    if urls_env.strip():
        rpc_urls = [u.strip() for u in urls_env.split(",") if u.strip()]
    else:
        single = os.environ.get("BSC_RPC_URL", "")
        assert single, "Set BSC_RPC_URL or BSC_RPC_URLS in env"
        rpc_urls = [single]

    wallet_lines = load_lines(Path("wallets.txt"))
    proxy_lines = load_lines(Path("proxies.txt"))
    assert len(wallet_lines) == len(proxy_lines), "wallets.txt і proxies.txt повинні мати однакову кількість рядків"

    return creds, rpc_urls, wallet_lines, proxy_lines


def run_trading_for_all_wallets():
    """ПУНКТ 1: Прогнати акаунти — просто викликає твій торговий сценарій."""
    # щоб не дублювати логіку, використовуємо існуючий run.main()
    from .run import main as trading_main
    log.info("Стартуємо торговий цикл для всіх гаманців…")
    trading_main()


def sell_leftovers_for_all_wallets():
    """ПУНКТ 2: Продати залишики по кожному гаманцю."""
    creds, rpc_urls, wallet_lines, proxy_lines = _load_common()

    for idx, (pk_raw, proxy) in enumerate(zip(wallet_lines, proxy_lines), start=1):
        tag = wallet_tag(idx)
        pk = pk_raw if pk_raw.startswith("0x") else "0x" + pk_raw
        acct = Account.from_key(pk)
        okx = OkxClient(creds, proxy=proxy)
        rot = RpcRotator(rpc_urls, proxy=proxy)

        log.info(f"\n[bold]=== Wallet #{idx} via proxy {proxy} ===[/bold]")
        log.info(f"{tag}Address: {acct.address}")

        try:
            w3, current_rpc = rot.connect()
            log.info(f"{tag}Using RPC: {current_rpc}")
        except Exception as e:
            log.error(f"{tag}RPC connect failed: {e}")
            continue

        # Health-check OKX
        try:
            _ = okx.get("/api/v5/dex/aggregator/supported/chain", {})
        except Exception as e:
            log.error(f"{tag}Auth/OKX error: {e}")
            continue

        # 1) Баланси
        try:
            bals = fetch_balances(okx, acct.address, TRADING_TOKENS)
            tok_line = ", ".join([f"{t['symbol']}: {bals[t['symbol']]}" for t in TRADING_TOKENS])
            log.info(f"{tag}Balances — BNB: {bals['BNB']}, USDT: {bals['USDT']} | {tok_line}")
        except Exception as e:
            log.error(f"{tag}Failed to fetch balances: {e}")
            continue

        # 2) Проста розпродажа всіх токенів
        try:
            sold_map = sell_all_tokens_simple(okx, w3, rot, acct, tokens=TRADING_TOKENS)
            total = sum(sold_map.values(), Decimal("0"))
            pretty = ", ".join(f"{k}:{v}" for k, v in sold_map.items()) or "—"
            log.info(f"{tag}Sold totals → {total} USDT ({pretty})")
        except Exception as e:
            log.error(f"{tag}Simple sell error: {e}")


def print_stats_for_all_wallets():
    from okx_dex_bot.stats import main as stats_main
    stats_main()
