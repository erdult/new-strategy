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
