from decimal import Decimal, ROUND_DOWN
from pathlib import Path
from typing import List, Optional

def load_lines(path: Path) -> List[str]:
    return [ln.strip() for ln in path.read_text().splitlines()
            if ln.strip() and not ln.strip().startswith("#")]

def to_base_units(amount: Decimal, decimals: int) -> str:
    q = Decimal(10) ** decimals
    return str(int((amount * q).to_integral_value(rounding=ROUND_DOWN)))

def raw_tx_bytes(signed) -> bytes:
    return getattr(signed, "rawTransaction", None) or getattr(signed, "raw_transaction", None)

def parse_int_auto(x):
    if x is None:
        return None
    if isinstance(x, int):
        return x
    if isinstance(x, str):
        s = x.strip().lower()
        try:
            if s.startswith("0x"):
                return int(s, 16)
            return int(s)
        except Exception:
            return None
    try:
        return int(x)
    except Exception:
        return None

def short_addr(addr: str) -> str:
    return addr[:6] + "…" + addr[-4:]
