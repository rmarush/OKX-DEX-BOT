from __future__ import annotations

import csv
import os
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from dotenv import load_dotenv
from eth_account import Account

from .balances import fetch_balances
from .config import (
    TRADING_TOKENS,
    BUY_SELL_TOTAL_USDT,
    USDT_BSC,
    CHAIN_INDEX,
    TRADE_LOG_PATH,   # e.g. "data/trades.csv"
)
from .dex import get_usdt_value_of_token
from .logging_setup import setup_logger, wallet_tag
from .okx_client import OkxClient, OkxCreds
from .rpc import RpcRotator
from .utils import load_lines, short_addr

log = setup_logger()


@dataclass
class TradeRow:
    ts: datetime
    wallet: str
    token: str
    side: str           # "BUY" | "SELL"
    usdt_amount: Decimal
    token_amount: Decimal
    tx_hash: str

def _parse_dt(s: str) -> Optional[datetime]:
    try:
        # допускаємо з або без 'Z'
        if s.endswith("Z"):
            return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None

def _read_trade_log(path: Path) -> List[TradeRow]:
    rows: List[TradeRow] = []
    if not path.exists():
        log.warning(f"Trade log not found: {path} — volumes will be 0")
        return rows
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for r in reader:
            ts = _parse_dt(r.get("timestamp_iso", "") or r.get("timestamp", ""))
            if not ts:
                continue
            try:
                rows.append(
                    TradeRow(
                        ts=ts,
                        wallet=(r.get("wallet") or "").strip(),
                        token=(r.get("token") or "").strip(),
                        side=(r.get("side") or "").strip().upper(),
                        usdt_amount=Decimal(str(r.get("usdt_amount", "0") or "0")),
                        token_amount=Decimal(str(r.get("token_amount", "0") or "0")),
                        tx_hash=(r.get("tx_hash") or "").strip(),
                    )
                )
            except Exception:
                continue
    return rows

def _volumes_for_window(
    trades: List[TradeRow],
    wallet_addrs: List[str],
    token_syms: List[str],
    start: datetime,
    end: datetime,
) -> Tuple[Decimal, Dict[str, Decimal], Dict[str, Decimal]]:
    """
    Повертає:
      total_usdt_volume, per_token_usdt_volume, per_token_token_amount
    де volume = сума BUY.usdt_amount + SELL.usdt_amount (тобто виконаний обсяг).
    """
    total = Decimal("0")
    per_tok_usdt: Dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    per_tok_amt: Dict[str, Decimal] = defaultdict(lambda: Decimal("0"))

    wl = set(a.lower() for a in wallet_addrs)
    allowed = set(token_syms)

    for tr in trades:
        if tr.wallet.lower() not in wl:
            continue
        if tr.token not in allowed:
            continue
        if not (start <= tr.ts <= end):
            continue
        # Виконаний обсяг рахуємо по USDT-стороні незалежно від side
        total += tr.usdt_amount
        per_tok_usdt[tr.token] += tr.usdt_amount
        # Додатково накопичимо обсяг у токенах (просто сума absolute amount)
        per_tok_amt[tr.token] += tr.token_amount

    return total, per_tok_usdt, per_tok_amt

def _fmt_tok_map(m: Dict[str, Decimal]) -> str:
    items = [(k, v) for k, v in m.items() if v > 0]
    items.sort(key=lambda kv: (-kv[1], kv[0]))
    return ", ".join(f"{k} {v.normalize()}" for k, v in items) if items else "—"

def _fmt_usdt_map(m: Dict[str, Decimal]) -> str:
    items = [(k, v) for k, v in m.items() if v > 0]
    items.sort(key=lambda kv: (-kv[1], kv[0]))
    # округлимо до 3 знаків
    return ", ".join(f"{k} {v.quantize(Decimal('0.001'))}" for k, v in items) if items else "—"


def print_summary_table(rows: List[Dict]):
    try:
        from rich.table import Table  # type: ignore
        from rich.console import Console  # type: ignore

        table = Table(title="[bold]Wallet stats[/bold]", show_lines=True)
        cols = [
            ("#", "idx"),
            ("Address", "address"),
            ("BNB", "bnb"),
            ("USDT", "usdt"),
            ("Tokens balance", "tokens_bal_str"),
            ("Today vol (USDT)", "today_usdt"),
            ("Today by token (USDT)", "today_usdt_by_tok"),
            ("15d vol (USDT)", "d15_usdt"),
            ("15d by token (USDT)", "d15_usdt_by_tok"),
        ]
        for c, _ in cols:
            right = c in {"BNB", "USDT", "Today vol (USDT)", "15d vol (USDT)", "#"}
            table.add_column(c, justify="right" if right else "left", no_wrap=(c == "Address"))

        for r in rows:
            table.add_row(
                str(r["idx"]),
                short_addr(r["address"]),
                r["bnb"],
                r["usdt"],
                r["tokens_bal_str"],
                r["today_usdt"],
                r["today_usdt_by_tok"],
                r["d15_usdt"],
                r["d15_usdt_by_tok"],
            )
        Console().print(table)
        return
    except Exception:
        headers = [
            "#", "Address", "BNB", "USDT", "Tokens balance",
            "Today vol (USDT)", "Today by token (USDT)",
            "15d vol (USDT)", "15d by token (USDT)",
        ]
        data = []
        for r in rows:
            data.append([
                str(r["idx"]),
                short_addr(r["address"]),
                r["bnb"],
                r["usdt"],
                r["tokens_bal_str"],
                r["today_usdt"],
                r["today_usdt_by_tok"],
                r["d15_usdt"],
                r["d15_usdt_by_tok"],
            ])
        col_w = [max(len(str(x)) for x in col) for col in zip(headers, *data)]
        fmt = " | ".join("{:<" + str(w) + "}" for w in col_w)
        line = "-+-".join("-" * w for w in col_w)
        print("\n" + fmt.format(*headers))
        print(line)
        for row in data:
            print(fmt.format(*row))


