"""Alpaca paper execution — limit orders, PDT cooldown, post-trade backtest hook."""

import os
import threading
from datetime import datetime, timezone
from datetime import timedelta
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

    def get_current_price(self, symbol: str) -> Optional[float]:
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
            self.logger.critical("KILL-SWITCH: daily loss API call failed: %s", e)
            return False

    def _check_cooldown(self, symbol: str) -> bool:
        with self._lock:
            if symbol in self._cooldowns:
                remaining = (self._cooldowns[symbol] - datetime.now(timezone.utc)).total_seconds()
                if remaining > 0:
                    self.logger.info("Cooldown %s: %.0fmin left", symbol, remaining / 60)
                    return False
                del self._cooldowns[symbol]
            return True

    def record_cooldown(self, symbol: str) -> None:
        with self._lock:
            self._cooldowns[symbol] = datetime.now(timezone.utc) + timedelta(hours=self.config.risk.min_hold_hours)
        self.logger.info("Cooldown set for %s: %.1f hours", symbol, self.config.risk.min_hold_hours)

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

        price = self.get_current_price(symbol)
        if price is None:
            return None

        buffer = price_buffer if price_buffer is not None else self.config.pead.limit_price_buffer
        limit_price = round(price * (1 + buffer), 2) if side == "buy" else round(price * (1 - buffer), 2)

        notional = qty * price
        if notional > self.config.risk.max_position_size_usd:
            self.logger.warning("%s position $%.2f > max $%.2f, reducing qty",
                                symbol, notional, self.config.risk.max_position_size_usd)
            qty = self.config.risk.max_position_size_usd / price
            notional = qty * price

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
            self.record_cooldown(symbol)
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

    def check_closed_positions(self):
        """Poll Alpaca for closed positions and run post-trade backtest for any closed trades."""
        try:
            current_positions = self.get_positions()
            current_symbols = {p["symbol"] for p in current_positions}
            open_trades = self.db.get_open_trades()

            for trade in open_trades:
                symbol = trade["symbol"]
                if symbol not in current_symbols:
                    self.logger.info("CLOSED: %s no longer in Alpaca positions, running post-trade backtest", symbol)
                    try:
                        trade_id = trade["id"]
                        entry_price = trade.get("entry_price", 0)
                        strategy_name = trade.get("strategy_name", "unknown")
                        exit_price = trade.get("exit_price", 0)
                        pnl_pct = trade.get("pnl_pct", 0)
                        hold_minutes = trade.get("hold_minutes", 0)
                        hold_days = hold_minutes / (60 * 24) if hold_minutes else 1.0
                        entry_time_utc = trade.get("entry_time_utc", "")

                        self.run_post_trade_backtest(
                            symbol=symbol, strategy_name=strategy_name,
                            entry_price=entry_price, exit_price=exit_price,
                            pnl_pct=pnl_pct, hold_days=hold_days,
                            entry_time_utc=entry_time_utc
                        )

                        self.db.update_trade(trade_id, {"status": "closed"})
                        self.logger.info("Trade #%d %s marked as closed", trade_id, symbol)
                    except Exception as e:
                        self.logger.error("Failed to process closed trade %s: %s", symbol, e)
        except Exception as e:
            self.logger.error("check_closed_positions failed: %s", e)

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

    def check_and_exit_positions(self):
        """Monitor open positions and exit all found positions."""
        try:
            self.rate_limiter.acquire()
            positions = self.client.get_all_positions()
            if not positions:
                return
            now = datetime.now(timezone.utc)
            for p in positions:
                symbol = p.symbol
                qty = abs(float(p.qty))
                if qty <= 0:
                    continue
                price = self.get_current_price(symbol)
                if not price or price <= 0:
                    continue
                self.logger.info("EXITING %s qty=%.2f entry=$%.2f curr=$%.2f PnL=%+.2f%%",
                                symbol, qty, float(p.avg_entry_price), price,
                                float(p.unrealized_plpc) * 100)
                self.rate_limiter.acquire()
                limit_price = round(price * 0.995, 2)
                order_req = LimitOrderRequest(
                    symbol=symbol, qty=qty,
                    side=OrderSide.SELL,
                    limit_price=limit_price,
                    time_in_force=TimeInForce.DAY,
                )
                self.client.submit_order(order_req)
        except Exception as e:
            self.logger.error("Position exit check failed: %s", e, exc_info=True)
