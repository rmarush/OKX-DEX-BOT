from __future__ import annotations

import random
import time
from decimal import Decimal, ROUND_DOWN
from typing import Optional, Tuple, List
from .trade_log import log_trade
from eth_account import Account
from web3 import Web3

from .config import (
    CHAIN_INDEX,
    SLIPPAGE,
    USDT_BSC,
    ENDPOINT_QUOTE,
    ENDPOINT_SWAP,
    ENDPOINT_APPROVE,
    RESET_APPROVE_ON_FAIL,
    REAPPROVE_EVERY_N_FAILURES,
    APPROVE_RESET_ERRORS,
    CHUNK_PRIMARY_RATIOS,
    CHUNK_SECONDARY_RATIOS,
    CHUNK_MAX_ATTEMPTS,
    SWAP_SEND_MAX_ATTEMPTS,
    SECONDARY_EARLY_EXIT_SOLD,
)
from .abi import ERC20_MIN_ABI
from .logging_setup import setup_logger
from .okx_client import OkxClient
from .rpc import RpcRotator
from .utils import to_base_units, raw_tx_bytes, parse_int_auto

log = setup_logger()


# ---------- QUOTES ----------
def get_quote(
    okx: OkxClient,
    *,
    from_token: str,
    to_token: str,
    amount_in: Decimal,
    decimals_in: int,
    user_addr: str,
    slippage: Optional[Decimal] = None,   # <<< приймаємо довільний slippage
) -> dict:
    amount_base = int(to_base_units(amount_in, decimals_in))
    params = {
        "chainIndex": CHAIN_INDEX,
        "fromTokenAddress": from_token,
        "toTokenAddress": to_token,
        "amount": str(amount_base),
        "swapMode": "exactIn",
        "slippage": str(slippage if slippage is not None else SLIPPAGE),
        "userWalletAddress": user_addr,
    }
    q = okx.get(ENDPOINT_QUOTE, params)
    d = (q.get("data") or [{}])[0]
    if not d:
        raise RuntimeError(f"Quote API returned empty data: {q}")
    return d


def get_usdt_value_of_token(
    okx: OkxClient,
    user_addr: str,
    token_addr: str,
    amount_token: Decimal,
    token_decimals: int,
) -> Decimal:
    q = get_quote(
        okx,
        from_token=token_addr,
        to_token=USDT_BSC,
        amount_in=amount_token,
        decimals_in=token_decimals,
        user_addr=user_addr,
    )
    to_amount_base = int(q.get("toTokenAmount") or 0)
    # USDT на BSC в OKX має 18
    return Decimal(to_amount_base) / (Decimal(10) ** 18)

def _okx_approve_payload(okx: OkxClient, token_addr: str, amount_base: int) -> Tuple[str, str, Optional[int], Optional[int]]:
    """Повертає (spender, data, gasLimit, gasPrice) з /approve-transaction для поточного маршруту."""
    params = {"chainIndex": CHAIN_INDEX, "tokenContractAddress": Web3.to_checksum_address(token_addr), "approveAmount": str(amount_base)}
    res = okx.get("/api/v5/dex/aggregator/approve-transaction", params)
    item = (res.get("data") or [{}])[0]
    if not item:
        raise RuntimeError(f"approve-transaction returned empty: {res}")
    spender = Web3.to_checksum_address(item["dexContractAddress"])
    data    = item["data"]
    gl      = int(item.get("gasLimit") or 0) or None
    gp      = int(item.get("gasPrice") or 0) or None
    return spender, data, gl, gp

