"""SQLite wrapper for trades, signals, backtest_results, and API data cache."""

import sqlite3
import os
from typing import Optional, List, Dict, Any, Tuple


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
            CREATE TABLE IF NOT EXISTS price_cache (
                symbol TEXT NOT NULL,
                trade_date TEXT NOT NULL,
                open REAL,
                high REAL,
                low REAL,
                close REAL,
                volume REAL,
                source TEXT DEFAULT 'yfinance',
                cached_at TEXT DEFAULT (datetime('now')),
                PRIMARY KEY (symbol, trade_date)
            );
            CREATE TABLE IF NOT EXISTS earnings_cache (
                symbol TEXT NOT NULL,
                event_date TEXT NOT NULL,
                eps_estimate REAL,
                eps_actual REAL,
                cached_at TEXT DEFAULT (datetime('now')),
                PRIMARY KEY (symbol, event_date)
            );
            CREATE INDEX IF NOT EXISTS idx_trades_strategy ON trades(strategy_name);
            CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades(symbol);
            CREATE INDEX IF NOT EXISTS idx_signals_strategy ON signals(strategy_name);
            CREATE INDEX IF NOT EXISTS idx_backtest_strategy ON backtest_results(strategy_name);
            CREATE INDEX IF NOT EXISTS idx_price_cache_symbol ON price_cache(symbol);
            CREATE INDEX IF NOT EXISTS idx_earnings_cache_symbol ON earnings_cache(symbol);
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
        ALLOWED_COLS = {"symbol", "strategy_name", "entry_price", "exit_price", "qty",
                        "target_price", "stop_loss", "pnl_pct", "pnl_usd", "status",
                        "exit_time_utc", "exit_time_exchange", "exit_reason",
                        "hold_minutes", "slippage_applied"}
        invalid = set(updates) - ALLOWED_COLS
        if invalid:
            raise ValueError(f"Invalid columns for update: {invalid}")
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

    def get_signals_by_symbol_strategy_date(self, symbol: str, strategy_name: str, date: str) -> List[Dict[str, Any]]:
        rows = self.connect().execute(
            "SELECT * FROM signals WHERE symbol = ? AND strategy_name = ? AND timestamp_utc LIKE ? AND decision = 'buy'",
            (symbol, strategy_name, f"{date}%")).fetchall()
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

    # ── API Data Cache ──────────────────────────────────────────

    def get_cached_price_range(self, symbol: str, start_date: str, end_date: str):
        """Return cached daily prices as DataFrame for symbol within date range, or None."""
        try:
            import pandas as pd
        except ImportError:
            return None
        rows = self.connect().execute(
            "SELECT trade_date, open, high, low, close, volume FROM price_cache "
            "WHERE symbol = ? AND trade_date >= ? AND trade_date <= ? ORDER BY trade_date",
            (symbol, start_date[:10], end_date[:10])
        ).fetchall()
        if not rows:
            return None
        df = pd.DataFrame([dict(r) for r in rows])
        if df.empty:
            return None
        df["trade_date"] = pd.to_datetime(df["trade_date"])
        df = df.set_index("trade_date")
        # Rename columns to match yfinance uppercase convention
        df = df.rename(columns={
            "open": "Open", "high": "High", "low": "Low",
            "close": "Close", "volume": "Volume"
        })
        return df

    def save_prices(self, symbol: str, df) -> int:
        """Store OHLCV DataFrame rows into price_cache. Returns count inserted."""
        if df is None or df.empty:
            return 0
        # Flatten MultiIndex columns (yfinance multi-ticker download format)
        import pandas as pd
        if isinstance(df.columns, pd.MultiIndex):
            try:
                df = df.xs(symbol, axis=1, level=0)
            except (KeyError, ValueError):
                try:
                    df = df.xs(symbol, axis=1, level=1)
                except (KeyError, ValueError):
                    return 0
        conn = self.connect()
        count = 0
        for idx, row in df.iterrows():
            try:
                def _scalar(v):
                    if v is None:
                        return None
                    return float(v.item()) if hasattr(v, "item") else float(v)
                conn.execute(
                    "INSERT OR REPLACE INTO price_cache (symbol, trade_date, open, high, low, close, volume) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (symbol, pd.to_datetime(idx).strftime("%Y-%m-%d"),
                     _scalar(row.get("Open")), _scalar(row.get("High")),
                     _scalar(row.get("Low")), _scalar(row.get("Close")),
                     _scalar(row.get("Volume")))
                )
                count += 1
            except Exception:
                continue
        conn.commit()
        return count

    def get_cached_earnings(self, symbol: str):
        """Return cached earnings dates as DataFrame for a symbol, or None."""
        try:
            import pandas as pd
        except ImportError:
            return None
        rows = self.connect().execute(
            "SELECT event_date, eps_estimate, eps_actual FROM earnings_cache "
            "WHERE symbol = ? ORDER BY event_date", (symbol,)
        ).fetchall()
        if not rows:
            return None
        data = []
        for r in rows:
            data.append({"epsestimate": r["eps_estimate"], "epsactual": r["eps_actual"]})
        df = pd.DataFrame(data, index=pd.to_datetime([r["event_date"] for r in rows]))
        return df if not df.empty else None

    def save_earnings(self, symbol: str, df) -> int:
        """Store earnings DataFrame rows into cache. Returns count."""
        if df is None or df.empty:
            return 0
        import pandas as pd
        conn = self.connect()
        count = 0
        for idx, row in df.iterrows():
            try:
                event_date = pd.to_datetime(idx).strftime("%Y-%m-%d")
                eps_est = row.get("epsestimate") or row.get("eps_estimate", 0)
                eps_act = row.get("epsactual") or row.get("eps_actual", 0)
                conn.execute(
                    "INSERT OR REPLACE INTO earnings_cache (symbol, event_date, eps_estimate, eps_actual) "
                    "VALUES (?, ?, ?, ?)",
                    (symbol, event_date,
                     float(eps_est) if eps_est else None,
                     float(eps_act) if eps_act else None)
                )
                count += 1
            except Exception:
                continue
        conn.commit()
        return count
