# New Strategy — Alpaca Multi-Strategy Trading System

## Project Overview
Three event-driven trading strategies on Alpaca paper API:
1. PEAD Momentum — Post-earnings drift on >15% EPS beats
2. Micro-cap SEC Regulatory Catalyst — 8-K filings with key words, $20M-$300M cap
3. CEF Discount Arbitrage — Closed-end funds at >12% NAV discount with activist filings

## Python Environment
```
C:\Users\User\Documents\code\auto_trader\myenv\Scripts\python.exe
```

## Usage
```bash
# Backtest mode
python main.py --mode backtest --start 2021-01-01 --end 2026-07-01

# Live mode (paper trading)
python main.py --mode live
```

## Key Files
| File | Purpose |
|------|---------|
| `config.py` | All configuration, env loading, strategy params |
| `logging_utils.py` | Timezone-aware structured logging to run_logs/ |
| `rate_limiter.py` | API rate limiting decorator |
| `database.py` | SQLite wrapper for trades, signals, backtest_results |
| `screeners.py` | Live scanners for all 3 strategies |
| `executor.py` | Alpaca paper order execution |
| `historical_backtester.py` | Backtesting engine + post-trade comparison |
| `main.py` | CLI entry point |

## Conventions
- UTC for generic log messages
- Exchange timezone (America/New_York) annotated for price feeds and events
- Limit orders only
- Structured logging, never print()