def _force_reset_allowance(
    okx: OkxClient, w3: Web3, rot: RpcRotator, acct: Account, token_addr: str, amount_base: int, *, sleep_after: float = 0.6
) -> None:
    token = Web3.to_checksum_address(token_addr)
    spender, data, gl, gp = _okx_approve_payload(okx, token, amount_base)

    erc20 = w3.eth.contract(address=token, abi=ERC20_MIN_ABI)
    # approve(0)
    log.info(f"Approve(0) → {spender}")
    try:
        nonce = w3.eth.get_transaction_count(acct.address)
        tx0 = erc20.functions.approve(spender, 0).build_transaction({
            "from": acct.address, "chainId": int(CHAIN_INDEX), "nonce": nonce, "gasPrice": w3.eth.gas_price,
        })
        try:
            tx0["gas"] = int(w3.eth.estimate_gas(tx0) * 1.2)
        except Exception:
            tx0["gas"] = 120000
        signed0 = w3.eth.account.sign_transaction(tx0, private_key=acct.key)
        h0 = w3.eth.send_raw_transaction(raw_tx_bytes(signed0))
        rec0 = w3.eth.wait_for_transaction_receipt(h0, timeout=180)
        if rec0.status != 1:
            raise RuntimeError(f"Approve(0) failed: {h0.hex()}")
        log.info("Approve(0) ok")
    except Exception as e:
        log.warning(f"Approve(0) error (will still try re-approve): {e}")

    time.sleep(sleep_after)

    # main approve using OKX calldata (to the token address)
    log.info("Approve(new amount)…")
    last_err = None
    for attempt in range(1, SWAP_SEND_MAX_ATTEMPTS + 1):
        try:
            nonce = w3.eth.get_transaction_count(acct.address)
            tx = {
                "from": acct.address,
                "to": token,
                "data": data,  # encoded approve(spender, amount)
                "value": 0,
                "chainId": int(CHAIN_INDEX),
                "nonce": nonce,
                "gasPrice": gp or w3.eth.gas_price,
            }
            try:
                tx["gas"] = gl or int(w3.eth.estimate_gas(tx) * 1.2)
            except Exception:
                tx["gas"] = gl or 150000
            signed = w3.eth.account.sign_transaction(tx, private_key=acct.key)
            h = w3.eth.send_raw_transaction(raw_tx_bytes(signed))
            rec = w3.eth.wait_for_transaction_receipt(h, timeout=180)
            if rec.status != 1:
                raise RuntimeError(f"Approve(new) status=0: {h.hex()}")
            log.info("Approve(new) ok")
            return
        except Exception as e:
            last_err = e
            log.warning(f"Re-approve attempt {attempt} failed: {e}")
            # bump gas + rotate RPC
            try:
                gp = int((gp or w3.eth.gas_price) * 1.15)
            except Exception:
                gp = None
            w3, _ = rot.rotate_and_connect()
            time.sleep(0.5)
    raise last_err or RuntimeError("Re-approve failed")


