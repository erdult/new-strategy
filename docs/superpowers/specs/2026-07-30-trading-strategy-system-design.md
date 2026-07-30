# Alpaca Multi-Strategy Trading System

## Overview
A modular Python application executing live paper trading on Alpaca with three event-driven strategies and a full historical backtesting engine. After live trades close, automated backtesting runs to compare actual vs expected strategy performance.

## Tech Stack
- Python 3.11+ (env: `auto_trader/myenv`)
- `alpaca-py` (TradingClient, paper=True)
- `yfinance` for market/historical prices
- `edgartools` for SEC EDGAR filings (live RSS + historical archives)
- SQLite via `sqlite3` standard library
- `apscheduler` for live scheduling
- `argparse` for CLI
- `python-dotenv` for API key management

## File Structure
```
new_strategy/
├── .env                   # API keys (gitignored)
├── .gitignore
├── CLAUDE.md
├── config.py              # All config, env loading, strategy params
├── rate_limiter.py        # Sliding-window rate limiter decorator
├── database.py            # SQLite wrapper (trades, signals, backtest_results)
├── screeners.py           # Live scanners: PEAD, micro-cap, CEF
├── executor.py            # Alpaca paper order execution + PDT cooldown
├── historical_backtester.py  # 3 strategy backtests + performance metrics
├── main.py                # CLI dispatcher: --mode live|backtest
├── docs/superpowers/specs/
└── trading_system.db      # SQLite database (gitignored)
```

## Component Design

### 1. config.py
- Loads `.env` via `python-dotenv`
- Strategy parameters as dataclasses or dicts:
  - PEAD: SUE threshold (15%), hold days (30), trailing stop (7%), slippage (0.1%)
  - Micro-cap: market cap range ($20M-$300M), keywords list, slippage (1.0%)
  - CEF: discount threshold (-12%), max hold days (60), convergence target (-4%)
- Risk limits: max position size ($), daily max loss % (kill-switch)
- Backtest date range defaults
- Database path setting

### 2. rate_limiter.py
- Sliding-window rate limiter: max 3 requests/second for Alpaca API
- Decorator form (`@rate_limit`) for sync functions
- Also wraps the Alpaca `httpx` client transport to enforce at the HTTP layer
- Exponential backoff on 429 responses

### 3. database.py
- `init_db()` — creates tables if not exist
- Tables:
  - `trades` (id, symbol, strategy_name, entry_time, entry_price, qty, target_price, stop_loss, exit_time, exit_price, pnl_pct, status)
  - `signals` (id, symbol, strategy_name, raw_data, timestamp, executed)
  - `backtest_results` (id, strategy_name, event_date, symbol, entry_price, exit_price, pnl_pct, hold_days, spy_return, alpha)
- CRUD helpers: `save_trade()`, `update_trade()`, `save_signal()`, `save_backtest_result()`, `get_trades_by_strategy()`

### 4. screeners.py
- **PEAD**: Runs daily post-market. Uses `yfinance` to get earnings calendar, filters SUE > 15%, checks forward guidance in earnings text.
- **Micro-cap**: Polls SEC EDGAR RSS every 15 min during market hours via `edgartools`. Parses 8-K filings for keyword matches. Filters by market cap range via Alpaca API.
- **CEF**: Pre-market daily scan. Uses Alpaca assets list filtered for CEFs, compares market price to NAV (pulled from stored lookup).

### 5. executor.py
- Integrates with Alpaca `TradingClient(paper=True)`
- `place_limit_order(symbol, side, qty, price_buffer=0.005)` — buys with +0.5% limit buffer
- `check_buying_power()` before each order
- PDT cooldown tracker: per-symbol dict of last exit time, enforces 24h min hold
- `cancel_unfilled_orders()` — runs every 15 min, cancels orders placed >15 min ago
- **Post-trade hook**: On position close, calls `historical_backtester.run_comparison(symbol, strategy_name, actual_trade)` for automated backtesting comparison

### 6. historical_backtester.py
- `backtest_pead(start, end)`:
  - Mine historical earnings dates via `yfinance`
  - Filter SUE > 15%
  - Simulate buy at Day+1 open, hold 30 days, 7% trailing stop
  - Slippage: 0.1%
- `backtest_microcaps(start, end)`:
  - Parse SEC EDGAR index files for 8-K filings
  - Match keywords in filing text
  - Filter CIKs by market cap ($20M-$300M) via historical data
  - Simulate entry 15 min after filing, 1.0% slippage
  - Evaluate 15-day forward return
