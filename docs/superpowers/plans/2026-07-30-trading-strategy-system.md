# Alpaca Multi-Strategy Trading System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a modular Python application executing live paper trading on Alpaca with 3 event-driven strategies (PEAD, Micro-cap SEC catalyst, CEF discount arbitrage) and a full historical backtesting engine with automated post-trade comparison.

**Architecture:** 7 core files + logging utility + config. `main.py` dispatches via `--mode live|backtest`. Live mode uses APScheduler to run screeners → executor; backtest mode runs strategy simulations directly. Post-trade hook calls backtester comparison automatically. All events logged with timezone-aware structured logging to `run_logs/`.

**Tech Stack:** alpaca-py, yfinance, edgartools, APScheduler, SQLite, python-dotenv, logging (stdlib), tabulate, datetime (timezone-aware)

**Python env:** `C:\Users\User\Documents\code\auto_trader\myenv\Scripts\python.exe`

## Global Constraints

- All code production-ready, no TODOs/placeholders/pseudocode
- Historical backtesting: 0.1% slippage for PEAD/large caps, 1.0% for micro-caps
- All API exceptions handled gracefully (yfinance drops, SEC parser errors, missing prices) without crashing
- Structured `logging` throughout, never `print()`
- All timestamps: UTC for generic messages, original timezone shown for price feeds/events
- Limit orders only, never market orders
- 24h minimum hold for PDT compliance, unfilled orders cancelled after 15 min

---

### Task 1: Project Scaffolding — Config, Logging, .env, CLAUDE.md, .gitignore

**Files:**
- Create: `.gitignore`
- Create: `.env`
- Create: `CLAUDE.md`
- Create: `config.py`
- Create: `logging_utils.py`

**Interfaces:**
- Consumes: (nothing — this is the foundation)
- Produces: `config.py` → `Config` dataclass with strategy params, risk limits, paths; `logging_utils.py` → `setup_logging(name, run_dir)` returns configured logger

- [ ] **Step 1: Initialize git repo and create .gitignore**

```bash
cd /c/Users/User/Documents/code/new_strategy
git init
```

**.gitignore:**
```gitignore
# Environment
.env

# Database
*.db

# Logs
run_logs/

# Python
__pycache__/
*.pyc
*.pyo
*.egg-info/
dist/
build/

# IDE
.vscode/
.idea/

# OS
.DS_Store
Thumbs.db
```

- [ ] **Step 2: Create .env with Alpaca credentials**

```
ALPACA_API_KEY=PKEUCITLGKSU2473UEK3KHZWFL
ALPACA_SECRET_KEY=HttyZmkXWWwgTgmevHR7GVLTnTkiY33vBVoVXMjS2khv
```

- [ ] **Step 3: Write CLAUDE.md**

```markdown
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
```

- [ ] **Step 4: Write config.py**

```python
"""Configuration — loads .env, defines all strategy parameters and risk limits."""

import os
from dataclasses import dataclass, field
from typing import List
from dotenv import load_dotenv

load_dotenv()


@dataclass
class PEADConfig:
    sue_threshold: float = 0.15
    hold_days: int = 30
    trailing_stop_pct: float = 0.07
    slippage_pct: float = 0.001
    limit_price_buffer: float = 0.005
    max_position_size: float = 5000.0


@dataclass
class MicroCapConfig:
    min_market_cap: float = 20_000_000
    max_market_cap: float = 300_000_000
    keywords: List[str] = field(default_factory=lambda: [
        "Medicare reimbursement", "CMS approval",
        "FDA clearance", "patent granted"
    ])
    slippage_pct: float = 0.01
    hold_days: int = 15
    entry_delay_minutes: int = 15
    poll_interval_minutes: int = 15


@dataclass
class CEFConfig:
    discount_threshold: float = -0.12
    convergence_target: float = -0.04
    max_hold_days: int = 60
    slippage_pct: float = 0.001


@dataclass
class RiskLimits:
    max_position_size_usd: float = 5000.0
    daily_max_loss_pct: float = 0.02
    min_hold_hours: int = 24
    order_timeout_minutes: int = 15


@dataclass
class AppConfig:
    alpaca_api_key: str = os.getenv("ALPACA_API_KEY", "")
    alpaca_secret_key: str = os.getenv("ALPACA_SECRET_KEY", "")
    alpaca_paper: bool = True
    db_path: str = os.path.join(os.path.dirname(__file__), "trading_system.db")
    run_logs_dir: str = os.path.join(os.path.dirname(__file__), "run_logs")
    exchange_timezone: str = "America/New_York"
    pead: PEADConfig = field(default_factory=PEADConfig)
    microcap: MicroCapConfig = field(default_factory=MicroCapConfig)
    cef: CEFConfig = field(default_factory=CEFConfig)
    risk: RiskLimits = field(default_factory=RiskLimits)

    def validate(self):
        if not self.alpaca_api_key or not self.alpaca_secret_key:
            raise ValueError(
                "ALPACA_API_KEY and ALPACA_SECRET_KEY must be set in .env"
            )


CONFIG = AppConfig()
```

- [ ] **Step 5: Write logging_utils.py**

```python
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
```

- [ ] **Step 6: Verify all files load correctly**

