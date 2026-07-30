You are an expert quantitative developer. Your task is to build a modular, production-ready Python application that executes live paper trading on Alpaca (Paper API), conducts post-trade event tracking, AND includes a full Historical Event Backtesting Engine to test all three strategies on past data (e.g., last 2 to 5 years).

---

### APP ARCHITECTURE & TECH STACK
- Python 3.11+
- Execution Broker: `alpaca-py` (TradingClient with `paper=True`)
- Free Data Stack: `yfinance` for market prices/history, `edgartools` / SEC EDGAR API for 8-K filings, and custom scrapers for historical/live earnings & CEF NAV data.
- Local Persistence: SQLite database (`trading_system.db`)
- Scheduling & CLI: `apscheduler` for live trading, `argparse` for CLI commands (`--mode live` vs `--mode backtest`).

---

### STRATEGY DEFINITIONS & EVENT LOGIC

1. **PEAD Momentum (Post-Earnings Announcement Drift)**
   - **Event Trigger:** Company reports earnings beating consensus EPS by > 15% with positive forward guidance.
   - **Live Logic:** Scan daily post-market earnings. Enter at next market open with a +0.5% limit price buffer.
   - **Historical Backtest Logic:** Mine historical quarterly earnings release dates (via `yfinance` or FMP/Alpha Vantage free tiers). Identify all historical SUE (Standardized Unexpected Earnings) beats > 15% over the past 3 years. Simulate buying at the open price on Day +1 post-earnings and holding for 30 trading days with a 7% trailing stop-loss.

2. **Micro-Cap SEC Regulatory Catalyst Scanner**
   - **Event Trigger:** 8-K filings containing regulatory/reimbursement keywords for companies with Market Cap between $20M and $300M.
   - **Live Logic:** Poll SEC EDGAR RSS feed every 15 mins during market hours.
   - **Historical Backtest Logic:** Parse historical SEC EDGAR index files over a multi-year window. Search historical 8-K filings for keywords ("Medicare reimbursement", "CMS approval", "FDA clearance", "patent granted") within micro-cap CIKs. Map filing timestamps to historical minute/daily price data to measure post-filing price reaction, simulate entry 15 minutes after filing time with 1.0% illiquidity slippage, and evaluate 15-day forward return.

3. **Closed-End Fund (CEF) Discount Arbitrage**
   - **Event Trigger:** CEF trading at a discount > -12% to NAV alongside activist hedge fund accumulation (Schedule 13D/13G filings).
   - **Live Logic:** Daily pre-market scan of NAV vs. Market Price and SEC 13D feeds.
   - **Historical Backtest Logic:** Pull historical CEF market prices and NAV time series. Identify historical points where discount widened past -12% (and cross-reference with historical SEC 13D filing dates for activist entry). Calculate historical mean-reversion rates, average time to close discount to -4%, and dividend yield capture during holding periods up to 60 days.

---

### REQUIRED MODULES & FILE STRUCTURE

Generate clean, fully implemented, well-commented code across the following files:

1. `config.py`: Configuration settings, environment variables (`ALPACA_API_KEY`, `ALPACA_SECRET_KEY`), risk limits (max position size $, daily max loss % kill-switch), backtest date ranges (`START_DATE`, `END_DATE`), and strategy parameters.
2. `rate_limiter.py`: A Python decorator and API wrapper enforcing a max of 3 requests per second (~180/min) to prevent rate limits (`HTTP 429`), with automatic exponential backoff.
3. `database.py`: SQLite wrapper with tables for:
   - `trades` (id, symbol, strategy_name, entry_time, entry_price, qty, target_price, stop_loss, status)
   - `signals` (id, symbol, strategy_name, raw_data, timestamp, executed)
   - `backtest_results` (id, strategy_name, event_date, symbol, entry_price, exit_price, pnl_pct, hold_days, spy_return, alpha)
4. `screeners.py`: Real-time scanners for live paper execution:
   - `scan_pead_candidates()`
   - `scan_microcap_filings()`
   - `scan_cef_discounts()`
5. `executor.py`: Alpaca live paper execution module:
   - Checks available buying power.
   - Places strict **Limit Orders** (never market orders) with customizable price buffers.
   - Enforces a minimum 24-hour hold time to prevent Pattern Day Trader (PDT) triggers on accounts under $25,000.
   - Cancels unfilled limit orders after 15 minutes.
6. `historical_backtester.py`: **Dedicated Historical Backtesting Engine**:
   - `backtest_pead(start_date, end_date)`: Fetches past earnings events, simulates 30-day drift trades, logs P&L and drawdown.
   - `backtest_microcaps(start_date, end_date)`: Searches historical SEC 8-K archives for keywords, matches with historical stock prices, applies realistic 1% slippage penalty, logs trades.
   - `backtest_cefs(start_date, end_date)`: Analyzes historical NAV vs Price data, simulates buying at wide discounts, and tracks discount convergence over 60-day windows.
   - `generate_performance_metrics()`: Calculates total return, CAGR, Sharpe Ratio, Max Drawdown, Win Rate, and Alpha relative to `SPY` and `IWM` benchmarks across all historical events.
7. `main.py`: Central CLI and event loop:
   - Accepts `--mode backtest` (runs `historical_backtester.py` across specified date range and outputs a terminal performance summary table).
   - Accepts `--mode live` (runs scheduled real-time screeners, live Alpaca paper executor, and daily database logging).

---

### CONSTRAINTS & CODING RULES
- Do NOT use placeholders, `TODO` comments, or pseudocode. Write full working functions.
- Ensure the historical backtesting engine explicitly includes realistic slippage (0.1% for PEAD/large caps, 1.0% for micro-caps) and transaction costs.
- Handle API exceptions gracefully (`yfinance` connection drops, SEC filing parser errors, missing historical price points) without crashing.
- Use structured `logging` instead of `print()` statements throughout the codebase.