- `backtest_cefs(start, end)`:
  - Pull CEF list from Alpaca/yfinance
  - Find points where discount < -12%
  - Cross-reference with 13D filing dates
  - Simulate entry, track time to converge to -4% discount, 60-day max hold
- `generate_performance_metrics()`:
  - Total return, CAGR, Sharpe Ratio, Max DD, Win Rate
  - Alpha vs SPY and IWM
- `run_comparison(symbol, strategy_name, actual_trade)`:
  - Called post-trade by executor
  - Runs focused backtest for the specific symbol/event period
  - Logs actual vs expected P&L for drift monitoring

### 7. main.py
- `--mode backtest`:
  - `--start` / `--end` date range
  - Runs requested strategies (default: all 3)
  - Outputs terminal summary table via `tabulate`
  - Saves results to DB
- `--mode live`:
  - Sets up APScheduler with strategy-specific intervals
  - Screeners → Executor pipeline
  - Post-trade backtesting hook active
  - Graceful shutdown handling

## Data Flow

### Live Mode
```
APScheduler → scan_pead_candidates() → signal → executor.place_limit_order()
            → scan_microcap_filings() → signal → executor.place_limit_order()
            → scan_cef_discounts()    → signal → executor.place_limit_order()
                                                    ↓
                                          database.save_trade()
                                          track position until exit (TP/SL/time)
                                                    ↓
                                          on close → auto_backtest(symbol, strategy)
                                                    ↓
                                          database.save_backtest_result()
```

### Backtest Mode
```
CLI args → historical_backtester.backtest_*()
              → load historical data
              → identify events
              → simulate trades with slippage
              → store results in database
              → generate_performance_metrics()
              → print summary table
```

## Risk Controls
- Daily max loss % kill-switch (configurable, halts new orders if breached)
- Max position size cap
- PDT cooldown (24h minimum hold per symbol)
- Limit orders only (no market orders)
- Unfilled order auto-cancel after 15 min

## Logging System
- All logs written to `run_logs/` directory, organized by date: `run_logs/YYYY-MM-DD/`
- Each run session gets a subdirectory: `run_logs/YYYY-MM-DD/run_{mode}_{strategy}_{timestamp}/`
- Structured `logging` with rotating file handlers, NOT print()

### Log Format
- Generic messages (system events, config loading, scheduling): ISO 8601 UTC
- Price feed messages: include both UTC and exchange local time — `2026-07-30 14:30:00 UTC (2026-07-30 10:30:00 America/New_York)`
- Event timestamps (earnings, filings, NAV prints): show original timezone with UTC offset
- Trade entry/exit logs: entry_time_utc, entry_time_exchange, slippage_applied, exchange_timezone
- All log lines include: `[UTC] [LEVEL] [module.function] message`

### What Gets Logged
- **Screener runs**: each scan cycle, candidates found, rejection reasons
- **Signal generation**: raw signal data, computed values, threshold comparisons, decision
- **Order placement**: buying_power, symbol, side, qty, limit_price, price_buffer, timestamp_utc, exchange_time
- **Order lifecycle**: submitted, filled, partial_fill, cancelled, unfilled_reason, timestamps in UTC + exchange tz
- **Trade close**: exit_time_utc, exit_time_exchange, hold_duration, pnl_pct, reason (TP/SL/time)
- **Post-trade backtest comparison**: live_trade result vs backtest expected result, discrepancy_pct
- **Backtest engine**: events found, events filtered, events skipped (with reason), each simulated trade
- **API rate limit events**: 429 responses, backoff applied, retry count
- **Kill-switch activations**: threshold breached, current_daily_loss, max_allowed_loss

### Timezone Handling
- System clock reference: `datetime.timezone.utc`
- Exchange timezone map: `{'alpaca': 'America/New_York', 'default': 'UTC'}`
- Market hours check uses exchange timezone; all comparisons use UTC
- Log headers at session start: system UTC offset, detected exchange timezone, DST status

## Error Handling
- All API calls wrapped in try/except with structured logging
- `yfinance` connection drops → log warning + connection details, retry with backoff
- SEC EDGAR parser errors → log error + filing URL, skip filing, continue
- Missing price data for historical events → log symbol + date range, skip event, continue
- Alpaca API errors → log full response body, exponential backoff, circuit-break on repeated failures