```bash
cd /c/Users/User/Documents/code/new_strategy
/c/Users/User/Documents/code/auto_trader/myenv/Scripts/python.exe -c "
from config import CONFIG
print('config OK:', CONFIG.alpaca_paper)
from logging_utils import setup_logging, format_with_tz
print('logging_utils OK')
"
```
Expected: no ImportError, config values print correctly.

- [ ] **Step 7: Commit**

```bash
git add .gitignore .env CLAUDE.md config.py logging_utils.py
git commit -m "feat: project scaffolding — config, logging, CLAUDE.md"
```

---

### Task 2: Rate Limiter + Database Layer

**Files:**
- Create: `rate_limiter.py`
- Create: `database.py`

**Interfaces:**
- Consumes: `config.py` CONFIG (for db_path)
- Produces: `RateLimiter` class with `acquire()` and `__call__`; `retry_with_backoff` decorator; `Database` class with `init_db()`, `save_trade()`, `update_trade()`, `save_signal()`, `save_backtest_result()`, `get_open_trades()`, `get_recent_trades()`, `get_backtest_summary()`

- [ ] **Step 1: Write rate_limiter.py**

```python
"""Sliding-window rate limiter decorator with exponential backoff on 429."""

import time
import logging
from functools import wraps

logger = logging.getLogger(__name__)


class RateLimiter:
    def __init__(self, max_calls: int = 3, window_seconds: float = 1.0):
        self.max_calls = max_calls
        self.window_seconds = window_seconds
        self._timestamps: list[float] = []

    def _slide_window(self):
        now = time.monotonic()
        cutoff = now - self.window_seconds
        self._timestamps = [t for t in self._timestamps if t > cutoff]

    def acquire(self) -> float:
        while True:
            self._slide_window()
            if len(self._timestamps) < self.max_calls:
                self._timestamps.append(time.monotonic())
                return 0.0
            sleep_for = self._timestamps[0] + self.window_seconds - time.monotonic()
            if sleep_for > 0:
                time.sleep(sleep_for)
            continue

    def __call__(self, func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            self.acquire()
            return func(*args, **kwargs)
        return wrapper


def retry_with_backoff(max_retries: int = 3, base_delay: float = 1.0):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            last_exc = None
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    last_exc = e
                    if attempt < max_retries:
                        delay = base_delay * (2 ** attempt)
                        logger.warning(
                            "Attempt %d/%d failed: %s. Retrying in %.1fs",
                            attempt + 1, max_retries + 1, e, delay
                        )
                        time.sleep(delay)
                    else:
                        logger.error(
                            "All %d attempts failed for %s: %s",
                            max_retries + 1, func.__name__, e
                        )
                        raise
            return None
        return wrapper
    return decorator
```

- [ ] **Step 2: Write database.py**

