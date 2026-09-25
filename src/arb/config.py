from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# Loads .env into the process environment the first time this module is
# imported (no-op if the file doesn't exist, e.g. in CI). Explicit
# os.environ values always win -- load_dotenv() never overrides a var
# that's already set.
load_dotenv()


def _env(name: str, default: str | None = None, required: bool = False) -> str | None:
    val = os.environ.get(name, default)
    if required and not val:
        raise RuntimeError(f"missing required env var: {name}")
    return val


@dataclass(frozen=True)
class KalshiConfig:
    api_base: str
    ws_base: str
    api_key_id: str | None       # required only for private endpoints
    private_key_path: str | None  # path to PEM RSA private key


@dataclass(frozen=True)
class PolymarketConfig:
    gamma_base: str
    clob_base: str
    api_key: str | None
    api_secret: str | None
    api_passphrase: str | None


@dataclass(frozen=True)
class DbConfig:
    sqlite_path: str


@dataclass(frozen=True)
class ArbitrageEngineConfig:
    """Config for arb.arbitrage: LLM-assisted rule extraction/world-state
    proposal, plus the guardrails that gate a TRUE ARBITRAGE label."""
    anthropic_api_key: str | None
    anthropic_model: str
    min_rule_confidence: float
    capital_limit_cents: int


@dataclass(frozen=True)
class Settings:
    kalshi: KalshiConfig
    polymarket: PolymarketConfig
    db: DbConfig
    arbitrage: ArbitrageEngineConfig
    poll_interval_seconds: int


def load_settings() -> Settings:
    """Load config from environment. Secrets are never hardcoded or logged."""
    return Settings(
        # Base URLs verified live against docs.kalshi.com / docs.polymarket.com
        # on 2026-09-20 -- see docs/api_notes.md for sources and the items
        # still flagged UNVERIFIED (notably Kalshi's fee schedule).
        kalshi=KalshiConfig(
            api_base=_env("KALSHI_API_BASE", "https://external-api.kalshi.com/trade-api/v2"),
            ws_base=_env("KALSHI_WS_BASE", "wss://external-api-ws.kalshi.com/trade-api/ws/v2"),
            api_key_id=_env("KALSHI_API_KEY_ID"),
            private_key_path=_env("KALSHI_PRIVATE_KEY_PATH"),
        ),
        polymarket=PolymarketConfig(
            gamma_base=_env("POLYMARKET_GAMMA_BASE", "https://gamma-api.polymarket.com"),
            clob_base=_env("POLYMARKET_CLOB_BASE", "https://clob.polymarket.com"),
            api_key=_env("POLYMARKET_API_KEY"),
            api_secret=_env("POLYMARKET_API_SECRET"),
            api_passphrase=_env("POLYMARKET_API_PASSPHRASE"),
        ),
        db=DbConfig(
            sqlite_path=_env("ARB_SQLITE_PATH", str(Path(__file__).resolve().parents[2] / "data" / "arb.sqlite3")),
        ),
        arbitrage=ArbitrageEngineConfig(
            anthropic_api_key=_env("ANTHROPIC_API_KEY"),
            anthropic_model=_env("ANTHROPIC_MODEL", "claude-sonnet-5"),
            min_rule_confidence=float(_env("ARB_MIN_RULE_CONFIDENCE", "0.9")),
            capital_limit_cents=int(_env("ARB_CAPITAL_LIMIT_CENTS", "1000000")),  # $10,000 default
        ),
        poll_interval_seconds=int(_env("ARB_POLL_INTERVAL_SECONDS", "45")),
    )