# ---------- APPROVE ----------
def maybe_approve(
    okx: OkxClient, w3: Web3, rot: RpcRotator, acct: Account, token: str, amount_base: int
) -> Optional[str]:
    token_addr = Web3.to_checksum_address(token)
    erc20 = w3.eth.contract(address=token_addr, abi=ERC20_MIN_ABI)

    # Ask OKX for spender+calldata
    params = {"chainIndex": CHAIN_INDEX, "tokenContractAddress": token_addr, "approveAmount": str(amount_base)}
    res = okx.get(ENDPOINT_APPROVE, params)
    item = (res.get("data") or [{}])[0]
    if not item:
        log.warning("approve endpoint returned empty data, skipping approve")
        return None

    okx_spender = Web3.to_checksum_address(item["dexContractAddress"])
    okx_data = item["data"]
    okx_gl = int(item.get("gasLimit") or 0) or None
    okx_gp = int(item.get("gasPrice") or 0) or None

    current = erc20.functions.allowance(acct.address, okx_spender).call()
    log.info(f"Allowance now: {current}; need: {amount_base}; spender: {okx_spender}")

    if current >= amount_base:
        return None

    # helper: send approve tx with optional gas hints
    def _send_approve_data(to_addr: str, data: str, gas: Optional[int], gas_price: Optional[int]) -> str:
        nonlocal w3
        last_err = None
        for attempt in range(1, 5):
            try:
                nonce = w3.eth.get_transaction_count(acct.address)
                tx = {
                    "from": acct.address,
                    "to": Web3.to_checksum_address(to_addr),
                    "data": data,
                    "value": 0,
                    "chainId": int(CHAIN_INDEX),
                    "nonce": nonce,
                    "gasPrice": gas_price or w3.eth.gas_price,
                }
                try:
                    tx["gas"] = gas or int(w3.eth.estimate_gas(tx) * 1.2)
                except Exception:
                    tx["gas"] = gas or 100000
                signed = w3.eth.account.sign_transaction(tx, private_key=acct.key)
                raw = raw_tx_bytes(signed); assert raw
                h = w3.eth.send_raw_transaction(raw)
                rec = w3.eth.wait_for_transaction_receipt(h, timeout=180)
                if rec.status != 1:
                    raise RuntimeError(f"Approve tx status=0: {h.hex()}")
                # невелика пауза, щоб allowance “прилетів” на всіх RPC
                time.sleep(0.6)
                return h.hex()
            except Exception as e:
                last_err = e
                log.warning(f"approve send attempt {attempt} failed: {e}")
                try:
                    gas_price = int((gas_price or w3.eth.gas_price) * 1.15)
                except Exception:
                    gas_price = None
                w3, _ = rot.rotate_and_connect()
                time.sleep(0.5)
        raise last_err

    # Якщо є non-zero allowance — обнулити
    if current > 0:
        log.info("Approve(0)…")
        zero_tx = erc20.functions.approve(okx_spender, 0).build_transaction({
            "from": acct.address,
            "chainId": int(CHAIN_INDEX),
            "nonce": w3.eth.get_transaction_count(acct.address),
            "gasPrice": w3.eth.gas_price,
        })
        try:
            zero_tx["gas"] = int(w3.eth.estimate_gas(zero_tx) * 1.2)
        except Exception:
            zero_tx["gas"] = 90000
        signed0 = w3.eth.account.sign_transaction(zero_tx, private_key=acct.key)
        raw0 = raw_tx_bytes(signed0); assert raw0
        h0 = w3.eth.send_raw_transaction(raw0)
        rec0 = w3.eth.wait_for_transaction_receipt(h0, timeout=180)
        if rec0.status != 1:
            raise RuntimeError(f"Approve(0) failed: {h0.hex()}")
        log.info("Approve(0) ok")

    # Основний approve через calldata OKX
    return _send_approve_data(token_addr, okx_data, okx_gl, okx_gp)


