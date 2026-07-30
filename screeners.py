"""Live screeners — PEAD, micro-cap SEC filings, and CEF discount arbitrage."""

from datetime import datetime, timezone
from typing import List, Dict, Any
from zoneinfo import ZoneInfo

import yfinance as yf
from edgar import Filing, set_identity

from config import CONFIG
from database import Database
from logging_utils import setup_logging, format_with_tz

set_identity("NewStrategy Research/1.0")


def _now_with_tz(exchange_tz: str = "America/New_York"):
    utc = datetime.now(timezone.utc)
    return utc, utc.astimezone(ZoneInfo(exchange_tz))


def _is_market_hours(exchange_tz: str = "America/New_York") -> bool:
    _, local = _now_with_tz(exchange_tz)
    if local.weekday() >= 5:
        return False
    m = local.hour * 60 + local.minute
    return 570 <= m <= 960


def scan_pead_candidates(db: Database, run_dir: str) -> List[Dict[str, Any]]:
    logger = setup_logging("screener_pead", run_dir)
    utc_now = datetime.now(timezone.utc)
    signals = []
    logger.info("PEAD scan starting %s", format_with_tz(utc_now))

    try:
        earnings = yf.EarningsCalendar(from_date=utc_now).data
        if earnings is None or earnings.empty:
            logger.info("No earnings data available")
            return signals
    except Exception as e:
        logger.error("Failed to fetch earnings calendar: %s", e)
        return signals

    for _, row in earnings.iterrows():
        try:
            symbol = row.get("symbol", "")
            if not symbol:
                continue
            eps_est = row.get("epsestimate") or row.get("eps_estimate", 0)
            eps_act = row.get("epsactual") or row.get("eps_actual", 0)
            if not (eps_est and eps_act and eps_est > 0):
                continue
            surprise = (eps_act - eps_est) / abs(eps_est)
            decision = "buy" if surprise > CONFIG.pead.sue_threshold else "skip"
            signal = {
                "symbol": symbol, "strategy_name": "pead",
                "raw_data": str(row.to_dict()),
                "signal_value": float(surprise),
                "threshold": CONFIG.pead.sue_threshold,
                "decision": decision,
                "reason": f"SUE: {surprise*100:.1f}% {'>' if decision == 'buy' else '<='} threshold",
                "timestamp_utc": utc_now.isoformat(),
                "exchange_time": utc_now.astimezone(ZoneInfo(CONFIG.exchange_timezone)).isoformat(),
                "executed": 0,
            }
            db.save_signal(signal)
            signals.append(signal)
            logger.info("PEAD %s SUE %+.1f%% -> %s", symbol, surprise * 100, decision)
        except Exception as e:
            logger.warning("PEAD error %s: %s", row.get("symbol", "?"), e)

    logger.info("PEAD scan: %d signals", len(signals))
    return signals


def scan_microcap_filings(db: Database, run_dir: str) -> List[Dict[str, Any]]:
    logger = setup_logging("screener_microcap", run_dir)
    utc_now, local_now = _now_with_tz()
    signals = []

    if not _is_market_hours():
        logger.debug("Outside market hours -- skip")
        return signals

    logger.info("Micro-cap scan %s", format_with_tz(utc_now))

    try:
        filings = Filing.get_filings(form="8-K", limit=50)
    except Exception as e:
        logger.error("SEC EDGAR fetch failed: %s", e)
        return signals

    for filing in filings:
        try:
            symbol = str(filing.ticker) if hasattr(filing, "ticker") and filing.ticker else str(filing.cik)
            try:
                text = filing.text().lower() if hasattr(filing, "text") else ""
            except Exception:
                text = ""
            matched = [kw for kw in CONFIG.microcap.keywords if kw.lower() in text]
            if not matched:
                continue

            try:
                info = yf.Ticker(symbol).info or {}
                mcap = info.get("marketCap", 0)
            except Exception:
                mcap = 0

            if not (CONFIG.microcap.min_market_cap <= mcap <= CONFIG.microcap.max_market_cap):
                logger.debug("%s mcap $%.0f out of range", symbol, mcap)
                continue

            signal = {
                "symbol": symbol, "strategy_name": "microcap",
                "raw_data": f"filing_date={filing.filing_date},keywords={matched}",
                "signal_value": 1.0, "threshold": 0.0, "decision": "buy",
                "reason": f"8-K catalyst: {', '.join(matched)}",
                "timestamp_utc": utc_now.isoformat(),
                "exchange_time": str(filing.filing_date), "executed": 0,
            }
            db.save_signal(signal)
            signals.append(signal)
            logger.info("MICRO-CAP %s: %s (mcap $%.0f)", symbol, matched, mcap)
        except Exception as e:
            logger.warning("Micro-cap error: %s", e)

    logger.info("Micro-cap scan: %d signals", len(signals))
    return signals


def scan_cef_discounts(db: Database, run_dir: str) -> List[Dict[str, Any]]:
    logger = setup_logging("screener_cef", run_dir)
    utc_now = datetime.now(timezone.utc)
    signals = []

    if not _is_market_hours():
        logger.debug("Outside market hours -- skip")
        return signals

    logger.info("CEF scan %s", format_with_tz(utc_now))

    cef_tickers = ["GOVT", "TLT", "HYG", "CWB", "PCY", "EMB", "BKLN", "FLOT", "JPST", "NEAR", "PULS", "GSY"]

    for symbol in cef_tickers:
        try:
            info = yf.Ticker(symbol).info or {}
            price = info.get("currentPrice") or info.get("regularMarketPrice") or info.get("previousClose")
            nav = info.get("navPrice") or info.get("yield_")
            if not price or not nav or nav <= 0:
                continue
            discount = (price - nav) / nav
            decision = "buy" if discount < CONFIG.cef.discount_threshold else "skip"
            signal = {
                "symbol": symbol, "strategy_name": "cef",
                "raw_data": f"price={price},nav={nav},discount={discount:.4f}",
                "signal_value": float(discount), "threshold": CONFIG.cef.discount_threshold,
                "decision": decision,
                "reason": f"discount={discount*100:.1f}% (threshold={CONFIG.cef.discount_threshold*100:.1f}%)",
                "timestamp_utc": utc_now.isoformat(),
                "exchange_time": utc_now.astimezone(ZoneInfo(CONFIG.exchange_timezone)).isoformat(),
                "executed": 0,
            }
            db.save_signal(signal)
            signals.append(signal)
            logger.info("CEF %s discount=%.1f%% -> %s", symbol, discount * 100, decision)
        except Exception as e:
            logger.warning("CEF error %s: %s", symbol, e)

    logger.info("CEF scan: %d signals", len(signals))
    return signals
