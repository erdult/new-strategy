"""Historical backtesting engine — all 3 strategies + post-trade comparison."""

import time
from datetime import timedelta
from typing import Optional, List, Dict, Any

import yfinance as yf
import pandas as pd
import numpy as np

from config import CONFIG
from database import Database
from logging_utils import setup_logging
from screeners import _check_activist_filing


class HistoricalBacktester:
    def __init__(self, db: Database, run_dir: str):
        self.config = CONFIG
        self.db = db
        self.logger = setup_logging("backtester", run_dir)

    def _fetch_prices(self, symbol: str, start: str, end: str, label: str = ""):
        """Fetch daily prices, checking DB cache before calling yfinance."""
        cached = self.db.get_cached_price_range(symbol, start, end)
        if cached is not None and not cached.empty:
            self.logger.debug("%s: using cached prices (%d rows)", symbol, len(cached))
            return cached
        self.logger.debug("%s: fetching prices %s to %s from yfinance%s", symbol, start, end, label)
        result = yf.download(symbol, start=start, end=end, auto_adjust=True, progress=False)
        if result is not None and not result.empty:
            # Flatten MultiIndex columns from yfinance multi-ticker format
            if isinstance(result.columns, pd.MultiIndex):
                try:
                    result = result.xs(symbol, axis=1, level=0)
                except (KeyError, ValueError):
                    try:
                        result = result.xs(symbol, axis=1, level=1)
                    except (KeyError, ValueError):
                        pass
            saved = self.db.save_prices(symbol, result)
            self.logger.debug("%s: cached %d price rows", symbol, saved)
        return result

    def backtest_pead(self, start_date: str, end_date: str) -> List[Dict[str, Any]]:
        results = []
        self.logger.info("PEAD backtest: %s to %s", start_date, end_date)

        self.logger.info("PEAD: fetching SPY benchmark %s to %s", start_date, end_date)
        try:
            spy = self._fetch_prices("SPY", start_date, end_date, " (benchmark)")
            spy_ret = float((1 + spy["Close"].pct_change()).prod().iloc[0] - 1) if spy is not None and not spy.empty else 0
            self.logger.info("PEAD: SPY benchmark return = %+.2f%%", spy_ret * 100)
        except Exception as e:
            self.logger.error("SPY fetch failed: %s", e)
            spy_ret = 0

        tickers = self._get_liquid_tickers()
        total = len(tickers)
        self.logger.info("PEAD: scanning %d liquid tickers for earnings events", total)

        for idx, symbol in enumerate(tickers, 1):
            self.logger.info("PEAD [%d/%d] %s: checking earnings...", idx, total, symbol)
            try:
                cached_earnings = self.db.get_cached_earnings(symbol)
                if cached_earnings is not None:
                    earnings = cached_earnings
                    self.logger.info("PEAD [%d/%d] %s: using cached earnings (%d events)", idx, total, symbol, len(earnings))
                else:
                    ticker = yf.Ticker(symbol)
                    earnings = ticker.earnings_dates
                    if earnings is not None and not earnings.empty:
                        self.db.save_earnings(symbol, earnings)
                        self.logger.info("PEAD [%d/%d] %s: fetched & cached %d earnings events", idx, total, symbol, len(earnings))
                    else:
                        self.logger.info("PEAD [%d/%d] %s: no earnings data available, skipping", idx, total, symbol)
                        continue

                if earnings is None or earnings.empty:
                    self.logger.info("PEAD [%d/%d] %s: no earnings events found", idx, total, symbol)
                    continue

                events_checked = 0
                events_matched = 0
                for eidx, row in earnings.iterrows():
                    try:
                        event_date = pd.to_datetime(eidx)
                        if event_date.tzinfo is None:
                            event_date = event_date.tz_localize("UTC")
                        ds = event_date.strftime("%Y-%m-%d")
                        if ds < start_date[:10] or ds > end_date[:10]:
                            continue
                        events_checked += 1

                        eps_est = row.get("epsestimate") or row.get("eps_estimate", 0)
                        eps_act = row.get("epsactual") or row.get("eps_actual", 0)
                        if not (eps_est and eps_act and eps_est > 0):
                            self.logger.debug("PEAD %s %s: incomplete EPS data est=%s act=%s", symbol, ds, eps_est, eps_act)
                            continue

                        surprise = (eps_act - eps_est) / abs(eps_est)
                        if surprise <= self.config.pead.sue_threshold:
                            self.logger.debug("PEAD %s %s: SUE %+.1f%% <= threshold %.0f%%, skip",
                                              symbol, ds, surprise * 100, self.config.pead.sue_threshold * 100)
                            continue

                        self.logger.info("PEAD [%d/%d] %s %s: SUE %+.1f%% > threshold %.0f%%, simulating trade...",
                                         idx, total, symbol, ds, surprise * 100, self.config.pead.sue_threshold * 100)
                        events_matched += 1

                        hist = self._fetch_prices(
                            symbol, ds,
                            (event_date + timedelta(days=45)).strftime("%Y-%m-%d"),
                            " (trade window)"
                        )
                        if hist is None or hist.empty or len(hist) < 2:
                            self.logger.debug("PEAD %s %s: insufficient price data after earnings", symbol, ds)
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
                        self.logger.warning("PEAD event %s: %s", eidx, e)

                self.logger.info("PEAD [%d/%d] %s: %d events in range, %d above threshold -> %d trades",
                                 idx, total, symbol, events_checked, events_matched, sum(1 for r in results if r["symbol"] == symbol))
                time.sleep(0.5)
            except Exception as e:
                self.logger.warning("PEAD ticker %s: %s", symbol, e)

        self.logger.info("PEAD backtest complete: %d trades from %d tickers", len(results), total)
        return results

    def backtest_microcaps(self, start_date: str, end_date: str) -> List[Dict[str, Any]]:
        results = []
        self.logger.info("Micro-cap backtest: %s to %s", start_date, end_date)

        try:
            from edgar import get_filings
            self.logger.info("MICRO: fetching 8-K filings %s to %s", start_date, end_date)
            # filing_date uses colon-separated range string; no limit param, so we break at 200
            filings = get_filings(form="8-K", filing_date=f"{start_date[:10]}:{end_date[:10]}")
        except Exception as e:
            self.logger.error("SEC filings fetch failed: %s", e)
            return results

        processed = 0
        total_filings = 0
        for filing in filings:
            total_filings += 1

        self.logger.info("MICRO: %d total 8-K filings retrieved, scanning for keywords", total_filings)

        # Re-fetch since we consumed the iterator counting
        try:
            from edgar import get_filings
            filings = get_filings(form="8-K", filing_date=f"{start_date[:10]}:{end_date[:10]}")
        except Exception as e:
            self.logger.error("SEC filings re-fetch failed: %s", e)
            return results

        for fcount, filing in enumerate(filings, 1):
            if processed >= 200:
                self.logger.info("MICRO: reached processing limit of 200 filings")
                break
            if fcount % 25 == 0 or fcount == 1:
                self.logger.info("MICRO: processing filing %d/%d", fcount, total_filings)
            try:
                try:
                    entity = filing.get_entity()
                    symbol = str(entity.tickers[0]) if entity.tickers else ""
                except Exception:
                    symbol = ""
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

                self.logger.info("MICRO [%d/%d] %s: matched keywords %s on %s, checking mcap...",
                                 fcount, total_filings, symbol, matched, fdate)
                info = yf.Ticker(symbol).info or {}
                mcap = info.get("marketCap", 0)
                if not (self.config.microcap.min_market_cap <= mcap <= self.config.microcap.max_market_cap):
                    self.logger.info("MICRO %s: mcap $%.0f outside range [$%.0f, $%.0f], skip",
                                     symbol, mcap, self.config.microcap.min_market_cap, self.config.microcap.max_market_cap)
                    continue

                start_f = (pd.to_datetime(fdate) - timedelta(days=5)).strftime("%Y-%m-%d")
                end_f = (pd.to_datetime(fdate) + timedelta(days=30)).strftime("%Y-%m-%d")
                hist = self._fetch_prices(symbol, start_f, end_f, " (trade window)")
                if hist is None or hist.empty:
                    self.logger.info("MICRO %s: no price data %s to %s, skip", symbol, start_f, end_f)
                    continue

                entry = float(hist["Open"].iloc[0]) * (1 + self.config.microcap.slippage_pct)
                max_bars = min(self.config.microcap.hold_days + 1, len(hist))
                if max_bars < 2:
                    continue
                exit_p = float(hist["Close"].iloc[max_bars - 1])
                hold = max_bars - 1
                pnl = (exit_p - entry) / entry

                # Compute period-matched IWM return for this trade
                trade_iwm_ret = 0
                try:
                    iwm_hist = self._fetch_prices("IWM", start_f, end_f, " (benchmark)")
                    if iwm_hist is not None and not iwm_hist.empty:
                        trade_iwm_ret = float((1 + iwm_hist["Close"].pct_change()).prod().iloc[0] - 1)
                except Exception:
                    pass

                result = {
                    "strategy_name": "microcap", "event_date": fdate, "symbol": symbol,
                    "entry_price": round(entry, 4), "exit_price": round(exit_p, 4),
                    "pnl_pct": round(pnl, 6), "pnl_usd": round(pnl * 3000, 2),
                    "hold_days": float(hold), "iwm_return": round(float(trade_iwm_ret), 6),
                    "alpha": round(pnl - float(trade_iwm_ret), 6),
                    "slippage_pct": self.config.microcap.slippage_pct,
                    "event_type": "8k_catalyst", "notes": f"kw:{','.join(matched)}",
                }
                self.db.save_backtest_result(result)
                results.append(result)
                self.logger.info("MICRO %s %s PnL %+.2f%% hold=%.0fd kw=%s", symbol, fdate, pnl * 100, hold, matched)
                processed += 1
            except Exception as e:
                self.logger.warning("Micro-cap error: %s", e)

        self.logger.info("Micro-cap backtest complete: %d trades from %d filings processed", len(results), total_filings)
        return results

    def backtest_cefs(self, start_date: str, end_date: str) -> List[Dict[str, Any]]:
        results = []
        self.logger.info("CEF backtest: %s to %s", start_date, end_date)

        cefs = ["BGR", "BST", "CEM", "DSL", "ETV", "EVT", "FFC", "FRA", "GDV", "HQH", "HQL",
                 "HTD", "JPC", "JQC", "MCI", "MMT", "NAD", "NIE", "PDI", "PHK", "PML", "PTY",
                 "QQQX", "RIV", "RVT", "USA", "UTF", "UTG"]
        total = len(cefs)
        self.logger.info("CEF: scanning %d tickers for discount opportunities", total)

        for idx, symbol in enumerate(cefs, 1):
            self.logger.info("CEF [%d/%d] %s: fetching price history...", idx, total, symbol)
            try:
                hist = self._fetch_prices(symbol, start_date, end_date, " (price history)")
                if hist is None or hist.empty:
                    self.logger.info("CEF [%d/%d] %s: no price data available", idx, total, symbol)
                    continue
                prices = hist["Close"].values

                # Try to fetch actual historical NAV data from yfinance
                try:
                    self.logger.debug("CEF %s: attempting NAV data download...", symbol)
                    nav_hist = yf.download(symbol, start=start_date, end=end_date, auto_adjust=False, progress=False)
                    if nav_hist is not None and not nav_hist.empty:
                        if isinstance(nav_hist.columns, pd.MultiIndex):
                            nav_hist = nav_hist.xs(symbol, axis=1, level=0)
                        if "Nav" in nav_hist.columns:
                            nav = nav_hist["Nav"].values
                            self.logger.info("CEF [%d/%d] %s: using yfinance NAV data (%d points)", idx, total, symbol, len(nav))
                        else:
                            raise ValueError("No Nav column")
                    else:
                        raise ValueError("Empty NAV data")
                except Exception:
                    # Fallback: 50-day SMA as NAV proxy
                    nav = pd.Series(prices).rolling(50, min_periods=20).mean().values
                    self.logger.debug("CEF [%d/%d] %s: NAV unavailable, using 50-day SMA proxy", idx, total, symbol)

                min_window = 50 if isinstance(nav, np.ndarray) and len(nav) == len(prices) else 20
                trades_found = 0

                for i in range(len(prices)):
                    if i < min_window or np.isnan(nav[i]):
                        continue
                    disc = (prices[i] - nav[i]) / nav[i]
                    if disc >= self.config.cef.discount_threshold:
                        continue

                    trades_found += 1
                    entry = float(prices[i]) * (1 + self.config.cef.slippage_pct)
                    ed = hist.index[i].strftime("%Y-%m-%d")
                    exit_i = min(i + self.config.cef.max_hold_days, len(prices) - 1)
                    # Check for early exit on convergence to target discount
                    for j in range(i + 1, exit_i + 1):
                        if j >= len(nav) or np.isnan(nav[j]):
                            break
                        disc_j = (prices[j] - nav[j]) / nav[j]
                        if disc_j >= self.config.cef.convergence_target:
                            exit_i = j
                            break
                    exit_p = float(prices[exit_i])
                    hold = exit_i - i
                    exit_disc = float((prices[exit_i] - nav[exit_i]) / nav[exit_i]) if exit_i < len(nav) and not np.isnan(nav[exit_i]) else disc
                    pnl = (exit_p - entry) / entry

                    activist = _check_activist_filing(symbol)
                    notes = f"entry_disc={disc*100:.1f}% exit_disc={exit_disc*100:.1f}%"
                    if activist:
                        notes += f" | activist: {','.join(activist)}"

                    result = {
                        "strategy_name": "cef", "event_date": ed, "symbol": symbol,
                        "entry_price": round(entry, 4), "exit_price": round(exit_p, 4),
                        "pnl_pct": round(pnl, 6), "pnl_usd": round(pnl * 5000, 2),
                        "hold_days": float(hold), "spy_return": 0, "alpha": 0,
                        "slippage_pct": self.config.cef.slippage_pct,
                        "event_type": "discount_arb",
                        "notes": notes,
                    }
                    self.db.save_backtest_result(result)
                    results.append(result)
                    self.logger.info("CEF %s %s disc %.1f%%->%.1f%% PnL %+.2f%% hold=%.0fd", symbol, ed, disc*100, exit_disc*100, pnl*100, hold)

                self.logger.info("CEF [%d/%d] %s: %d discount events found -> %d trades",
                                 idx, total, symbol, trades_found, sum(1 for r in results if r["symbol"] == symbol))
            except Exception as e:
                self.logger.warning("CEF error %s: %s", symbol, e)

        self.logger.info("CEF backtest complete: %d trades from %d tickers", len(results), total)
        return results

    def run_comparison(self, symbol: str, strategy_name: str,
                        actual_entry: float, actual_exit: float,
                        actual_pnl_pct: float, hold_days: float, event_date: str) -> Dict[str, Any]:
        end = (pd.to_datetime(event_date) + timedelta(days=int(hold_days) + 5)).strftime("%Y-%m-%d")
        start = (pd.to_datetime(event_date) - timedelta(days=1)).strftime("%Y-%m-%d")
        try:
            hist = self._fetch_prices(symbol, start, end, " (comparison)")
            if hist is None or hist.empty or len(hist) < 2:
                return {"expected_pnl": actual_pnl_pct, "discrepancy": 0}
            # Entry: first available open price after event_date
            entry_price = float(hist["Open"].iloc[0])
            # Exit: close at index matching hold_days (or last available)
            exit_idx = min(int(hold_days), len(hist) - 1)
            exit_price = float(hist["Close"].iloc[exit_idx])
            exp_pnl = (exit_price - entry_price) / entry_price
            if np.isnan(exp_pnl):
                exp_pnl = actual_pnl_pct
            disc = actual_pnl_pct - exp_pnl
            return {"symbol": symbol, "strategy": strategy_name, "event_date": event_date,
                    "expected_pnl": round(float(exp_pnl), 6), "actual_pnl_pct": round(actual_pnl_pct, 6),
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
