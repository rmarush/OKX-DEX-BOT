# okx_dex_bot/run.py
import os
import random
import time
from decimal import Decimal
from pathlib import Path
from typing import Dict, List

from dotenv import load_dotenv
from eth_account import Account

from .balances import fetch_balances
from .config import (
    BUY_PART_USDT,
    BUY_SELL_TOTAL_USDT,
    MIN_BNB_FOR_GAS,
    NUM_CYCLES,
    USDT_BSC,
    TRADING_TOKENS,
    DELAY_MIN_SEC,
    DELAY_MAX_SEC,
)
from .dex import do_swap, get_usdt_value_of_token, sell_token_with_retry
from .trade_log import log_trade
from .logging_setup import setup_logger, wallet_tag
from .okx_client import OkxClient, OkxCreds
from .rpc import RpcRotator
from .utils import load_lines, short_addr

log = setup_logger()


# ---------- helpers for pretty summary ----------
def _sorted_nonzero_items(d: Dict[str, int]) -> List[tuple]:
    return sorted([(k, v) for k, v in d.items() if v > 0], key=lambda x: (-x[1], x[0]))

def format_token_counts(d: Dict[str, int]) -> str:
    items = _sorted_nonzero_items(d)
    return ", ".join(f"{k}×{v}" for k, v in items) if items else "—"


# ---------- Табличний підсумок ----------
def print_summary_table(rows: List[Dict]):
    try:
        from rich.table import Table  # type: ignore
        from rich.console import Console  # type: ignore

        table = Table(title="[bold]Per-wallet summary[/bold]", show_lines=True)
        cols = [
            ("#", "idx"),
            ("Address", "address"),
            ("Cycles", "cycles_done"),
            ("Buy USDT", "buy_vol"),
            ("Sell USDT", "sell_vol"),
            ("Exec USDT", "exec_vol"),
            ("Net USDT (−=profit)", "net_usdt"),
            ("BNB gas", "bnb_spent"),
            ("Tokens (cycles)", "tokens_str"),
            ("Target/cycle", "target_per_cycle"),
            ("Target total", "target_total"),
        ]
        for c, _ in cols:
            right = ("USDT" in c) or ("BNB" in c) or (c in {"Cycles", "#"})
            table.add_column(c, justify="right" if right else "left")

        for r in rows:
            table.add_row(
                str(r["idx"]),
                short_addr(r["address"]),
                f'{r["cycles_done"]}/{r["num_cycles"]}',
                f'{r["buy_vol"]}',
                f'{r["sell_vol"]}',
                f'{r["exec_vol"]}',
                f'{r["net_usdt"]}',
                f'{r["bnb_spent"]}',
                r["tokens_str"],
                f'{r["target_per_cycle"]}',
                f'{r["target_total"]}',
            )
        Console().print(table)
        return
    except Exception:
        headers = [
            "#", "Address", "Cycles", "Buy USDT", "Sell USDT", "Exec USDT",
            "Net USDT (−=profit)", "BNB gas", "Tokens (cycles)", "Target/cycle", "Target total",
        ]
        data = []
        for r in rows:
            data.append([
                str(r["idx"]),
                short_addr(r["address"]),
                f'{r["cycles_done"]}/{r["num_cycles"]}',
                f'{r["buy_vol"]}',
                f'{r["sell_vol"]}',
                f'{r["exec_vol"]}',
                f'{r["net_usdt"]}',
                f'{r["bnb_spent"]}',
                r["tokens_str"],
                f'{r["target_per_cycle"]}',
                f'{r["target_total"]}',
            ])
        col_w = [max(len(str(x)) for x in col) for col in zip(headers, *data)]
        fmt = " | ".join("{:<" + str(w) + "}" for w in col_w)
        line = "-+-".join("-" * w for w in col_w)
        print("\n" + fmt.format(*headers))
        print(line)
        for row in data:
            print(fmt.format(*row))


# ---------- Рандомна затримка ----------
def rand_delay(tag: str = ""):
    """Рандомна пауза перед дією (BUY/SELL) у діапазоні [DELAY_MIN_SEC, DELAY_MAX_SEC]."""
    d = random.uniform(DELAY_MIN_SEC, DELAY_MAX_SEC)
    if tag:
        log.info(f"{tag}sleep {d:.2f}s…")
    else:
        log.info(f"Delay {d:.2f}s…")
    time.sleep(d)


# ---------- Допоміжне ----------
def _choose_token() -> dict:
    """Випадковий вибір токена з урахуванням ваги (поле weight у TRADING_TOKENS)."""
    weights = [t.get("weight", 1.0) for t in TRADING_TOKENS]
    return random.choices(TRADING_TOKENS, weights=weights, k=1)[0]


