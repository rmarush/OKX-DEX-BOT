from decimal import Decimal
from typing import Dict, List
from .config import CHAIN_INDEX, USDT_BSC, ENDPOINT_BAL_SPECIFIC

def fetch_balances(okx, address: str, token_list: List[dict]) -> Dict[str, Decimal]:
    # готуємо список адрес для запиту (BNB "", USDT, і всі токени)
    token_addresses = [
        {"chainIndex": CHAIN_INDEX, "tokenAddress": ""},                 # native BNB
        {"chainIndex": CHAIN_INDEX, "tokenAddress": USDT_BSC},           # USDT
    ]
    for t in token_list:
        token_addresses.append({"chainIndex": CHAIN_INDEX, "tokenAddress": t["address"]})

    payload = {"address": address, "tokenAddresses": token_addresses}
    resp = okx.post(ENDPOINT_BAL_SPECIFIC, payload)
    data = resp.get("data", [{}])[0].get("tokenAssets", [])

    out: Dict[str, Decimal] = {"BNB": Decimal(0), "USDT": Decimal(0)}
    # ініціалізуємо всі токени нулями
    for t in token_list:
        out[t["symbol"]] = Decimal(0)

    for t in data:
        token_addr = (t.get("tokenAddress") or "").lower()
        bal = Decimal(str(t.get("balance", "0")))
        if token_addr == "" or t.get("tokenAddress") == "":
            out["BNB"] = bal
        elif token_addr == str(USDT_BSC).lower():
            out["USDT"] = bal
        else:
            # знайдемо символ за адресою
            for x in token_list:
                if token_addr == x["address"].lower():
                    out[x["symbol"]] = bal
                    break
    return out