# ---------- SWAP ----------
def do_swap(
    okx: OkxClient,
    w3: Web3,
    rot: RpcRotator,
    acct: Account,
    *,
    from_token: str,
    to_token: str,
    amount_in: Decimal,
    decimals_in: int,
    slippage: Optional[Decimal] = None,
    max_attempts: int = 5,
) -> Tuple[str, Decimal, int]:
    # 0) Quote з урахуванням slippage
    quote = get_quote(
        okx,
        from_token=from_token,
        to_token=to_token,
        amount_in=amount_in,
        decimals_in=decimals_in,
        user_addr=acct.address,
        slippage=slippage,
    )

    # 1) Approve за потреби
    if from_token.lower() != "0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee":
        amount_base = int(to_base_units(amount_in, decimals_in))
        maybe_approve(okx, w3, rot, acct, from_token, amount_base)

    # 2) /swap (з тим же slippage)
    amount_base = int(to_base_units(amount_in, decimals_in))
    swap_params = {
        "chainIndex": CHAIN_INDEX,
        "fromTokenAddress": from_token,
        "toTokenAddress": to_token,
        "amount": str(amount_base),
        "swapMode": "exactIn",
        "slippage": str(slippage if slippage is not None else SLIPPAGE),
        "userWalletAddress": acct.address,
    }
    swap_res = okx.get(ENDPOINT_SWAP, swap_params)
    d = (swap_res.get("data") or [{}])[0]
    if not d:
        raise RuntimeError(f"Swap API returned empty data: {swap_res}")

    tx_obj = d.get("tx") or d

    to_addr = (
        tx_obj.get("to")
        or tx_obj.get("toAddress")
        or d.get("to")
        or d.get("toAddress")
    )
    data = (
        tx_obj.get("data")
        or tx_obj.get("calldata")
        or tx_obj.get("input")
        or tx_obj.get("inputData")
        or d.get("data")
        or d.get("calldata")
        or d.get("input")
    )
    value = parse_int_auto(
        tx_obj.get("value") or tx_obj.get("ethValue") or d.get("value") or d.get("ethValue") or 0
    )
    gas = parse_int_auto(tx_obj.get("gas") or d.get("gas"))
    gas_price = parse_int_auto(tx_obj.get("gasPrice") or d.get("gasPrice"))
    if gas_price is None:
        gas_price = parse_int_auto(
            tx_obj.get("maxFeePerGas") or d.get("maxFeePerGas") or tx_obj.get("maxPriorityFeePerGas") or d.get("maxPriorityFeePerGas")
        )

    if not to_addr or not data:
        log.error("Swap tx is missing 'to' or 'data' fields")
        raise KeyError("Swap tx is missing 'to' or 'data' fields")

    to_addr = Web3.to_checksum_address(to_addr)
    if value is None:
        value = 0

    # 3) Надсилання з ретраями і ротацією RPC
    last_error = None
    for attempt in range(1, 3):
        try:
            nonce = w3.eth.get_transaction_count(acct.address)
            tx = {
                "from": acct.address,  # важливо для деяких RPC
                "to": to_addr,
                "value": value,
                "data": data,
                "chainId": int(CHAIN_INDEX),
                "nonce": nonce,
            }
            effective_gas_price = gas_price or parse_int_auto(w3.eth.gas_price) or 0
            if effective_gas_price <= 0:
                effective_gas_price = int(3e9)  # 3 gwei fallback
            tx["gasPrice"] = effective_gas_price

            if gas:
                tx["gas"] = gas
            else:
                try:
                    tx["gas"] = int(w3.eth.estimate_gas(tx) * 1.2)
                except Exception:
                    tx["gas"] = 300000

            signed = w3.eth.account.sign_transaction(tx, private_key=acct.key)
            raw = raw_tx_bytes(signed); assert raw
            tx_hash = w3.eth.send_raw_transaction(raw)
            rec = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=180)
            if rec.status != 1:
                raise RuntimeError(f"Swap failed: {tx_hash.hex()}")

            # 4) Очікуваний вихід (з урахуванням toToken.decimals)
            to_token_info = d.get("toToken") or quote.get("toToken") or {}
            to_dec = int(to_token_info.get("decimal", 18))
            min_receive = d.get("minReceiveAmount") or d.get("toTokenAmount") or quote.get("toTokenAmount") or "0"
            out_base = int(min_receive)
            out_human = Decimal(out_base) / (Decimal(10) ** to_dec)
            return tx_hash.hex(), out_human, out_base

        except Exception as e:
            last_error = e
            log.warning(f"swap send attempt {attempt} failed: {e}")
            try:
                if gas_price:
                    gas_price = int(gas_price * 1.15)
                else:
                    gp = parse_int_auto(w3.eth.gas_price)
                    gas_price = int(gp * 1.2) if gp else None
            except Exception:
                gas_price = None
            w3, _ = rot.rotate_and_connect()
            time.sleep(0.7)

    raise last_error or RuntimeError("Swap failed after retries")


# ---------- SELL with adaptive slippage ----------
def _make_chunks(total: Decimal, percents: List[Decimal]) -> List[Decimal]:
    # генерує суми по відсотках; останній шматок = залишок, щоб уникнути втрати на округленні
    q = Decimal("0.000000000000000001")
    chunks = []
    acc = Decimal("0")
    for i, p in enumerate(percents):
        if i < len(percents) - 1:
            part = (total * p).quantize(q, rounding=ROUND_DOWN)
            chunks.append(part); acc += part
        else:
            chunks.append((total - acc).quantize(q))
    # фільтруємо нульові
    return [c for c in chunks if c > 0]

def _maybe_reset_allowance_on_fail(msg_lower: str, attempt: int) -> bool:
    if not RESET_APPROVE_ON_FAIL:
        return False
    if (attempt % REAPPROVE_EVERY_N_FAILURES) == 0:
        return True
    return any(key in msg_lower for key in APPROVE_RESET_ERRORS)