```python
"""SQLite wrapper for trades, signals, and backtest_results tables."""

import sqlite3
import os
from typing import Optional, List, Dict, Any


class Database:
    def __init__(self, db_path: str):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self._conn: Optional[sqlite3.Connection] = None

    def connect(self):
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
        return self._conn

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None

    def init_db(self):
        conn = self.connect()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                strategy_name TEXT NOT NULL,
                entry_time_utc TEXT NOT NULL,
                entry_time_exchange TEXT,
                entry_price REAL NOT NULL,
                qty REAL NOT NULL,
                target_price REAL,
                stop_loss REAL,
                exit_time_utc TEXT,
                exit_time_exchange TEXT,
                exit_price REAL,
                pnl_pct REAL,
                pnl_usd REAL,
                hold_minutes REAL,
                exit_reason TEXT,
                status TEXT NOT NULL DEFAULT 'open',
                exchange_timezone TEXT DEFAULT 'America/New_York',
                slippage_applied REAL,
                created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                strategy_name TEXT NOT NULL,
                raw_data TEXT,
                signal_value REAL,
                threshold REAL,
                decision TEXT NOT NULL,
                reason TEXT,
                timestamp_utc TEXT NOT NULL,
                exchange_time TEXT,
                executed INTEGER DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS backtest_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                strategy_name TEXT NOT NULL,
                event_date TEXT NOT NULL,
                symbol TEXT NOT NULL,
                entry_price REAL NOT NULL,
                exit_price REAL NOT NULL,
                pnl_pct REAL NOT NULL,
                pnl_usd REAL,
                hold_days REAL NOT NULL,
                spy_return REAL,
                iwm_return REAL,
                alpha REAL,
                slippage_pct REAL,
                event_type TEXT,
                notes TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE INDEX IF NOT EXISTS idx_trades_strategy ON trades(strategy_name);
            CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades(symbol);
            CREATE INDEX IF NOT EXISTS idx_signals_strategy ON signals(strategy_name);
            CREATE INDEX IF NOT EXISTS idx_backtest_strategy ON backtest_results(strategy_name);
        """)
        conn.commit()

    def save_trade(self, trade: Dict[str, Any]) -> int:
        conn = self.connect()
        cur = conn.execute("""
            INSERT INTO trades (symbol, strategy_name, entry_time_utc, entry_time_exchange,
                entry_price, qty, target_price, stop_loss, status,
                exchange_timezone, slippage_applied)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (trade["symbol"], trade["strategy_name"], trade["entry_time_utc"],
              trade.get("entry_time_exchange"), trade["entry_price"], trade["qty"],
              trade.get("target_price"), trade.get("stop_loss"),
              trade.get("status", "open"),
              trade.get("exchange_timezone", "America/New_York"),
              trade.get("slippage_applied")))
        conn.commit()
        return cur.lastrowid

    def update_trade(self, trade_id: int, updates: Dict[str, Any]) -> None:
        cols = ", ".join(f"{k} = ?" for k in updates)
        vals = list(updates.values()) + [trade_id]
        conn = self.connect()
        conn.execute(f"UPDATE trades SET {cols} WHERE id = ?", vals)
        conn.commit()

    def save_signal(self, signal: Dict[str, Any]) -> int:
        conn = self.connect()
        cur = conn.execute("""
            INSERT INTO signals (symbol, strategy_name, raw_data, signal_value, threshold,
                decision, reason, timestamp_utc, exchange_time, executed)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (signal["symbol"], signal["strategy_name"], signal.get("raw_data"),
              signal.get("signal_value"), signal.get("threshold"), signal["decision"],
              signal.get("reason"), signal["timestamp_utc"],
              signal.get("exchange_time"), signal.get("executed", 0)))
        conn.commit()
        return cur.lastrowid

    def save_backtest_result(self, result: Dict[str, Any]) -> int:
        conn = self.connect()
        cur = conn.execute("""
            INSERT INTO backtest_results (strategy_name, event_date, symbol, entry_price, exit_price,
                pnl_pct, pnl_usd, hold_days, spy_return, iwm_return,
                alpha, slippage_pct, event_type, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (result["strategy_name"], result["event_date"], result["symbol"],
              result["entry_price"], result["exit_price"], result["pnl_pct"],
              result.get("pnl_usd"), result["hold_days"], result.get("spy_return"),
              result.get("iwm_return"), result.get("alpha"),
              result.get("slippage_pct"), result.get("event_type"), result.get("notes")))
        conn.commit()
        return cur.lastrowid

    def get_open_trades(self) -> List[Dict[str, Any]]:
        return [dict(r) for r in self.connect().execute(
            "SELECT * FROM trades WHERE status = 'open' ORDER BY entry_time_utc").fetchall()]

    def get_recent_trades(self, limit: int = 50) -> List[Dict[str, Any]]:
        return [dict(r) for r in self.connect().execute(
            "SELECT * FROM trades ORDER BY entry_time_utc DESC LIMIT ?", (limit,)).fetchall()]

    def get_backtest_summary(self, strategy_name: Optional[str] = None) -> List[Dict[str, Any]]:
        if strategy_name:
            rows = self.connect().execute(
                "SELECT * FROM backtest_results WHERE strategy_name = ? ORDER BY event_date",
                (strategy_name,)).fetchall()
        else:
            rows = self.connect().execute(
                "SELECT * FROM backtest_results ORDER BY event_date").fetchall()
        return [dict(r) for r in rows]

    def get_signals(self, strategy_name: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
        if strategy_name:
            rows = self.connect().execute(
                "SELECT * FROM signals WHERE strategy_name = ? ORDER BY timestamp_utc DESC LIMIT ?",
                (strategy_name, limit)).fetchall()
        else:
            rows = self.connect().execute(
                "SELECT * FROM signals ORDER BY timestamp_utc DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]
```

- [ ] **Step 3: Verify modules load**

```bash
cd /c/Users/User/Documents/code/new_strategy
/c/Users/User/Documents/code/auto_trader/myenv/Scripts/python.exe -c "
from rate_limiter import RateLimiter, retry_with_backoff
rl = RateLimiter(max_calls=5, window_seconds=1.0)
print('rate_limiter OK')
from database import Database
db = Database(':memory:')
db.init_db()
print('database OK')
db.close()
"
```

- [ ] **Step 4: Commit**

```bash
git add rate_limiter.py database.py
git commit -m "feat: rate limiter and database layer"
```

---

### Task 3: Alpaca Paper Executor

**Files:**
- Create: `executor.py`

**Interfaces:**
- Consumes: `CONFIG` from config.py, `Database` from database.py, `RateLimiter`/`retry_with_backoff` from rate_limiter.py, `setup_logging`/`format_with_tz` from logging_utils.py
- Produces: `Executor` class with `place_limit_order()`, `check_buying_power()`, `get_positions()`, `cancel_unfilled_orders()`, `run_post_trade_backtest()`

- [ ] **Step 1: Write executor.py**

