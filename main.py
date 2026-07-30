#!/usr/bin/env python3
"""CLI entry point — dispatches live trading or backtesting mode."""

import argparse
import os
import signal
import sys
from datetime import datetime, timezone

from apscheduler.schedulers.background import BackgroundScheduler

from config import CONFIG
from database import Database
from executor import Executor
from screeners import scan_pead_candidates, scan_microcap_filings, scan_cef_discounts
from historical_backtester import HistoricalBacktester
from logging_utils import setup_logging, session_startup_log


def _run_dir(mode: str) -> str:
    now = datetime.now(timezone.utc)
    p = os.path.join(CONFIG.run_logs_dir, now.strftime("%Y-%m-%d"), f"run_{mode}_{now.strftime('%H%M%S')}")
    os.makedirs(p, exist_ok=True)
    return p


def mode_backtest(args):
    start = args.start or "2021-01-01"
    end = args.end or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    run_dir = _run_dir("backtest")

    db = Database(CONFIG.db_path)
    db.init_db()
    bt = HistoricalBacktester(db, run_dir)
    logger = setup_logging("main_backtest", run_dir)
    session_startup_log(logger, CONFIG.exchange_timezone)
    logger.info("Backtest: %s to %s", start, end)

    strategies = args.strategies or ["pead", "microcap", "cef"]
    all_results = []

    try:
        for strat in strategies:
            logger.info("=== RUNNING %s ===", strat.upper())
            if strat == "pead":
                r = bt.backtest_pead(start, end)
            elif strat == "microcap":
                r = bt.backtest_microcaps(start, end)
            else:
                r = bt.backtest_cefs(start, end)
            all_results.extend(r)
            metrics = bt.generate_performance_metrics(strat)
            if metrics:
                _print_metrics(strat, metrics)

        if all_results:
            total_pnl = sum(r.get("pnl_usd", 0) for r in all_results)
            wins = sum(1 for r in all_results if r["pnl_pct"] > 0)
            logger.info("Combined: %d trades | $%.2f PnL | %.1f%% wins",
                        len(all_results), total_pnl, (wins / len(all_results) * 100))
    finally:
        db.close()


def mode_live(args):
    run_dir = _run_dir("live")
    db = Database(CONFIG.db_path)
    db.init_db()
    executor = Executor(db, run_dir)
    logger = setup_logging("main_live", run_dir)
    session_startup_log(logger, CONFIG.exchange_timezone)
    logger.info("LIVE MODE — Alpaca paper trading")

    scheduler = BackgroundScheduler()

    scheduler.add_job(lambda: _run_screener("pead", scan_pead_candidates, db, run_dir, executor, logger),
                      trigger="cron", day_of_week="mon-fri", hour=22, minute=0,
                      timezone=CONFIG.exchange_timezone, id="pead")
    scheduler.add_job(lambda: _run_screener("microcap", scan_microcap_filings, db, run_dir, executor, logger),
                      trigger="interval", minutes=CONFIG.microcap.poll_interval_minutes, id="microcap")
    scheduler.add_job(lambda: _run_screener("cef", scan_cef_discounts, db, run_dir, executor, logger),
                      trigger="cron", day_of_week="mon-fri", hour=8, minute=30,
                      timezone=CONFIG.exchange_timezone, id="cef")
    scheduler.add_job(executor.cancel_unfilled_orders, trigger="interval", minutes=15, id="cleanup")
    scheduler.add_job(executor.check_closed_positions, trigger="interval", minutes=30, id="check_closed")

    scheduler.start()
    logger.info("Scheduler started: %s", [j.id for j in scheduler.get_jobs()])

    shutdown = False
    def _stop(signum, frame):
        nonlocal shutdown
        logger.info("Shutdown signal — stopping...")
        scheduler.shutdown(wait=False)
        shutdown = True

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    try:
        while not shutdown:
            signal.pause()
    except AttributeError:
        import time
        while not shutdown:
            time.sleep(1)

    logger.info("Shutdown complete")
    try:
        db.close()
    except Exception:
        pass


def _run_screener(name, scan_func, db, run_dir, executor, logger):
    logger.info("=== %s cycle ===", name.upper())
    try:
        for sig in scan_func(db, run_dir):
            if sig["decision"] == "buy" and not sig.get("executed"):
                slip = CONFIG.pead.slippage_pct if name == "pead" else \
                       CONFIG.microcap.slippage_pct if name == "microcap" else \
                       CONFIG.cef.slippage_pct
                price = executor._get_current_price(sig["symbol"])
                if price and price > 0:
                    qty = max(1, int(CONFIG.risk.max_position_size_usd * 0.2 / price))
                else:
                    qty = 1
                executor.place_limit_order(symbol=sig["symbol"], qty=qty, side="buy",
                                           strategy_name=name, slippage_pct=slip)
    except Exception as e:
        logger.error("%s screener failed: %s", name, e, exc_info=True)


def _print_metrics(strategy, metrics):
    try:
        from tabulate import tabulate
        print(f"\n{'='*50}\n  {strategy.upper()} METRICS\n{'='*50}")
        print(tabulate([[k, v] for k, v in metrics.items()], headers=["Metric", "Value"], tablefmt="simple"))
    except ImportError:
        print(f"\n--- {strategy.upper()} METRICS ---")
        for k, v in metrics.items():
            print(f"  {k}: {v}")
        print()


def main():
    parser = argparse.ArgumentParser(description="Alpaca Multi-Strategy Trading System")
    parser.add_argument("--mode", required=True, choices=["live", "backtest"],
                        help="live=paper trading | backtest=historical simulation")
    parser.add_argument("--start", help="Backtest start date (YYYY-MM-DD)")
    parser.add_argument("--end", help="Backtest end date (YYYY-MM-DD)")
    parser.add_argument("--strategies", nargs="*", choices=["pead", "microcap", "cef"],
                        help="Strategies to backtest (default: all)")
    args = parser.parse_args()

    try:
        CONFIG.validate()
    except ValueError as e:
        print(f"Config error: {e}")
        sys.exit(1)

    if args.mode == "backtest":
        mode_backtest(args)
    else:
        mode_live(args)


if __name__ == "__main__":
    main()