def _build_chunks(amount: Decimal, ratios: List[Decimal]) -> List[Decimal]:
    """
    Робимо дроблення amount за ratios (сума коефіцієнтів може бути != 1 через округлення).
    Останній елемент коригуємо, щоб сума дорівнювала amount.
    """
    if amount <= 0:
        return []
    # 18 знаків — під токени з 18 decimals
    q = Decimal("0.000000000000000001")
    parts = []
    acc = Decimal("0")
    for i, r in enumerate(ratios):
        if i < len(ratios) - 1:
            p = (amount * r).quantize(q, rounding=ROUND_DOWN)
            parts.append(p)
            acc += p
        else:
            parts.append((amount - acc).quantize(q, rounding=ROUND_DOWN))
    # Прибрати нулі, раптом
    return [p for p in parts if p > 0]

def _sell_once(
    okx: OkxClient,
    w3: Web3,
    rot: RpcRotator,
    acct: Account,
    token_addr: str,
    amount_token: Decimal,
    token_decimals: int,
) -> Tuple[str, Decimal]:
    """
    Один виклик свопу token -> USDT із внутрішніми ретраями do_swap (не змінюємо сліпедж).
    Кидає виняток, якщо do_swap не зміг завершити.
    """
    tx, usdt_back, _ = do_swap(
        okx, w3, rot, acct,
        from_token=token_addr, to_token=USDT_BSC,
        amount_in=amount_token, decimals_in=token_decimals,
    )
    return tx, usdt_back

def _maybe_reset_approve_on_error(
    err_msg_lower: str,
    *,
    okx: OkxClient,
    w3: Web3,
    rot: RpcRotator,
    acct: Account,
    token_addr: str,
    amount_token: Decimal,
    token_decimals: int,
):
    """Логіка умовного пере-апрува після фейлу."""
    need_reset = False
    if RESET_APPROVE_ON_FAIL and (
        any(e in err_msg_lower for e in APPROVE_RESET_ERRORS)
    ):
        need_reset = True
    if need_reset:
        try:
            amount_base = int(to_base_units(amount_token, token_decimals))
            log.warning("Will reset allowance and re-approve before next SELL attempt…")
            _force_reset_allowance(okx, w3, rot, acct, token_addr, amount_base)
        except Exception as re:
            log.warning(f"Allowance reset failed (will still retry): {re}")