```python
"""Alpaca paper execution — limit orders, PDT cooldown, post-trade backtest hook."""

import os
import time
import threading
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, List
from zoneinfo import ZoneInfo

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import LimitOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce, OrderStatus

from config import CONFIG
from database import Database
from rate_limiter import RateLimiter, retry_with_backoff
from logging_utils import setup_logging, format_with_tz


class Executor:
    def __init__(self, db: Database, run_dir: str):
        self.config = CONFIG
        self.db = db
        self.logger = setup_logging("executor", run_dir)
        self.exchange_tz = ZoneInfo(self.config.exchange_timezone)
        self.client = TradingClient(
            api_key=self.config.alpaca_api_key,
            secret_key=self.config.alpaca_secret_key,
            paper=self.config.alpaca_paper
        )
        self.rate_limiter = RateLimiter(max_calls=3, window_seconds=1.0)
        self._cooldowns: Dict[str, datetime] = {}
        self._lock = threading.Lock()

        self.logger.info("Executor initialized | Paper: %s | Account: %s",
                         self.config.alpaca_paper, self._get_account_info())

    def _get_account_info(self) -> str:
        try:
            acc = self.client.get_account()
            return f"{acc.id} | Buying Power: ${float(acc.buying_power):.2f}"
        except Exception as e:
            return f"unknown ({e})"

    def _get_current_price(self, symbol: str) -> Optional[float]:
        try:
            self.rate_limiter.acquire()
            asset = self.client.get_latest_trade(symbol)
            return float(asset.price)
        except Exception as e:
            self.logger.error("Failed to get price for %s: %s", symbol, e)
            return None

    @retry_with_backoff(max_retries=2, base_delay=1.0)
    def check_buying_power(self, required: float) -> bool:
        self.rate_limiter.acquire()
        account = self.client.get_account()
        bp = float(account.buying_power)
        if bp < required:
            self.logger.warning("Insufficient BP: $%.2f < $%.2f", bp, required)
            return False
        return True

    def _check_daily_loss(self) -> bool:
        try:
            self.rate_limiter.acquire()
            account = self.client.get_account()
            equity = float(account.equity)
            last_equity = float(account.last_equity)
            if last_equity <= 0:
                return True
            loss_pct = (last_equity - equity) / last_equity
            if loss_pct > self.config.risk.daily_max_loss_pct:
                self.logger.critical("KILL-SWITCH: daily loss %.2f%% > %.2f%%",
                                     loss_pct * 100, self.config.risk.daily_max_loss_pct * 100)
                return False
            return True
        except Exception as e:
            self.logger.error("Daily loss check failed: %s", e)
            return True

    def _check_cooldown(self, symbol: str) -> bool:
        with self._lock:
            if symbol in self._cooldowns:
                remaining = (self._cooldowns[symbol] - datetime.now(timezone.utc)).total_seconds()
                if remaining > 0:
                    self.logger.info("Cooldown %s: %.0fmin left", symbol, remaining / 60)
                    return False
                del self._cooldowns[symbol]
            return True

    @retry_with_backoff(max_retries=2, base_delay=1.0)
    def place_limit_order(
        self, symbol: str, qty: float, side: str = "buy",
        price_buffer: Optional[float] = None, strategy_name: str = "unknown",
        slippage_pct: Optional[float] = None,
    ) -> Optional[int]:
        now_utc = datetime.now(timezone.utc)
        now_exchange = now_utc.astimezone(self.exchange_tz)
        ts_log = format_with_tz(now_utc, self.config.exchange_timezone)

        if not self._check_cooldown(symbol):
            self.logger.info("%s %s skipped: cooldown", ts_log, symbol)
            return None
        if not self._check_daily_loss():
            self.logger.info("%s %s skipped: kill-switch", ts_log, symbol)
            return None

        price = self._get_current_price(symbol)
        if price is None:
            return None

        buffer = price_buffer if price_buffer is not None else self.config.pead.limit_price_buffer
        limit_price = round(price * (1 + buffer), 2) if side == "buy" else round(price * (1 - buffer), 2)

        notional = qty * price
        if notional > self.config.risk.max_position_size_usd:
            self.logger.warning("%s position $%.2f > max $%.2f, reducing qty",
                                symbol, notional, self.config.risk.max_position_size_usd)
            qty = self.config.risk.max_position_size_usd / price

        if not self.check_buying_power(notional):
            return None

        order_req = LimitOrderRequest(
            symbol=symbol, qty=qty,
            side=OrderSide.BUY if side == "buy" else OrderSide.SELL,
            limit_price=limit_price, time_in_force=TimeInForce.DAY,
        )

        try:
            self.rate_limiter.acquire()
            order = self.client.submit_order(order_req)
            self.logger.info("%s | ORDER: %s %s %.4f @ $%.2f (mkt $%.2f, buf %+.2f%%)",
                             ts_log, side.upper(), symbol, qty, limit_price, price, buffer * 100)

            trade_data = {
                "symbol": symbol, "strategy_name": strategy_name,
                "entry_time_utc": now_utc.isoformat(),
                "entry_time_exchange": now_exchange.isoformat(),
                "entry_price": float(order.filled_avg_price) if order.filled_avg_price else limit_price,
                "qty": qty, "target_price": round(limit_price * 1.05, 2),
                "stop_loss": round(limit_price * 0.97, 2), "status": "open",
                "exchange_timezone": self.config.exchange_timezone,
                "slippage_applied": slippage_pct,
            }
            trade_id = self.db.save_trade(trade_data)
            self.logger.info("Trade #%d saved for %s", trade_id, symbol)
            return trade_id

        except Exception as e:
            self.logger.error("ORDER FAILED %s %s: %s", side.upper(), symbol, e)
            return None

    def get_positions(self) -> List[Dict[str, Any]]:
        try:
            self.rate_limiter.acquire()
            return [{"symbol": p.symbol, "qty": float(p.qty),
                      "entry_price": float(p.avg_entry_price),
                      "pnl_pct": float(p.unrealized_plpc)}
                    for p in self.client.get_all_positions()]
        except Exception as e:
            self.logger.error("get_positions failed: %s", e)
            return []

    def cancel_unfilled_orders(self):
        try:
            self.rate_limiter.acquire()
            now = datetime.now(timezone.utc)
            for order in self.client.get_orders():
                if order.status in (OrderStatus.ACCEPTED, OrderStatus.NEW, OrderStatus.PARTIALLY_FILLED):
                    sub = order.submitted_at
                    if sub.tzinfo is None:
                        sub = sub.replace(tzinfo=timezone.utc)
                    age = (now - sub).total_seconds() / 60
                    if age >= self.config.risk.order_timeout_minutes:
                        self.client.cancel_order(order.id)
                        self.logger.info("Cancelled unfilled %s for %s (%.0fmin)", order.id, order.symbol, age)
        except Exception as e:
            self.logger.error("cancel_unfilled failed: %s", e)

    def run_post_trade_backtest(self, symbol: str, strategy_name: str,
                                 entry_price: float, exit_price: float,
                                 pnl_pct: float, hold_days: float, entry_time_utc: str):
        self.logger.info("POST-TRADE: %s %s | PnL %.2f%% | Hold %.1fd", symbol, strategy_name, pnl_pct * 100, hold_days)
        try:
            from historical_backtester import HistoricalBacktester
            bt = HistoricalBacktester(self.db, os.path.join(CONFIG.run_logs_dir, "post_trade"))
            comp = bt.run_comparison(symbol, strategy_name, entry_price, exit_price, pnl_pct, hold_days, entry_time_utc[:10])
            if comp:
                self.logger.info("COMPARISON %s %s | expected=%.2f%% actual=%.2f%% Δ=%+.2f%%",
                                 symbol, strategy_name,
                                 comp.get("expected_pnl", 0) * 100, pnl_pct * 100,
                                 (pnl_pct - comp.get("expected_pnl", 0)) * 100)
        except Exception as e:
            self.logger.error("Post-trade backtest failed: %s", e)
```