def main():
    load_dotenv()
    creds = OkxCreds(
        key=os.environ.get("OKX_API_KEY", ""),
        secret=os.environ.get("OKX_API_SECRET", ""),
        passphrase=os.environ.get("OKX_API_PASSPHRASE", ""),
    )
    assert creds.key and creds.secret and creds.passphrase, "Missing OKX API credentials in env"

    # RPC — потрібен лише для health в окремих місцях; для статистики достатньо REST
    urls_env = os.environ.get("BSC_RPC_URLS", "")
    if urls_env.strip():
        rpc_urls = [u.strip() for u in urls_env.split(",") if u.strip()]
    else:
        single = os.environ.get("BSC_RPC_URL", "")
        rpc_urls = [single] if single else []

    wallet_lines = load_lines(Path("wallets.txt"))
    proxy_lines = load_lines(Path("proxies.txt"))
    assert len(wallet_lines) == len(proxy_lines), "wallets.txt and proxies.txt must have the same number of lines (1 proxy = 1 wallet)"

    # підготуємо OKX клієнти по проксі
    okx_by_proxy: Dict[str, OkxClient] = {}
    for proxy in proxy_lines:
        if proxy not in okx_by_proxy:
            okx_by_proxy[proxy] = OkxClient(creds, proxy=proxy)

    # читаємо трейд-лог
    trade_log = _read_trade_log(Path(TRADE_LOG_PATH))

    all_rows: List[Dict] = []
    # часові вікна
    now = datetime.now(timezone.utc)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    d15_start = now - timedelta(days=15)

    # список символів токенів із конфіга
    token_syms = [t["symbol"] for t in TRADING_TOKENS]

    # Зберемо адреси гаманців (для підрахунку по трейд-логу)
    wallet_addrs: List[str] = []
    for pk_raw in wallet_lines:
        pk = pk_raw if pk_raw.startswith("0x") else "0x" + pk_raw
        acct = Account.from_key(pk)
        wallet_addrs.append(acct.address)

    # Обчислюємо раз — по всім гаманцям — “сьогодні” та “15д” обсяги,
    # щоб потім у виводі показувати пер-гаманець (фільтрація по адресу відбувається всередині _volumes_for_window)
    # (ми перерахуємо їх окремо для кожного гаманця, щоб значення були коректні саме по ньому)
    for idx, (pk_raw, proxy) in enumerate(zip(wallet_lines, proxy_lines), start=1):
        tag = wallet_tag(idx)
        pk = pk_raw if pk_raw.startswith("0x") else "0x" + pk_raw
        acct = Account.from_key(pk)
        okx = okx_by_proxy[proxy]

        # Баланси по гаманцю
        try:
            bals = fetch_balances(okx, acct.address, TRADING_TOKENS)
            tok_bal_map = {t["symbol"]: bals.get(t["symbol"], Decimal(0)) for t in TRADING_TOKENS}
            tok_bal_str = ", ".join(f"{sym}: {amt.normalize()}" for sym, amt in tok_bal_map.items() if amt > 0) or "—"
        except Exception as e:
            log.warning(f"{tag}Failed to fetch balances: {e}")
            bals = {"BNB": Decimal(0), "USDT": Decimal(0)}
            tok_bal_str = "—"

        # Обсяги за вікнами — ТІЛЬКИ для цього гаманця:
        today_total, today_per_tok_usdt, _ = _volumes_for_window(
            trade_log, [acct.address], token_syms, day_start, now
        )
        d15_total, d15_per_tok_usdt, _ = _volumes_for_window(
            trade_log, [acct.address], token_syms, d15_start, now
        )

        all_rows.append({
            "idx": idx,
            "address": acct.address,
            "bnb": f"{bals.get('BNB', Decimal(0))}",
            "usdt": f"{bals.get('USDT', Decimal(0))}",
            "tokens_bal_str": tok_bal_str,  # без фільтра >$1, як просили
            "today_usdt": f"{today_total.quantize(Decimal('0.001'))}",
            "today_usdt_by_tok": _fmt_usdt_map(today_per_tok_usdt),
            "d15_usdt": f"{d15_total.quantize(Decimal('0.001'))}",
            "d15_usdt_by_tok": _fmt_usdt_map(d15_per_tok_usdt),
        })

    # виводимо
    print_summary_table(all_rows)
