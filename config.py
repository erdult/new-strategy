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