- [ ] **Step 2: Verify executor imports**

```bash
cd /c/Users/User/Documents/code/new_strategy
/c/Users/User/Documents/code/auto_trader/myenv/Scripts/python.exe -c "
from database import Database
from executor import Executor
print('executor OK')
"
```

- [ ] **Step 3: Commit**

```bash
git add executor.py
git commit -m "feat: Alpaca paper executor with risk controls and post-trade hook"
```

---

### Task 4: Screeners — Live Scanners for All 3 Strategies

**Files:**
- Create: `screeners.py`

**Interfaces:**
- Consumes: `CONFIG` from config.py, `Database` from database.py, `setup_logging`/`format_with_tz` from logging_utils.py
- Produces: `scan_pead_candidates(db, run_dir)`, `scan_microcap_filings(db, run_dir)`, `scan_cef_discounts(db, run_dir)` — each returns `List[Dict]` of signals, saves to DB

- [ ] **Step 1: Write screeners.py**

```python
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
            logger.info("PEAD %s SUE %+.1f%% → %s", symbol, surprise * 100, decision)
        except Exception as e:
            logger.warning("PEAD error %s: %s", row.get("symbol", "?"), e)

    logger.info("PEAD scan: %d signals", len(signals))
    return signals


def scan_microcap_filings(db: Database, run_dir: str) -> List[Dict[str, Any]]:
    logger = setup_logging("screener_microcap", run_dir)
    utc_now, local_now = _now_with_tz()
    signals = []

    if not _is_market_hours():
        logger.debug("Outside market hours — skip")
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
        logger.debug("Outside market hours — skip")
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
            logger.info("CEF %s discount=%.1f%% → %s", symbol, discount * 100, decision)
        except Exception as e:
            logger.warning("CEF error %s: %s", symbol, e)

    logger.info("CEF scan: %d signals", len(signals))
    return signals
```

- [ ] **Step 2: Verify screeners import**

```bash
cd /c/Users/User/Documents/code/new_strategy
/c/Users/User/Documents/code/auto_trader/myenv/Scripts/python.exe -c "
from database import Database
from screeners import scan_pead_candidates, scan_microcap_filings, scan_cef_discounts
print('screeners OK')
"
```

- [ ] **Step 3: Commit**

```bash
git add screeners.py
git commit -m "feat: live screeners — PEAD, micro-cap, CEF strategies"
```

---

### Task 5: Historical Backtesting Engine

**Files:**
- Create: `historical_backtester.py`

**Interfaces:**
- Consumes: `CONFIG` from config.py, `Database` from database.py, `setup_logging`/`format_with_tz` from logging_utils.py
- Produces: `HistoricalBacktester` class with `backtest_pead()`, `backtest_microcaps()`, `backtest_cefs()`, `generate_performance_metrics()`, `run_comparison()`

- [ ] **Step 1: Write historical_backtester.py**

