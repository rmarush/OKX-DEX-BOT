from decimal import Decimal
from web3 import Web3
from typing import List, Dict
from pathlib import Path

# Мережа / токени
CHAIN_INDEX = "56"  # BNB Smart Chain
USDT_BSC = Web3.to_checksum_address("0x55d398326f99059fF775485246999027B3197955")

# Пари для торгівлі
TRADING_TOKENS = [
    {  # вже існуючий
        "symbol": "KOGE",
        "address": Web3.to_checksum_address("0xe6DF05CE8C8301223373CF5B969AFCb1498c5528"),
        "decimals": 18,
        "weight": 1.0, 
    },
    {  # приклад 1: AICELL (18)
        "symbol": "AICELL",
        "address": Web3.to_checksum_address("0xde04da55b74435d7b9f2c5c62d9f1b53929b09aa"),
        "decimals": 18,
        "weight": 0.85,
    },
    {  # приклад 2: MTP (18)
        "symbol": "MTP",
        "address": Web3.to_checksum_address("0xbcba33bf0b3cd8d626b7a3732a3ee18a0af51bd0"),
        "decimals": 18,
        "weight": 0.6,
    },
]

# Торгові параметри
SLIPPAGE = Decimal("0.010")             # 1.0%
SELL_SLIPPAGE_STEPS = [0.010, 0.011, 0.012, 0.013, 0.014, 0.015]
BUY_SELL_TOTAL_USDT = Decimal("140")     # ~140 USDT за цикл (BUY + SELL)
BUY_PART_USDT = (BUY_SELL_TOTAL_USDT / 2).quantize(Decimal("0.000001"))
MIN_BNB_FOR_GAS = Decimal("0.0005")     # мін. BNB на комісії
NUM_CYCLES = 8                          # к-сть прокрутів
TX_DELAY_SEC = 0.8                      # пауза між транзакціями
APPROVE_DELAY_SEC = 0.8                 # пауза після approve
CYCLE_DELAY_SEC = 1.5                   # пауза між циклами
DELAY_MIN_SEC = 4                       # мінімальна пауза перед BUY/SELL
DELAY_MAX_SEC = 8                      # максимальна пауза перед BUY/SELL

# --- Allowance reset policy ---
RESET_APPROVE_ON_FAIL = True          # вмикає стратегію «скинути апрув та переапрувити» на фейлах
REAPPROVE_EVERY_N_FAILURES = 2        # робити переапрув кожні N фейлів SELL
APPROVE_RESET_ERRORS = [
    "insufficient allowance",
    "transfer amount exceeds allowance",
    "spender",                        # на випадок зміни spender/permit2
    "ERC20: insufficient allowance",
    "ERC20: transfer amount exceeds allowance",
]

USE_SELL_CHUNKING = True
CHUNK_AFTER_FAILS = 2                 # після скількох фейлів переходимо на порційний продаж
SELL_CHUNKS = [Decimal("0.60"), Decimal("0.30"), Decimal("0.10")] 
SWAP_SEND_MAX_ATTEMPTS = 3
CHUNK_MAX_ATTEMPTS = 2  # було 3 — зменшили, щоб швидше виходити
SECONDARY_EARLY_EXIT_SOLD = 2
CHUNK_PRIMARY_RATIOS = [Decimal("0.60"), Decimal("0.30"), Decimal("0.10")]
CHUNK_SECONDARY_RATIOS = [Decimal("0.50"), Decimal("0.30"), Decimal("0.20")]

# Можеш зробити токен-специфічно, напр. для MTP/AICELL частіше ділити:
TOKEN_CHUNKING: Dict[str, List[Decimal]] = {
    "MTP": [Decimal("0.5"), Decimal("0.3"), Decimal("0.1")],
    "AICELL": [Decimal("0.5"), Decimal("0.25"), Decimal("0.1")],
    "KOGE": [Decimal("0.5"), Decimal("0.25"), Decimal("0.1")]
}

POINTS_PER_100_USDT = Decimal("1")   # скільки поінтів за кожні 100 USDT обороту
BOOST_MULTIPLIER = Decimal("1.00")   # множник буста (1.00 = без буста)

TRADE_LOG_PATH = Path("data/trades.csv")  # або інший шлях, якщо зручно

# OKX Web3 API
OKX_HOST = "https://web3.okx.com"
ENDPOINT_QUOTE = "/api/v5/dex/aggregator/quote"
ENDPOINT_SWAP = "/api/v5/dex/aggregator/swap"
ENDPOINT_APPROVE = "/api/v5/dex/aggregator/approve-transaction"
ENDPOINT_BAL_SPECIFIC = "/api/v5/wallet/asset/token-balances-by-address"
