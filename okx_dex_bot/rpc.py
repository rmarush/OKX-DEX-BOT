from typing import List, Optional, Tuple
from web3 import Web3, HTTPProvider
from .logging_setup import setup_logger

log = setup_logger()

try:
    from web3 import WebsocketProvider  # type: ignore
except Exception:
    WebsocketProvider = None


class RpcRotator:
    """Ротація RPC (HTTP/WS) з перевіркою підключення. WS пропускаємо, якщо ввімкнено proxy."""

    def __init__(self, urls: List[str], proxy: Optional[str] = None):
        norm = []
        for u in urls:
            u = (u or "").strip()
            if not u:
                continue
            if not (u.startswith("http://") or u.startswith("https://") or u.startswith("ws://") or u.startswith("wss://")):
                u = "https://" + u
            if u not in norm:
                norm.append(u)
        if not norm:
            raise ValueError("No RPC URLs provided")
        self.urls = norm
        self.idx = 0
        self.proxy = proxy

    def _make_web3(self, url: str) -> Optional[Web3]:
        if url.startswith(("ws://", "wss://")):
            if self.proxy:
                log.warning(f"Skip WS RPC with proxy: {url}")
                return None
            if WebsocketProvider is None:
                log.warning(f"Skip WS RPC (WebsocketProvider not available): {url}")
                return None
            provider = WebsocketProvider(url, websocket_timeout=30)
            return Web3(provider)
        if self.proxy:
            provider = HTTPProvider(url, request_kwargs={"proxies": {"http": self.proxy, "https": self.proxy}})
        else:
            provider = HTTPProvider(url)
        return Web3(provider)

    def connect(self, tries: Optional[int] = None) -> Tuple[Web3, str]:
        tries = tries or len(self.urls)
        attempts = 0
        while attempts < tries:
            url = self.urls[self.idx % len(self.urls)]
            w3 = self._make_web3(url)
            self.idx += 1
            attempts += 1
            if w3 is None:
                continue
            try:
                if w3.is_connected():
                    _ = w3.eth.block_number
                    if attempts > 1:
                        log.info(f"Switched RPC → {url}")
                    return w3, url
            except Exception:
                pass
        raise RuntimeError("All RPC endpoints failed to respond")

    def rotate_and_connect(self) -> Tuple[Web3, str]:
        return self.connect(tries=len(self.urls))