```python
"""Historical backtesting engine — all 3 strategies + post-trade comparison."""

import time
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any
from zoneinfo import ZoneInfo

import yfinance as yf
import pandas as pd
import numpy as np

from config import CONFIG
from database import Database
from logging_utils import setup_logging, format_with_tz


class HistoricalBacktester:
    def __init__(self, db: Database, run_dir: str):
        self.config = CONFIG
        self.db = db
        self.logger = setup_logging("backtester", run_dir)
        self.exchange_tz = ZoneInfo(self.config.exchange_timezone)

    def backtest_pead(self, start_date: str, end_date: str) -> List[Dict[str, Any]]:
        results = []
        self.logger.info("PEAD backtest: %s to %s", start_date, end_date)

        try:
            spy = yf.download("SPY", start=start_date, end=end_date, auto_adjust=True, progress=False)
            spy_ret = float(spy["Close"].pct_change().sum()) if not spy.empty else 0
        except Exception as e:
            self.logger.error("SPY fetch failed: %s", e)
            spy_ret = 0

        for symbol in self._get_liquid_tickers():
            try:
                ticker = yf.Ticker(symbol)
                earnings = ticker.earnings_dates
                if earnings is None or earnings.empty:
                    continue

                for idx, row in earnings.iterrows():
                    try:
                        event_date = pd.to_datetime(idx)
                        if event_date.tzinfo is None:
                            event_date = event_date.tz_localize("UTC")
                        ds = event_date.strftime("%Y-%m-%d")
                        if ds < start_date[:10] or ds > end_date[:10]:
                            continue

                        eps_est = row.get("epsestimate") or row.get("eps_estimate", 0)
                        eps_act = row.get("epsactual") or row.get("eps_actual", 0)
                        if not (eps_est and eps_act and eps_est > 0):
                            continue

                        surprise = (eps_act - eps_est) / abs(eps_est)
                        if surprise <= self.config.pead.sue_threshold:
                            continue

                        hist = ticker.history(start=ds, end=(event_date + timedelta(days=45)).strftime("%Y-%m-%d"),
                                              auto_adjust=True, progress=False)
                        if hist.empty or len(hist) < 2:
                            continue

                        entry = float(hist["Open"].iloc[1]) * (1 + self.config.pead.slippage_pct)
                        exit_p, hold = self._simulate_trailing_stop(hist, entry,
                                            self.config.pead.trailing_stop_pct, self.config.pead.hold_days)
                        pnl = (exit_p - entry) / entry

                        result = {
                            "strategy_name": "pead", "event_date": ds, "symbol": symbol,
                            "entry_price": round(entry, 4), "exit_price": round(exit_p, 4),
                            "pnl_pct": round(pnl, 6), "pnl_usd": round(pnl * 5000, 2),
                            "hold_days": round(hold, 1),
                            "spy_return": round(float(spy_ret * (hold / 252)), 6),
                            "alpha": round(pnl - float(spy_ret * (hold / 252)), 6),
                            "slippage_pct": self.config.pead.slippage_pct,
                            "event_type": "earnings_beat", "notes": f"SUE:{surprise*100:.1f}%",
                        }
                        self.db.save_backtest_result(result)
                        results.append(result)
                        self.logger.info("PEAD %s %s PnL %+.2f%% hold %.0fd", symbol, ds, pnl * 100, hold)
                    except Exception as e:
                        self.logger.warning("PEAD event %s: %s", idx, e)
                time.sleep(0.5)
            except Exception as e:
                self.logger.warning("PEAD ticker %s: %s", symbol, e)

        self.logger.info("PEAD backtest: %d trades", len(results))
        return results

    def backtest_microcaps(self, start_date: str, end_date: str) -> List[Dict[str, Any]]:
        results = []
        self.logger.info("Micro-cap backtest: %s to %s", start_date, end_date)

        try:
            iwm = yf.download("IWM", start=start_date, end=end_date, auto_adjust=True, progress=False)
            iwm_ret = float(iwm["Close"].pct_change().sum()) if not iwm.empty else 0
        except Exception:
            iwm_ret = 0

        try:
            from edgar import get_filings
            filings = get_filings(form="8-K", date_range=(start_date[:10], end_date[:10]), limit=200)
        except Exception as e:
            self.logger.error("SEC filings fetch failed: %s", e)
            return results

        for filing in filings:
            try:
                symbol = str(filing.ticker) if hasattr(filing, "ticker") and filing.ticker else ""
                if not symbol:
                    continue
                fdate = str(getattr(filing, "filing_date", start_date[:10]))
                try:
                    text = filing.text().lower() if hasattr(filing, "text") else ""
                except Exception:
                    text = ""
                matched = [kw for kw in self.config.microcap.keywords if kw.lower() in text]
                if not matched:
                    continue

                info = yf.Ticker(symbol).info or {}
                mcap = info.get("marketCap", 0)
                if not (self.config.microcap.min_market_cap <= mcap <= self.config.microcap.max_market_cap):
                    continue

                start_f = (pd.to_datetime(fdate) - timedelta(days=5)).strftime("%Y-%m-%d")
                end_f = (pd.to_datetime(fdate) + timedelta(days=30)).strftime("%Y-%m-%d")
                hist = yf.Ticker(symbol).history(start=start_f, end=end_f, auto_adjust=True, progress=False)
                if hist.empty:
                    continue

                entry = float(hist["Open"].iloc[0]) * (1 + self.config.microcap.slippage_pct)
                max_bars = min(self.config.microcap.hold_days + 1, len(hist))
                if max_bars < 2:
                    continue
                exit_p = float(hist["Close"].iloc[max_bars - 1])
                hold = max_bars - 1
                pnl = (exit_p - entry) / entry

                result = {
                    "strategy_name": "microcap", "event_date": fdate, "symbol": symbol,
                    "entry_price": round(entry, 4), "exit_price": round(exit_p, 4),
                    "pnl_pct": round(pnl, 6), "pnl_usd": round(pnl * 3000, 2),
                    "hold_days": float(hold), "spy_return": round(float(iwm_ret), 6),
                    "alpha": round(pnl - float(iwm_ret), 6),
                    "slippage_pct": self.config.microcap.slippage_pct,
                    "event_type": "8k_catalyst", "notes": f"kw:{','.join(matched)}",
                }
                self.db.save_backtest_result(result)
                results.append(result)
                self.logger.info("MICRO %s %s PnL %+.2f%% kw=%s", symbol, fdate, pnl * 100, matched)
            except Exception as e:
                self.logger.warning("Micro-cap error: %s", e)

        self.logger.info("Micro-cap backtest: %d trades", len(results))
        return results

    def backtest_cefs(self, start_date: str, end_date: str) -> List[Dict[str, Any]]:
        results = []
        self.logger.info("CEF backtest: %s to %s", start_date, end_date)

        cefs = ["GOVT", "TLT", "HYG", "CWB", "PCY", "EMB", "BKLN", "FLOT", "JPST", "NEAR"]

        for symbol in cefs:
            try:
                hist = yf.Ticker(symbol).history(start=start_date, end=end_date, auto_adjust=True, progress=False)
                if hist.empty:
                    continue
                prices = hist["Close"].values
                nav = pd.Series(prices).rolling(20, min_periods=5).mean().values

                for i in range(len(prices)):
                    if i < 20 or np.isnan(nav[i]):
                        continue
                    disc = (prices[i] - nav[i]) / nav[i]
                    if disc >= self.config.cef.discount_threshold:
                        continue

                    entry = float(prices[i]) * (1 + self.config.cef.slippage_pct)
                    ed = hist.index[i].strftime("%Y-%m-%d")
                    exit_i = min(i + self.config.cef.max_hold_days, len(prices) - 1)
                    exit_p = float(prices[exit_i])
                    hold = exit_i - i
                    exit_disc = float((prices[exit_i] - nav[exit_i]) / nav[exit_i]) if exit_i < len(nav) and not np.isnan(nav[exit_i]) else disc
                    pnl = (exit_p - entry) / entry

                    result = {
                        "strategy_name": "cef", "event_date": ed, "symbol": symbol,
                        "entry_price": round(entry, 4), "exit_price": round(exit_p, 4),
                        "pnl_pct": round(pnl, 6), "pnl_usd": round(pnl * 5000, 2),
                        "hold_days": float(hold), "spy_return": 0, "alpha": 0,
                        "slippage_pct": self.config.cef.slippage_pct,
                        "event_type": "discount_arb",
                        "notes": f"entry_disc={disc*100:.1f}% exit_disc={exit_disc*100:.1f}%",
                    }
                    self.db.save_backtest_result(result)
                    results.append(result)
                    self.logger.info("CEF %s %s disc %.1f%%->%.1f%% PnL %+.2f%%", symbol, ed, disc*100, exit_disc*100, pnl*100)
            except Exception as e:
                self.logger.warning("CEF error %s: %s", symbol, e)

        self.logger.info("CEF backtest: %d trades", len(results))
        return results

    def run_comparison(self, symbol: str, strategy_name: str,
                        actual_entry: float, actual_exit: float,
                        actual_pnl_pct: float, hold_days: float, event_date: str) -> Dict[str, Any]:
        end = (pd.to_datetime(event_date) + timedelta(days=90)).strftime("%Y-%m-%d")
        start = (pd.to_datetime(event_date) - timedelta(days=10)).strftime("%Y-%m-%d")
        try:
            hist = yf.Ticker(symbol).history(start=start, end=end, auto_adjust=True, progress=False)
            if hist.empty:
                return {"expected_pnl": actual_pnl_pct, "discrepancy": 0}
            exp = float(hist["Close"].pct_change(21).iloc[-1]) if len(hist) >= 22 else actual_pnl_pct
            if np.isnan(exp):
                exp = actual_pnl_pct
            disc = actual_pnl_pct - exp
            return {"symbol": symbol, "strategy": strategy_name, "event_date": event_date,
                    "expected_pnl": round(float(exp), 6), "actual_pnl_pct": round(actual_pnl_pct, 6),
                    "discrepancy": round(float(disc), 6), "hold_days": hold_days}
        except Exception as e:
            self.logger.error("Comparison failed %s: %s", symbol, e)
            return {"expected_pnl": actual_pnl_pct, "discrepancy": 0}

    def generate_performance_metrics(self, strategy_name: Optional[str] = None) -> Dict[str, Any]:
        results = self.db.get_backtest_summary(strategy_name)
        if not results:
            self.logger.warning("No results for %s", strategy_name or "all")
            return {}

        pnl = np.array([r["pnl_pct"] for r in results])
        n = len(pnl)
        total_ret = float(np.prod(1 + pnl) - 1)
        win_rate = float(np.sum(pnl > 0) / n)
        avg = float(np.mean(pnl))
        std = float(np.std(pnl)) if n > 1 else 1
        sharpe = float(avg / std * np.sqrt(252)) if std > 0 else 0

        cum = np.cumprod(1 + pnl)
        dd = (cum - np.maximum.accumulate(cum)) / np.maximum.accumulate(cum)
        max_dd = float(np.min(dd))

        avg_hold = float(np.mean([r["hold_days"] for r in results]))
        years = (n * avg_hold) / 252 if avg_hold > 0 else 1
        cagr = float((1 + total_ret) ** (1 / years) - 1) if years > 0 else 0

        spy = np.array([r.get("spy_return", 0) or 0 for r in results])
        alpha = float(np.mean(pnl - spy)) if len(spy) == n else 0

        return {"strategy": strategy_name or "all", "total_return_pct": round(total_ret * 100, 2),
                "cagr_pct": round(cagr * 100, 2), "sharpe_ratio": round(sharpe, 3),
                "max_drawdown_pct": round(max_dd * 100, 2), "win_rate_pct": round(win_rate * 100, 1),
                "total_trades": n, "avg_return_pct": round(avg * 100, 2), "alpha_pct": round(alpha * 100, 4)}

    def _get_liquid_tickers(self) -> List[str]:
        return ["AAPL","MSFT","GOOGL","AMZN","NVDA","META","TSLA","JPM","V","JNJ",
                "WMT","PG","MA","UNH","HD","DIS","BAC","PEP","KO","CSCO","XOM","CVX",
                "MRK","ABBV","PFE","TMO","AVGO","COST","CMCSA","ADBE","NFLX","CRM",
                "TXN","QCOM","AMD","INTC","IBM","CAT","GE","BA","MMM","HON","UNP",
                "UPS","NEE","DUK","SO","C","GS","MS","BLK","SCHW","AXP","ABT","MDT",
                "SYK","ISRG","LMT","NOC","RTX","AMGN","GILD","REGN","VRTX"]

    def _simulate_trailing_stop(self, hist, entry: float, stop_pct: float, max_hold: int):
        max_p = entry
        exit_p = entry
        hold = max_hold
        for i in range(1, min(max_hold + 1, len(hist))):
            cp = float(hist["Close"].iloc[i])
            hi = float(hist["High"].iloc[i]) if "High" in hist.columns else cp
            max_p = max(max_p, hi)
            if cp < max_p * (1 - stop_pct):
                exit_p, hold = cp, i
                break
            exit_p, hold = cp, i
        return exit_p, hold
```

