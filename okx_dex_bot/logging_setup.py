import logging

USE_RICH = False  # оновиться автоматично, якщо rich доступний

def setup_logger() -> logging.Logger:
    global USE_RICH
    logger = logging.getLogger("okx_dex_bot")
    logger.setLevel(logging.INFO)
    if logger.handlers:
        return logger

    fmt = "%(asctime)s | %(levelname)-8s | %(message)s"
    datefmt = "%H:%M:%S"
    try:
        from rich.logging import RichHandler  # type: ignore
        rh = RichHandler(
            rich_tracebacks=False,
            show_time=False,   # час дасть Formatter
            show_level=False,  # рівень дасть Formatter
            show_path=False,
            markup=True,
        )
        rh.setLevel(logging.INFO)
        rh.setFormatter(logging.Formatter(fmt=fmt, datefmt=datefmt))
        logger.addHandler(rh)
        USE_RICH = True
    except Exception:
        ch = logging.StreamHandler()
        ch.setLevel(logging.INFO)
        ch.setFormatter(logging.Formatter(fmt=fmt, datefmt=datefmt))
        logger.addHandler(ch)
        USE_RICH = False
    return logger


def wallet_tag(idx: int) -> str:
    if USE_RICH:
        return f"[bold cyan][Wallet{idx}][/bold cyan] "
    return f"[Wallet{idx}] "
