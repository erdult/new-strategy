"""Timezone-aware structured logging to run_logs/."""

import logging
import os
import time
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo


def setup_logging(name: str, run_dir: str, level: int = logging.DEBUG) -> logging.Logger:
    os.makedirs(run_dir, exist_ok=True)
    logger = logging.getLogger(name)
    logger.setLevel(level)

    if logger.handlers:
        return logger

    file_path = os.path.join(run_dir, f"{name}.log")
    fh = logging.FileHandler(file_path, encoding="utf-8")
    fh.setLevel(level)

    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)

    fmt = logging.Formatter(
        "%(asctime)s.%(msecs)03d UTC [%(levelname)s] %(name)s.%(funcName)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    fmt.converter = time.gmtime
    fh.setFormatter(fmt)
    ch.setFormatter(fmt)

    logger.addHandler(fh)
    logger.addHandler(ch)
    return logger


def format_with_tz(dt: datetime, exchange_tz: str = "America/New_York") -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    utc_str = dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    local_str = dt.astimezone(ZoneInfo(exchange_tz)).strftime("%Y-%m-%d %H:%M:%S %Z")
    return f"{utc_str} ({local_str})"


def session_startup_log(logger: logging.Logger, exchange_tz: str):
    now = datetime.now(timezone.utc)
    local = now.astimezone(ZoneInfo(exchange_tz))
    dst_status = local.dst() and local.dst() != timedelta(0) if hasattr(local, 'dst') else "N/A"
    logger.info("=" * 60)
    logger.info("SESSION START — UTC: %s | Exchange: %s | DST: %s",
                now.strftime("%Y-%m-%d %H:%M:%S"),
                local.strftime("%Y-%m-%d %H:%M:%S %Z"),
                dst_status)
    logger.info("Exchange timezone: %s", exchange_tz)
    logger.info("=" * 60)