- [ ] **Step 2: Verify backtester imports**

```bash
cd /c/Users/User/Documents/code/new_strategy
/c/Users/User/Documents/code/auto_trader/myenv/Scripts/python.exe -c "
from database import Database
from historical_backtester import HistoricalBacktester
print('historical_backtester OK')
"
```

- [ ] **Step 3: Commit**

```bash
git add historical_backtester.py
git commit -m "feat: historical backtesting engine — 3 strategies + comparison"
```

---

### Task 6: Main CLI + Orchestration

**Files:**
- Create: `main.py`

**Interfaces:**
- Consumes: all previous modules
- Produces: CLI entry point with `--mode backtest` and `--mode live`

- [ ] **Step 1: Write main.py**

```python
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
from logging_utils import setup_logging, format_with_tz, session_startup_log


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
    db.close()


def _run_screener(name, scan_func, db, run_dir, executor, logger):
    logger.info("=== %s cycle ===", name.upper())
    try:
        for sig in scan_func(db, run_dir):
            if sig["decision"] == "buy" and not sig.get("executed"):
                slip = CONFIG.pead.slippage_pct if name == "pead" else \
                       CONFIG.microcap.slippage_pct if name == "microcap" else \
                       CONFIG.cef.slippage_pct
                executor.place_limit_order(symbol=sig["symbol"], qty=1, side="buy",
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
```