# ---------- Головний сценарій ----------
def main():
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
    assert len(wallet_lines) == len(proxy_lines), "wallets.txt and proxies.txt must have the same number of lines (1 proxy = 1 wallet)"

    all_results: List[Dict] = []

    for idx, (pk_raw, proxy) in enumerate(zip(wallet_lines, proxy_lines), start=1):
        tag = wallet_tag(idx)

        log.info(f"\n[bold]=== Wallet #{idx} via proxy {proxy} ===[/bold]")
        pk = pk_raw if pk_raw.startswith("0x") else "0x" + pk_raw
        acct = Account.from_key(pk)
        log.info(f"{tag}Address: {acct.address}")

        okx = OkxClient(creds, proxy=proxy)
        rot = RpcRotator(rpc_urls, proxy=proxy)
        try:
            w3, current_rpc = rot.connect()
            log.info(f"{tag}Using RPC: {current_rpc}")
        except Exception as e:
            log.error(f"{tag}RPC connect failed: {e}")
            continue

        # OKX health
        try:
            test = okx.get("/api/v5/dex/aggregator/supported/chain", {})
            log.info(f"{tag}Auth OK. Chains supported: {len(test.get('data', []))}")
        except Exception as e:
            log.error(f"{tag}Auth failed (check API key / time sync / IP whitelist): {e}")
            continue

        # Баланси (BNB, USDT + усі торговані токени)
        try:
            start_bal = fetch_balances(okx, acct.address, TRADING_TOKENS)
            tok_line = ", ".join([f"{t['symbol']}: {start_bal[t['symbol']]}" for t in TRADING_TOKENS])
            log.info(f"{tag}Start balances — BNB: {start_bal['BNB']}, USDT: {start_bal['USDT']} | {tok_line}")

            if start_bal["BNB"] < MIN_BNB_FOR_GAS:
                log.warning(f"{tag}Skip: not enough BNB for gas (need ≥ {MIN_BNB_FOR_GAS})")
                continue
            if start_bal["USDT"] < BUY_PART_USDT and all(start_bal[t["symbol"]] == 0 for t in TRADING_TOKENS):
                log.warning(f"{tag}Skip: not enough USDT (need ≥ {BUY_PART_USDT}) and no tokens to sell")
                continue
        except Exception as e:
            log.error(f"{tag}Failed to fetch balances: {e}")
            continue

        start_bnb = start_bal["BNB"]

        # Метрики ТІЛЬКИ ДЛЯ ЦИКЛІВ
        cycle_buy_usdt = Decimal("0")
        cycle_sell_usdt = Decimal("0")
        pre_sell_usdt = Decimal("0")
        cycles_done = 0

        # лічильники токенів
        token_trade_counts: Dict[str, int] = {t["symbol"]: 0 for t in TRADING_TOKENS}
        token_presell_counts: Dict[str, int] = {t["symbol"]: 0 for t in TRADING_TOKENS}

        # --- Pre-sell для КОЖНОГО токена, якщо його вартість > $1 (не входить у метрики циклів) ---
        for t in TRADING_TOKENS:
            sym, addr, dec = t["symbol"], t["address"], t["decimals"]
            bal = start_bal.get(sym, Decimal(0))
            if bal > 0:
                try:
                    est = get_usdt_value_of_token(okx, acct.address, addr, bal, dec)
                    log.info(f"{tag}Initial {sym} ≈ {bal} (~{est} USDT)")
                    if est > Decimal("1"):
                        rand_delay(f"{tag}Pre-SELL {sym} delay: ")
                        _, usdt_back = sell_token_with_retry(
                            okx, w3, rot, acct,
                            token_addr=addr, token_decimals=dec, amount_token=bal
                        )
                        pre_sell_usdt += usdt_back
                        token_presell_counts[sym] += 1
                        log.info(f"{tag}Pre-sell USDT (excluded from cycles) ↑: {pre_sell_usdt}")
                        
                    else:
                        log.info(f"{tag}Skip initial {sym} sell: value ≤ $1")
                except Exception as e:
                    log.warning(f"{tag}{sym} valuation/sell failed: {e}")

        usdt_dec = 18  # USDT на BSC в OKX-відповідях має 18

        # --- Основні цикли ---
        for c in range(1, NUM_CYCLES + 1):
            log.info(f"\n{tag}--- Cycle {c}/{NUM_CYCLES} ---")

            # Перевіряємо, чи є USDT на покупку
            rand_delay(f"{tag}Balance check {sym} delay: ")
            bal_now = fetch_balances(okx, acct.address, TRADING_TOKENS)
            if bal_now["USDT"] < BUY_PART_USDT:
                log.warning(f"{tag}Stop: not enough USDT for next buy (need ≥ {BUY_PART_USDT}, have {bal_now['USDT']})")
                break

            # Випадковий вибір токена для цього циклу
            tok = random.choices(TRADING_TOKENS, weights=[t.get("weight", 1.0) for t in TRADING_TOKENS], k=1)[0]
            sym, addr, dec = tok["symbol"], tok["address"], tok["decimals"]
            log.info(f"{tag}Chosen token: [bold]{sym}[/bold]")

            # BUY
            try:
                rand_delay(f"{tag}Pre-BUY {sym} delay: ")
                log.info(f"{tag}Buying {sym} for {BUY_PART_USDT} USDT…")
                buy_tx, amount_token, _ = do_swap(
                    okx, w3, rot, acct,
                    from_token=USDT_BSC, to_token=addr,
                    amount_in=BUY_PART_USDT, decimals_in=usdt_dec,
                )
                cycle_buy_usdt += BUY_PART_USDT
                log.info(f"{tag}BUY tx: {buy_tx} | {sym} received ≈ {amount_token}")
                log_trade(acct.address, sym, "BUY", BUY_PART_USDT, amount_token, buy_tx)
            except Exception as e:
                log.error(f"{tag}BUY {sym} failed: {e}")
                break

            # SELL (завжди з ретраями)
            try:
                rand_delay(f"{tag}Pre-SELL {sym} delay: ")
                _, usdt_back = sell_token_with_retry(
                    okx, w3, rot, acct,
                    token_addr=addr, token_decimals=dec, amount_token=amount_token
                )
                cycle_sell_usdt += usdt_back
                token_trade_counts[sym] += 1  # рахуємо лише завершені (buy+sell) цикли
            except Exception as e:
                log.error(f"{tag}SELL {sym} failed unexpectedly: {e}")
                break

            cycles_done += 1
            time.sleep(1.0 + random.uniform(0.0, 0.8))  # невелика додаткова пауза між циклами

        # Кінцеві баланси і підсумки
        try:
            end_bal = fetch_balances(okx, acct.address, TRADING_TOKENS)
            log.info(f"\n{tag}End balances — BNB: {end_bal['BNB']}, USDT: {end_bal['USDT']}")
        except Exception as e:
            log.error(f"{tag}Failed to fetch end balances: {e}")
            continue

        executed_volume = (cycle_buy_usdt + cycle_sell_usdt).quantize(Decimal("0.000001"))
        net_usdt_cycles = (cycle_buy_usdt - cycle_sell_usdt).quantize(Decimal("0.000001"))
        bnb_spent = (start_bnb - end_bal["BNB"]).quantize(Decimal("0.00000001"))

        # ---- Summary per wallet ----
        log.info(f"{tag}Summary:")
        log.info(f"{tag}  Pre-sell USDT (excluded):      {pre_sell_usdt}")
        log.info(f"{tag}  Cycles done: {cycles_done}/{NUM_CYCLES}")
        log.info(f"{tag}  Buy volume (USDT):  {cycle_buy_usdt}")
        log.info(f"{tag}  Sell volume (USDT): {cycle_sell_usdt}")
        log.info(f"{tag}  Executed volume ≈   {executed_volume} USDT")
        log.info(f"{tag}  Net USDT change (− = прибуток): {net_usdt_cycles}")
        log.info(f"{tag}  BNB spent on gas (approx):      {bnb_spent}")
        log.info(f"{tag}  Tokens traded (cycles): {format_token_counts(token_trade_counts)}")
        if any(token_presell_counts.values()):
            log.info(f"{tag}  Pre-sold tokens (excluded): {format_token_counts(token_presell_counts)}")
        log.info(f"{tag}  Target per cycle: ~{BUY_SELL_TOTAL_USDT} USDT  → Target total: ~{BUY_SELL_TOTAL_USDT * NUM_CYCLES} USDT")

        all_results.append({
            "idx": idx,
            "address": acct.address,
            "cycles_done": cycles_done,
            "num_cycles": NUM_CYCLES,
            "buy_vol": f"{cycle_buy_usdt}",
            "sell_vol": f"{cycle_sell_usdt}",
            "exec_vol": f"{executed_volume}",
            "net_usdt": f"{net_usdt_cycles}",
            "bnb_spent": f"{bnb_spent}",
            "tokens_str": format_token_counts(token_trade_counts),
            "target_per_cycle": f"{BUY_SELL_TOTAL_USDT}",
            "target_total": f"{(BUY_SELL_TOTAL_USDT * NUM_CYCLES)}",
        })

    if all_results:
        print()
        print_summary_table(all_results)
    else:
        log.warning("No wallets processed successfully — nothing to summarize.")