def sell_token_with_retry(
    okx: OkxClient,
    w3: Web3,
    rot: RpcRotator,
    acct: Account,
    token_addr: str,
    amount_token: Decimal,
    token_decimals: int = 18,
    *,
    symbol: str = "",
    max_sleep: int = 60,
):
    """
    Продає token → USDT. Якщо прямий SELL не проходить (do_swap вже з ретраями),
    переходимо в chunk-режим:
      1) primary split (60/30/10), кожен chunk до 3 спроб продати;
      2) якщо якийсь chunk не продався — додаткове друге ділення саме цього chunk на 50/30/20,
         знову по 3 спроби на під-chunks;
      3) якщо й це не допомогло — завершуємо з частковим результатом (переходимо до наступного циклу).
    Повертає (last_tx_hash або "", сумарний USDT_back).
    """
    sym = symbol or token_addr
    # --- 0) Спроба продати все разом (do_swap має внутрішні 5 ретраїв)
    try:
        log.info(f"Attempt 1: SELL {amount_token} of {token_addr} → USDT (slippage {SLIPPAGE*100:.3f}%)…")
        tx, usdt_back = _sell_once(okx, w3, rot, acct, token_addr, amount_token, token_decimals)
        log.info(f"SELL tx: {tx} | USDT back ≈ {usdt_back}")
        log_trade(acct.address, sym, "SELL", usdt_back, amount_token, tx)
        return tx, usdt_back
    except Exception as e:
        log.warning(f"Direct SELL failed: {e}")
        _maybe_reset_approve_on_error(str(e).lower(),
                                      okx=okx, w3=w3, rot=rot, acct=acct,
                                      token_addr=token_addr,
                                      amount_token=amount_token,
                                      token_decimals=token_decimals)

    # --- 1) PRIMARY CHUNKS
    primary_chunks = _build_chunks(amount_token, CHUNK_PRIMARY_RATIOS)
    log.warning(f"Switching to chunked SELL ({len(primary_chunks)} parts): {primary_chunks}")
    total_usdt = Decimal("0")
    last_tx = ""

    for i, chunk_amt in enumerate(primary_chunks, start=1):
        # На кожен первинний chunk даємо CHUNK_MAX_ATTEMPTS спроб
        ok_chunk = False
        for attempt in range(1, CHUNK_MAX_ATTEMPTS + 1):
            try:
                log.info(f"Chunk {i}/{len(primary_chunks)}: SELL {chunk_amt}… (try {attempt}/{CHUNK_MAX_ATTEMPTS})")
                tx, usdt_back = _sell_once(okx, w3, rot, acct, token_addr, chunk_amt, token_decimals)
                total_usdt += usdt_back
                last_tx = tx
                ok_chunk = True
                break
            except Exception as e:
                log.warning(f"swap send attempt {attempt} failed: {e}")
                _maybe_reset_approve_on_error(str(e).lower(),
                                              okx=okx, w3=w3, rot=rot, acct=acct,
                                              token_addr=token_addr,
                                              amount_token=chunk_amt,
                                              token_decimals=token_decimals)
                w3, _ = rot.rotate_and_connect()
                sleep_s = min(2 ** (attempt - 1), max_sleep) + random.uniform(0.2, 0.6)
                log.warning(f"Chunk SELL failed: {e}. Retrying this chunk in {sleep_s:.2f}s…")
                time.sleep(sleep_s)

        if ok_chunk:
            continue  # цей первинний chunk продано, йдемо до наступного

        # --- 2) SECONDARY SPLIT для проблемного первинного chunk
        secondary = _build_chunks(chunk_amt, CHUNK_SECONDARY_RATIOS)
        log.warning("Will split this chunk once more: %s", secondary)

        sold_sub_count = 0  # скільки під-чанків уже продали успішно

        for j, sub_amt in enumerate(secondary, start=1):
            sold_sub = False
            for attempt in range(1, CHUNK_MAX_ATTEMPTS + 1):  # менше спроб на під-чанк
                try:
                    log.info(
                        f"  Sub-chunk {j}/{len(secondary)} of chunk {i}: SELL {sub_amt}… "
                        f"(try {attempt}/{CHUNK_MAX_ATTEMPTS})"
                    )
                    tx, usdt_back = _sell_once(
                        okx, w3, rot, acct, token_addr, sub_amt, token_decimals
                    )
                    total_usdt += usdt_back
                    last_tx = tx
                    sold_sub = True
                    sold_sub_count += 1
                    break
                except Exception as e:
                    log.warning(f"  sub-chunk attempt {attempt} failed: {e}")
                    _maybe_reset_approve_on_error(
                        str(e).lower(),
                        okx=okx, w3=w3, rot=rot, acct=acct,
                        token_addr=token_addr,
                        amount_token=sub_amt,
                        token_decimals=token_decimals
                    )
                    w3, _ = rot.rotate_and_connect()
                    sleep_s = min(2 ** (attempt - 1), max_sleep) + random.uniform(0.2, 0.6)
                    log.warning(
                        f"  Sub-chunk SELL failed: {e}. Retrying this sub-chunk in {sleep_s:.2f}s…"
                    )
                    time.sleep(sleep_s)

            # ✅ Ранній вихід: якщо вже продали достатньо під-чанків — зупиняємось
            if sold_sub_count >= SECONDARY_EARLY_EXIT_SOLD:
                log.warning(
                    f"  Early exit on secondary split: sold {sold_sub_count} sub-chunks "
                    f"(target {SECONDARY_EARLY_EXIT_SOLD}). Moving to next cycle."
                )
                return last_tx, total_usdt

            if not sold_sub:
                # Після другого ділення і спроб — здаємось по цьому під-чанку → переходимо до наступного циклу
                log.warning(
                    "  Sub-chunk still unsold after secondary split and retries — moving to next cycle."
                )
                return last_tx, total_usdt

        # якщо сюди дійшли — цей первинний chunk розпродали під-чанками й продовжуємо

    # Всі первинні chunks (разом із можливими secondary) продані або частково → повертаємо суму
    return last_tx, total_usdt