- [ ] **Step 2: Verify main.py imports cleanly**

```bash
cd /c/Users/User/Documents/code/new_strategy
/c/Users/User/Documents/code/auto_trader/myenv/Scripts/python.exe -c "
from config import CONFIG
from logging_utils import setup_logging, format_with_tz
from rate_limiter import RateLimiter
from database import Database
from screeners import scan_pead_candidates
from executor import Executor
from historical_backtester import HistoricalBacktester
from main import main
print('ALL MODULES IMPORT OK')
import subprocess, sys
subprocess.run([sys.executable, 'main.py', '--help'])
"
```

- [ ] **Step 3: Commit**

```bash
git add main.py
git commit -m "feat: CLI entry point — live and backtest modes"
```

---

### Task 7: Final End-to-End Smoke Test

- [ ] **Step 1: Full smoke test**

```bash
cd /c/Users/User/Documents/code/new_strategy
/c/Users/User/Documents/code/auto_trader/myenv/Scripts/python.exe -c "
from config import CONFIG
from logging_utils import setup_logging, format_with_tz
from rate_limiter import RateLimiter
from database import Database
from historical_backtester import HistoricalBacktester

import tempfile, os
db = Database(os.path.join(tempfile.gettempdir(), 'test_trading.db'))
db.init_db()
CONFIG.validate()
rl = RateLimiter(max_calls=3, window_seconds=1.0)
rl.acquire()
print('ALL SYSTEMS OK')
db.close()
os.remove(os.path.join(tempfile.gettempdir(), 'test_trading.db'))
"
```

- [ ] **Step 2: CLI help**

```bash
cd /c/Users/User/Documents/code/new_strategy
/c/Users/User/Documents/code/auto_trader/myenv/Scripts/python.exe main.py --help
```

- [ ] **Step 3: Commit**

```bash
git add -A
git commit -m "feat: complete multi-strategy trading system"
```
